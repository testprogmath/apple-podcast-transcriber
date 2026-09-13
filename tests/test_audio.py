import httpx
import pytest

from podcast_bot.models import Segment, Transcript, UserError
from podcast_bot.net import check_public_url, fetch
from podcast_bot.transcription.audio import (
    check_duration,
    download,
    inspect_audio,
    prepare,
    run,
    validate_audio,
)
from podcast_bot.transcription.openai import combine, to_srt


@pytest.mark.parametrize("duration", [10801, float("nan"), float("inf"), 0, -1])
def test_maximum_duration(duration):
    with pytest.raises(UserError):
        check_duration(duration, 180)


def test_duration_boundary():
    check_duration(10800, 180)


@pytest.mark.parametrize(
    ("content", "headers", "limit"),
    [
        (b"", {"content-type": "audio/mpeg"}, 100),
        (b"123", {"content-type": "text/html"}, 100),
        (b"123", {"content-type": "audio/mpeg", "content-length": "200"}, 100),
        (b"12345", {"content-type": "audio/mpeg"}, 4),
        (b"123", {"content-type": "audio/mpeg", "content-length": "4"}, 100),
    ],
)
async def test_bad_downloads(tmp_path, content, headers, limit):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=content, headers=headers)
        )
    ) as client:
        with pytest.raises(UserError):
            await download(client, "https://example.test/a.mp3", tmp_path, limit)


async def test_redirect_download(tmp_path):
    def handler(request):
        if request.url.path == "/a":
            return httpx.Response(302, headers={"location": "/b"})
        return httpx.Response(200, content=b"audio", headers={"content-type": "audio/mpeg"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await download(client, "https://example.test/a", tmp_path, 100)
        ).read_bytes() == b"audio"


async def test_metadata_size_limit():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"12345"))
    ) as client:
        with pytest.raises(UserError):
            await fetch(client, "https://example.test/a", 4)


async def test_non_public_rejected(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(UserError, match="non-public"):
        await check_public_url("https://example.test")


def test_chunk_combination_timestamps_and_repetition():
    a = Transcript("我们。", [Segment(0, 1.5, "我们。")], "zh")
    b = Transcript("我们。咱们。", [Segment(0.2, 2.5, "我们。咱们。")], "zh")
    result = combine([(0, a), (600.5, b)])
    assert result.text == "我们。\n\n我们。咱们。\n"
    assert result.segments[1].start == 600.7
    assert "00:10:00,700 --> 00:10:03,000" in to_srt(result)


async def test_real_ffmpeg_synthetic_audio(tmp_path):
    # Only local generated sound: no network or paid transcription.
    source = tmp_path / "test.wav"
    await run(
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(source)
    )
    duration = await inspect_audio(source)
    assert duration == pytest.approx(3, abs=0.1)
    await validate_audio(source)
    chunks = await prepare(source, duration, tmp_path, 2)
    assert len(chunks) == 2
    assert chunks[1].offset == pytest.approx(2)
    assert all(c.path.is_file() for c in chunks)
    assert sum(c.duration for c in chunks) == pytest.approx(duration)


async def test_corrupt_audio(tmp_path):
    source = tmp_path / "bad.mp3"
    source.write_bytes(b"not audio")
    with pytest.raises(UserError):
        await inspect_audio(source)


async def test_real_mp3_copy_split_at_silence(tmp_path):
    source = tmp_path / "test.mp3"
    await run(
        "ffmpeg",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=6",
        "-af",
        "volume=enable='between(t,2,3)':volume=0",
        "-c:a",
        "libmp3lame",
        str(source),
    )
    duration = await inspect_audio(source)
    chunks = await prepare(source, duration, tmp_path, 4)
    assert 2 <= chunks[1].offset <= 3.2
    for chunk in chunks:
        await validate_audio(chunk.path)
    assert sum(c.duration for c in chunks) == pytest.approx(duration)


def test_missing_ffmpeg(monkeypatch):
    import shutil

    from podcast_bot.transcription.audio import check_ffmpeg

    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(UserError, match="brew install ffmpeg"):
        check_ffmpeg()
