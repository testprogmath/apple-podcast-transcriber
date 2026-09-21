import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import URL
from test_pipeline_bot import update

from podcast_bot.bot import BotHandlers
from podcast_bot.models import UserError
from podcast_bot.mp3 import MP3, prepare_mp3, source_url, youtube_source
from podcast_bot.transcription.audio import run

YT = "https://www.youtube.com/watch?v=abcdefghijk"


@pytest.mark.parametrize(
    "url",
    [
        YT,
        "https://youtu.be/abcdefghijk?t=12",
        "https://m.youtube.com/shorts/abcdefghijk",
        "https://youtube.com/watch?v=abcdefghijk&list=playlist",
    ],
)
def test_youtube_normalizes_single_video(url):
    assert source_url(url) == ("youtube", YT)


def test_apple_episode():
    assert source_url(URL) == ("podcast", URL)


@pytest.mark.parametrize(
    "url",
    [
        "http://youtu.be/abcdefghijk",
        "https://youtube.com.evil/watch?v=abcdefghijk",
        "https://user:secret@youtube.com/watch?v=abcdefghijk",
        "https://youtube.com/playlist?list=123",
        "https://youtu.be/short",
        "https://youtube.com:444/watch?v=abcdefghijk",
        "file:///tmp/audio",
        "https://127.0.0.1/audio",
        "https://podcasts.apple.com/us/podcast/id123",
    ],
)
def test_invalid_sources_rejected(url):
    with pytest.raises(UserError):
        source_url(url)


async def test_youtube_extracts_only_direct_audio_without_downloading(monkeypatch):
    execute = AsyncMock(
        return_value=(
            json.dumps(
                {
                    "title": "演讲",
                    "uploader": "Teacher",
                    "url": "https://audio.example/a",
                    "duration": 60,
                    "vcodec": "none",
                    "protocol": "https",
                }
            ),
            "",
        )
    )
    monkeypatch.setattr("podcast_bot.mp3.run", execute)
    assert await youtube_source(YT, 180) == ("https://audio.example/a", "演讲", "Teacher")
    args = execute.call_args.args
    assert "bestaudio[protocol=https]" in args
    assert "--skip-download" in args and "--ignore-config" in args
    assert "--no-playlist" in args and args[-1] == YT
    assert execute.call_args.kwargs["timeout"] == 120


@pytest.mark.parametrize(
    "extra",
    [
        {"live_status": "is_live"},
        {"_type": "playlist"},
        {"duration": 99999},
        {"duration": None},
        {"vcodec": "h264"},
        {"protocol": "m3u8_native"},
    ],
)
async def test_youtube_rejects_live_video_formats_and_limits(monkeypatch, extra):
    data = {"duration": 60, "vcodec": "none", "protocol": "https", **extra}
    monkeypatch.setattr("podcast_bot.mp3.run", AsyncMock(return_value=(json.dumps(data), "")))
    with pytest.raises(UserError):
        await youtube_source(YT, 180)


@pytest.fixture
def fake_source(monkeypatch):
    monkeypatch.setattr(
        "podcast_bot.mp3.resolve",
        AsyncMock(
            return_value=SimpleNamespace(
                audio_url="https://audio.example/file",
                title="original",
                podcast="Teacher",
                duration=2,
            )
        ),
    )

    async def download(client, url, directory, limit):
        target = directory / "original.wav"
        await run(
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            str(target),
        )
        return target

    monkeypatch.setattr("podcast_bot.mp3.download", download)


async def test_real_local_conversion_metadata_and_cleanup(config, fake_source):
    progress = AsyncMock()
    async with prepare_mp3(URL, config, progress) as result:
        assert result.path.exists() and result.path.suffix == ".mp3"
        assert result.title == "original" and result.performer == "Teacher"
        assert result.duration == 2
        stdout, _ = await run(
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(result.path),
        )
        metadata = json.loads(stdout)
        assert [s["codec_type"] for s in metadata["streams"]] == ["audio"]
        assert metadata["format"]["tags"]["title"] == "original"
        path = result.path
    assert not path.exists()
    assert list((config.data_dir / "tmp").iterdir()) == []


@pytest.mark.parametrize("cancel", [False, True])
async def test_temp_cleanup_on_failure_or_cancel(config, fake_source, monkeypatch, cancel):
    download = AsyncMock(
        side_effect=asyncio.CancelledError() if cancel else UserError("Download failed")
    )
    monkeypatch.setattr("podcast_bot.mp3.download", download)
    with pytest.raises(asyncio.CancelledError if cancel else UserError):
        async with prepare_mp3(URL, config, AsyncMock()):
            pytest.fail("must not yield")
    assert list((config.data_dir / "tmp").iterdir()) == []


