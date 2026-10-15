"""Synthetic records only; no network or production dictionary artifact."""

import gzip
import json

import pytest

from tools.dutch_raw_source_gate import subset


def test_stream_filters_language_preserves_evidence_and_refuses_overwrite(tmp_path):
    source, output = tmp_path / "raw.gz", tmp_path / "nl.jsonl"
    records = [
        {"title": "redirect", "redirect": "target"},
        {"word": "house", "lang_code": "en"},
        {"word": "huis", "lang_code": "nl", "senses": [{"tags": ["neuter"]}]},
    ]
    lines = [json.dumps(r).encode() + b"\n" for r in records]
    with gzip.open(source, "wb") as stream:
        stream.write(b"".join(lines))
    stats = subset(source, output)
    assert output.read_bytes() == lines[-1]
    assert stats["raw_records"] == 3 and stats["dutch_records"] == 1
    assert stats["uncompressed_bytes"] == sum(map(len, lines))
    with pytest.raises(FileExistsError):
        subset(source, output)


def test_corrupt_compression_is_not_a_successful_extraction(tmp_path):
    source = tmp_path / "bad.gz"
    source.write_bytes(b"not gzip")
    with pytest.raises(gzip.BadGzipFile):
        subset(source, tmp_path / "out")


def test_comparison_retains_pos_etymology_and_multiplicity(tmp_path):
    import sqlite3
    from types import SimpleNamespace

    from tools.dutch_raw_source_gate import compare

    old = SimpleNamespace(db=sqlite3.connect(":memory:"))
    raw = SimpleNamespace(db=sqlite3.connect(":memory:"))
    try:
        for index in (old, raw):
            index.db.execute("CREATE TABLE entries(record TEXT)")
        record = {"word": "sample", "pos": "noun", "senses": [{"glosses": ["fixture"]}]}
        encoded = json.dumps(record)
        old.db.executemany("INSERT INTO entries VALUES (?)", [(encoded,), (encoded,)])
        raw.db.execute("INSERT INTO entries VALUES (?)", (encoded,))
        raw.db.execute("INSERT INTO entries VALUES (?)", (json.dumps({**record, "pos": "verb"}),))
        result = compare(old, raw)
        assert result["only_old_count"] == 0
        assert result["only_raw_examples"] == [("sample", "verb", None)]
        assert result["changed_evidence_counts"]["senses"] == 1
    finally:
        old.db.close()
        raw.db.close()
