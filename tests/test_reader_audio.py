import json
from dataclasses import replace

import pytest
from test_reader import call
from test_reader import reader as reader

from podcast_bot.models import Segment, Transcript
from podcast_bot.reader.audio import align, document_audio, safe_source
from podcast_bot.reader.documents import from_text
from podcast_bot.reader.sentences import parse_sentences
from podcast_bot.storage import Storage, atomic_json
from podcast_bot.transcription.openai import combine


def unit(text, start, end):
    return {"text": text, "start": start, "end": end}


@pytest.mark.parametrize(
    ("text", "units", "expected"),
    [
        ("你好。", [unit("你好", 0.05, 1)], [(0, 1200)]),
        ("你，好！", [unit("你", 1, 1.5), unit("好", 1.5, 2)], [(880, 2200)]),
        ("你好。你好。", [unit("你好", 0, 1), unit("你好", 1, 2)], [(0, 1000), (1000, 2200)]),
        ("好好。", [unit("好", 1, 2), unit("好", 2, 3)], [(880, 3000)]),
        ("你好。再见。", [unit("你好。再见。", 0, 2)], []),
        ("你好。", [unit("您好", 0, 1)], []),
        ("你好。", [unit("你好", 2, 1)], []),
        ("你好。", [unit("你好", 0, float("nan"))], []),
        ("你好。", [unit("你好", 0, 4)], []),
        ("你好。", [unit("你", 0, 2), unit("好", 1, 3)], []),
    ],
)
def test_conservative_alignment(text, units, expected):
    result = align(parse_sentences(text), units, 3)
    assert [(r["start_ms"], r["end_ms"]) for r in result.values()] == expected


def timing(document, **overrides):
    return {
        "version": 1,
        "text": document.raw_text,
        "duration": 10,
        "audio_url": "https://podcast.example/episode.mp3",
        "words": [unit("你好", 1, 2), unit("再见", 3, 4)],
        "segments": [unit("你好。再见。", 1, 4)],
        **overrides,
    }


def test_durable_timing_and_source_association(store, tmp_path):
    document = replace(
        from_text(42, "你好。再见。"), source_type="podcast", source_reference=str(tmp_path)
    )
    atomic_json(tmp_path / "audio-timing.json", timing(document))
    store.save_reader_document(document)
    with_reload = Storage(store.root)
    try:
        reopened = with_reload.reader_document(document.id)
        source, ranges = document_audio(reopened, reopened.sentences())
        assert source["url"] == "https://podcast.example/episode.mp3"
        assert source["granularity"] == "word"
        assert len(ranges) == 2
        assert document_audio(replace(reopened, source_type="text"), reopened.sentences()) == (
            None,
            {},
        )
        changed = replace(reopened, raw_text="别的文章。")
        assert document_audio(changed, changed.sentences()) == (None, {})
        (tmp_path / "audio-timing.json").unlink()
        assert document_audio(reopened, reopened.sentences()) == (None, {})
    finally:
        with_reload.close()


def test_segment_fallback_and_corrupt_metadata(tmp_path):
    document = replace(
        from_text(42, "你好。再见。"), source_type="podcast", source_reference=str(tmp_path)
    )
    data = timing(document, words=[], segments=[unit("你好", 1, 2), unit("再见", 3, 4)])
    atomic_json(tmp_path / "audio-timing.json", data)
    assert document_audio(document, document.sentences())[0]["granularity"] == "segment"
    for broken in [
        "{",
        "null",
        json.dumps({**data, "duration": "bad"}),
        json.dumps({**data, "words": [None]}),
    ]:
        (tmp_path / "audio-timing.json").write_text(broken)
        assert document_audio(document, document.sentences()) == (None, {})


@pytest.mark.parametrize(
    "url",
    [
        "http://a.example/a",
        "https://127.0.0.1/a",
        "https://user:secret@a.example/a",
        "https://a.local/a",
        "https://a.example:444/a",
        "file:///tmp/a",
        "https://localhost/a",
    ],
)
def test_unsafe_sources_are_not_exposed(url):
    assert safe_source(url) is None


async def test_audio_metadata_requires_document_owner(reader, tmp_path):
    document = replace(
        reader.document,
        id="c" * 32,
        raw_text="你好。再见。",
        source_type="podcast",
        source_reference=str(tmp_path),
    )
    atomic_json(tmp_path / "audio-timing.json", timing(document))
    reader.store.save_reader_document(document)
    path = f"/api/reader/{document.id}"
    status, data = await call(reader, "GET", path)
    assert status == 200 and data["audio"]["url"].endswith("episode.mp3")
    assert data["sentences"][0]["audio"]["start_ms"] == 880
    assert (await call(reader, "GET", path, headers={}))[0] == 401
    reader.store.save_reader_document(replace(document, id="d" * 32, chat_id=999))
    status, data = await call(reader, "GET", "/api/reader/" + "d" * 32)
    assert status == 404 and "audio" not in data


def test_word_cache_roundtrip_and_legacy_cache(store):
    transcript = Transcript("你好", words=[Segment(0.1, 0.5, "你好")])
    store.save_chunk(123, "new", transcript, 0)
    assert store.chunk(123, "new").words == transcript.words
    with store.db:
        store.db.execute(
            "INSERT INTO chunks VALUES (?,?,?)",
            (123, "old", json.dumps({"text": "你好", "segments": [], "language": "zh"})),
        )
    assert store.chunk(123, "old").words == []
    combined = combine([(0, transcript), (100, transcript)])
    assert combined.words[1].start == 100.1
    assert combined.words[1].end == 100.5