async def test_duration_rejected_before_podcast_download(config, fake_source, monkeypatch):
    download = AsyncMock()
    monkeypatch.setattr("podcast_bot.mp3.download", download)
    with pytest.raises(UserError, match="limit"):
        async with prepare_mp3(URL, replace(config, max_minutes=0.01), AsyncMock()):
            pytest.fail("must not yield")
    download.assert_not_called()


async def test_handler_background_busy_authorization_and_audio_delivery(
    config, store, monkeypatch, tmp_path
):
    entered, finish = asyncio.Event(), asyncio.Event()
    artifact = tmp_path / "test.mp3"
    artifact.write_bytes(b"mp3-test")

    @asynccontextmanager
    async def prepare(url, config, progress):
        assert url == YT
        entered.set()
        await finish.wait()
        yield MP3(artifact, "Title", "Author", 60)

    monkeypatch.setattr("podcast_bot.bot.prepare_mp3", prepare)
    handlers = BotHandlers(config, store)
    context = SimpleNamespace(bot=SimpleNamespace(send_audio=AsyncMock()))
    await handlers.handle(update("/mp3 " + YT, 99), context)
    assert handlers.mp3_task is None
    await handlers.handle(update("/mp3 " + YT), context)
    await entered.wait()
    assert not handlers.mp3_task.done()
    busy = update("/mp3 " + YT)
    await handlers.handle(busy, context)
    assert "already" in busy.effective_message.reply_text.call_args.args[0]
    status = update("/status")
    await handlers.handle(status, context)
    status.effective_message.reply_text.assert_awaited_once()
    finish.set()
    await handlers.mp3_task
    sent = context.bot.send_audio.call_args.kwargs
    assert sent["filename"] == "test.mp3" and sent["title"] == "Title"
    assert store.claim() is None  # Never enqueues transcription or study work.


async def test_handler_latest_episode_and_sanitized_failure(config, store, monkeypatch, tmp_path):
    (tmp_path / "metadata.json").write_text(json.dumps({"apple_url": URL}))
    (tmp_path / "transcript.txt").write_text("你好。")
    store.remember_source(42, tmp_path)

    @asynccontextmanager
    async def prepare(url, config, progress):
        assert url == URL
        raise RuntimeError("SECRET_PROVIDER_URL")
        yield

    monkeypatch.setattr("podcast_bot.bot.prepare_mp3", prepare)
    handlers = BotHandlers(config, store)
    u = update("/mp3")
    await handlers.handle(u, SimpleNamespace())
    await handlers.mp3_task
    status = u.effective_message.reply_text.return_value
    assert "SECRET" not in status.edit_text.call_args.args[0]
    assert "retry" in status.edit_text.call_args.args[0]


@pytest.mark.parametrize(("duration", "expected"), [(60, "128k"), (10800, "32k"), (86000, None)])
async def test_bitrate_fits_telegram_without_truncation(
    config, fake_source, monkeypatch, duration, expected
):
    async def download(client, url, directory, limit):
        path = directory / "original.mp3"
        path.write_bytes(b"audio")
        return path

    async def convert(*args, **kwargs):
        from pathlib import Path

        Path(args[-1]).write_bytes(b"mp3")
        assert args[args.index("-b:a") + 1] == expected
        return "", ""

    monkeypatch.setattr("podcast_bot.mp3.download", download)
    monkeypatch.setattr("podcast_bot.mp3.inspect_audio", AsyncMock(return_value=duration))
    conversion = AsyncMock(side_effect=convert)
    monkeypatch.setattr("podcast_bot.mp3.run", conversion)
    if expected is None:
        with pytest.raises(UserError, match="too long"):
            async with prepare_mp3(URL, replace(config, max_minutes=1440), AsyncMock()):
                pytest.fail("must not yield")
        conversion.assert_not_called()
    else:
        async with prepare_mp3(URL, config, AsyncMock()) as result:
            assert result.path.name == "mp3-original.mp3"
            assert (result.path.parent / "original.mp3").read_bytes() == b"audio"
    assert list((config.data_dir / "tmp").iterdir()) == []


async def test_missing_link_is_helpful_and_does_not_start_job(config, store):
    handlers = BotHandlers(config, store)
    u = update("/mp3")
    await handlers.handle(u, SimpleNamespace())
    assert handlers.mp3_task is None
    assert "/mp3" in u.effective_message.reply_text.call_args.args[0]
    assert store.claim() is None
