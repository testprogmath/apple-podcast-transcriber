import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_pipeline_bot import update

from podcast_bot.bot import BotHandlers
from podcast_bot.models import UserError
from podcast_bot.mp3 import MP3, youtube_source
from podcast_bot.reader.audio import document_audio
from podcast_bot.reader.documents import from_transcript
from podcast_bot.reader.media import asset_path
from podcast_bot.subtitles import Subtitles
from podcast_bot.youtube import prepare_youtube

URL = "https://www.youtube.com/watch?v=rHyuQctiDZM"
SRT = "1\n00:00:00,000 --> 00:00:01,000\n你好。\n"


@pytest.fixture
def sources(tmp_path, monkeypatch):
    caption = tmp_path / "video.srt"
    caption.write_text(SRT)
    audio = tmp_path / "temporary.mp3"
    audio.write_bytes(b"fake-mp3")
    calls = []

    @asynccontextmanager
    async def captions(url, language, data_dir):
        yield Subtitles(caption, caption, language, False)

    @asynccontextmanager
    async def mp3(url, config, progress, language=None):
        calls.append((url, language))
        yield MP3(audio, "Video", "Author", 2)

    monkeypatch.setattr("podcast_bot.youtube.download_subtitles", captions)
    monkeypatch.setattr("podcast_bot.youtube.prepare_mp3", mp3)
    monkeypatch.setattr("podcast_bot.youtube.inspect_audio", AsyncMock(return_value=2.04))
    return SimpleNamespace(calls=calls, audio=audio)


async def test_persist_reopen_and_reuse_without_audio_redownload(config, sources):
    source, status = await prepare_youtube(URL, "zh", 42, config, AsyncMock())
    assert "saved" in status
    sources.audio.unlink()
    document = from_transcript(42, source)
    audio, ranges, reason = document_audio(document, document.sentences(), config.data_dir)
    assert reason == "" and ranges[0]["start_ms"] == 0
    assert audio["duration"] == 2.04
    assert asset_path(config.data_dir, audio["asset_id"]).read_bytes() == b"fake-mp3"
    assert sources.calls == [(URL, "zh")]
    reopened, status = await prepare_youtube(URL, "zh", 42, config, AsyncMock())
    assert reopened == source and "reused" in status
    assert len(sources.calls) == 1
    metadata = json.loads((source / "metadata.json").read_text())
    assert metadata["source_url"] == URL and metadata["title"] == "Video"


async def test_audio_failure_keeps_subtitles_for_tts(config, sources, monkeypatch):
    @asynccontextmanager
    async def fail(*args, **kwargs):
        raise UserError("No matching audio track.")
        yield

    monkeypatch.setattr("podcast_bot.youtube.prepare_mp3", fail)
    source, status = await prepare_youtube(URL, "zh", 42, config, AsyncMock())
    assert "TTS" in status and (source / "transcript.txt").is_file()
    assert "audio_asset" not in json.loads((source / "audio-timing.json").read_text())


async def test_captions_beyond_audio_are_not_linked(config, sources, monkeypatch):
    monkeypatch.setattr("podcast_bot.youtube.inspect_audio", AsyncMock(return_value=0.5))
    source, status = await prepare_youtube(URL, "zh", 42, config, AsyncMock())
    assert "not linked" in status
    assert not (config.data_dir / "media").exists()


async def test_no_subtitles_does_not_download_audio(config, sources, monkeypatch):
    @asynccontextmanager
    async def fail(*args):
        raise UserError("No subtitles")
        yield

    monkeypatch.setattr("podcast_bot.youtube.download_subtitles", fail)
    with pytest.raises(UserError):
        await prepare_youtube(URL, "zh", 42, config, AsyncMock())
    assert not sources.calls


async def test_language_selection_uses_matching_track_and_rejects_foreign_dub(monkeypatch):
    data = {
        "duration": 10,
        "vcodec": "none",
        "protocol": "https",
        "url": "https://audio.example/en",
        "formats": [
            {
                "language": "en",
                "vcodec": "none",
                "protocol": "https",
                "url": "https://audio.example/en",
            },
            {
                "language": "zh-CN",
                "vcodec": "none",
                "protocol": "https",
                "url": "https://audio.example/zh",
            },
        ],
    }
    execute = AsyncMock(return_value=(json.dumps(data), ""))
    monkeypatch.setattr("podcast_bot.mp3.run", execute)
    assert (await youtube_source(URL, 180, "zh"))[0].endswith("/zh")
    data["formats"].pop()
    execute.return_value = (json.dumps(data), "")
    with pytest.raises(UserError, match="matching-language"):
        await youtube_source(URL, 180, "zh")
    data["formats"] = []
    execute.return_value = (json.dumps(data), "")
    assert (await youtube_source(URL, 180, "zh"))[0] == data["url"]


async def test_command_generates_only_study_job_and_reader(config, store, sources):
    handlers = BotHandlers(
        replace(config, reader_url="https://reader.example", study_enabled=True), store
    )
    context = SimpleNamespace()
    await handlers.handle(update(f"/youtube {URL}", 99), context)
    assert handlers.youtube_task is None
    await handlers.handle(update(f"/youtube {URL}"), context)
    await handlers.youtube_task
    document = store.recent_reader_document(42)
    assert document.source_type == "subtitles"
    audio, _, _ = document_audio(document, document.sentences(), store.root)
    assert audio and audio["url"].startswith("/api/reader/")
    job = store.claim()
    assert job.kind == "study" and job.url == URL
    assert store.db.execute("SELECT count(*) FROM usage").fetchone()[0] == 0
