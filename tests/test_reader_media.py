import asyncio
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_reader import reader as reader

from podcast_bot.reader.audio import document_audio
from podcast_bot.reader.documents import from_transcript
from podcast_bot.reader.media import FileSlice, asset_path, grant_cookie, valid_grant
from podcast_bot.reader.server import ReaderServer
from podcast_bot.storage import atomic_json
from podcast_bot.subtitle_import import import_srt
from podcast_bot.youtube import save_audio

SRT = b"1\n00:00:00,000 --> 00:00:01,000\n\xe4\xbd\xa0\xe5\xa5\xbd\xe3\x80\x82\n"


@pytest.fixture
def media(reader, tmp_path):
    source = import_srt(
        reader.store.root,
        SRT,
        "test.srt",
        42,
        "zh",
        source_url="https://www.youtube.com/watch?v=abcdefghijk",
    )
    audio = tmp_path / "test.mp3"
    audio.write_bytes(bytes(range(256)) * 1024)
    identifier = save_audio(audio, reader.store.root, 10)
    timing = json.loads((source / "audio-timing.json").read_text())
    timing.update(audio_asset=identifier, duration=2)
    atomic_json(source / "audio-timing.json", timing)
    doc = from_transcript(42, source)
    reader.store.save_reader_document(doc)
    return SimpleNamespace(
        reader=reader,
        document=doc,
        path=asset_path(reader.store.root, identifier),
        identifier=identifier,
        source=source,
    )


async def grant(media):
    status, headers, body = await media.reader.api.dispatch(
        "GET", f"/api/reader/{media.document.id}", media.reader.headers, b""
    )
    assert status == 200
    data = json.loads(body)
    assert data["audio"]["url"] == f"/api/reader/{media.document.id}/audio"
    assert "asset_id" not in data["audio"]
    assert media.reader.api.config.token.encode() not in body
    cookie = headers["Set-Cookie"]
    assert "Secure; HttpOnly; SameSite=Strict" in cookie
    assert "?" not in data["audio"]["url"]
    return cookie.split(";", 1)[0]


async def test_media_authorization_owner_scope_and_expiry(media, monkeypatch):
    api = media.reader.api
    path = f"/api/reader/{media.document.id}/audio"
    assert (await api.dispatch("GET", path, {}, b""))[0] == 401
    cookie = await grant(media)
    assert (await api.dispatch("GET", path, {"cookie": cookie}, b""))[0] == 200
    assert (await api.dispatch("GET", path, {"x-telegram-init-data": "bad"}, b""))[0] == 401
    another = replace(media.document, id="a" * 32)
    media.reader.store.save_reader_document(another)
    assert (await api.dispatch("GET", f"/api/reader/{another.id}/audio", {"cookie": cookie}, b""))[
        0
    ] == 401
    assert (await api.dispatch("GET", path, {"cookie": cookie + "x"}, b""))[0] == 401
    import time

    later = time.time() + 50000
    monkeypatch.setattr("podcast_bot.reader.media.time.time", lambda: later)
    assert (await api.dispatch("GET", path, {"cookie": cookie}, b""))[0] == 401


async def test_media_checks_current_owner_even_with_valid_grant(media):
    cookie = await grant(media)
    with media.reader.store.db:
        media.reader.store.db.execute(
            "UPDATE reader_documents SET chat_id=99 WHERE id=?", (media.document.id,)
        )
    assert (
        await media.reader.api.dispatch(
            "GET", f"/api/reader/{media.document.id}/audio", {"cookie": cookie}, b""
        )
    )[0] == 404


@pytest.mark.parametrize(
    ("range_value", "status", "offset", "length"),
    [
        ("", 200, 0, 262144),
        ("bytes=0-9", 206, 0, 10),
        ("bytes=262140-", 206, 262140, 4),
        ("bytes=-4", 206, 262140, 4),
        ("bytes=262140-999999", 206, 262140, 4),
        ("bytes=999999-", 416, 0, 0),
        ("bytes=10-1", 416, 0, 0),
        ("bytes=-0", 416, 0, 0),
        ("bytes=0-1,3-4", 416, 0, 0),
        ("nope", 416, 0, 0),
    ],
)
async def test_exact_byte_ranges(media, range_value, status, offset, length):
    result, headers, body = await media.reader.api.dispatch(
        "GET",
        f"/api/reader/{media.document.id}/audio",
        {**media.reader.headers, "range": range_value},
        b"",
    )
    assert result == status
    assert headers["Accept-Ranges"] == "bytes"
    if status == 416:
        assert body == b"" and headers["Content-Range"] == "bytes */262144"
    else:
        assert isinstance(body, FileSlice)
        assert (body.offset, body.length) == (offset, length)
        assert body.path == media.path
        if status == 206:
            assert headers["Content-Range"] == f"bytes {offset}-{offset + length - 1}/262144"


async def test_missing_or_path_traversal_asset_fails_closed(media):
    media.path.unlink()
    assert (
        await media.reader.api.dispatch(
            "GET", f"/api/reader/{media.document.id}/audio", media.reader.headers, b""
        )
    )[0] == 404
    assert asset_path(media.reader.store.root, "../metadata") is None
    assert asset_path(media.reader.store.root, "https://internal.test") is None
    secret = media.source / "metadata.json"
    media.path.symlink_to(secret)
    assert asset_path(media.reader.store.root, media.identifier) is None


async def test_cached_pack_uses_new_canonical_audio_reference(media, tmp_path):
    pack = tmp_path / "cached-study"
    pack.mkdir()
    for name in ["transcript.txt", "metadata.json"]:
        (pack / name).write_bytes((media.source / name).read_bytes())
    atomic_json(pack / "audio-timing.json", {"version": 1, "audio_url": None})
    document = from_transcript(42, pack)
    audio, ranges, _ = document_audio(document, document.sentences(), media.reader.store.root)
    assert audio["asset_id"] == media.identifier and ranges


@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_http_stream_is_bounded_and_headers_match_bytes(media, method):
    cookie = await grant(media)
    stream = asyncio.StreamReader()
    stream.feed_data(
        f"{method} /api/reader/{media.document.id}/audio HTTP/1.1\r\nCookie: {cookie}\r\nRange: bytes=65535-200000\r\n\r\n".encode()
    )
    stream.feed_eof()
    chunks = []
    writer = SimpleNamespace(
        write=chunks.append, drain=AsyncMock(), close=lambda: None, wait_closed=AsyncMock()
    )
    await ReaderServer(media.reader.api).connection(stream, writer)
    head, content = b"".join(chunks).split(b"\r\n\r\n", 1)
    if method == "GET":
        assert b"206 Partial Content" in head
        assert b"Content-Length: 134466" in head
        assert content == media.path.read_bytes()[65535:200001]
    else:
        assert b"200 OK" in head and b"Content-Length: 262144" in head
        assert content == b""
    assert max(map(len, chunks)) <= 65536


async def test_client_disconnect_closes_stream_gracefully(media):
    stream = asyncio.StreamReader()
    cookie = await grant(media)
    stream.feed_data(
        f"GET /api/reader/{media.document.id}/audio HTTP/1.1\r\nCookie: {cookie}\r\n\r\n".encode()
    )
    writer = SimpleNamespace(
        write=lambda _: None,
        drain=AsyncMock(side_effect=ConnectionResetError),
        close=lambda: None,
        wait_closed=AsyncMock(),
    )
    await ReaderServer(media.reader.api).connection(stream, writer)
    writer.wait_closed.assert_awaited_once()


def test_content_addressing_quota_and_cleanup(tmp_path):
    audio = tmp_path / "input.mp3"
    audio.write_bytes(b"audio")
    root = tmp_path / "data"
    identifier = save_audio(audio, root, 1)
    assert identifier == hashlib.sha256(b"audio").hexdigest()
    assert save_audio(audio, root, 1) == identifier
    audio.write_bytes(b"x" * 1_000_001)
    from podcast_bot.models import UserError

    with pytest.raises(UserError, match="full"):
        save_audio(audio, root, 1)
    assert len(list((root / "media").iterdir())) == 1


def test_cookie_is_bound_to_secret_and_document():
    cookie = grant_cookie("secret", "a" * 32, 42).split(";", 1)[0]
    assert valid_grant(cookie, "secret", "a" * 32, 42)
    assert not valid_grant(cookie, "other-secret", "a" * 32, 42)
    assert not valid_grant(cookie, "secret", "a" * 32, 99)
