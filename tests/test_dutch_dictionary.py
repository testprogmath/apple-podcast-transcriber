"""Synthetic lexical evidence; no dictionary downloads, network or paid providers."""

import hashlib
import json
import socket
import sqlite3

import pytest

from podcast_bot.reader.dutch import SNAPSHOT, DutchDictionary
from tools.build_dutch_dictionary import build


def entry(word, pos="noun", tags=("neuter",), forms=(), senses=None):
    return {
        "word": word,
        "pos": pos,
        "lang_code": "nl",
        "forms": list(forms),
        "senses": senses
        if senses is not None
        else [{"tags": list(tags), "glosses": ["fixture definition"]}],
    }


def form(text, *tags):
    return {"form": text, "tags": list(tags)}


def artifact(tmp_path, records):
    source, output = tmp_path / "fixture.jsonl", tmp_path / "fixture.sqlite3"
    source.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n")
    digest = hashlib.file_digest(source.open("rb"), "sha256").hexdigest()
    snapshot = {**SNAPSHOT, "subset_sha256": digest}
    stats = build(source, output, snapshot=snapshot)
    return DutchDictionary(output, snapshot=snapshot), source, output, snapshot, stats


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("neuter", ["het"]),
        ("masculine", ["de"]),
        ("feminine", ["de"]),
        ("common-gender", ["de"]),
        ("common", []),
        ("unknown", []),
    ],
)
def test_article_is_lexical_sense_evidence(tmp_path, tag, expected):
    d, *_ = artifact(
        tmp_path, [entry("sample", tags=[tag], forms=[form("sampletje", "diminutive", "neuter")])]
    )
    try:
        c = d.lookup("sample")["candidates"][0]
        assert c["senses"][0]["articles"] == expected
        assert c["senses"][0]["article"] == (expected[0] if expected else None)
    finally:
        d.close()


def test_senses_articles_register_reference_glosses_and_morphology(tmp_path):
    senses = [
        {"tags": ["masculine"], "glosses": ["fixture person"]},
        {"tags": ["neuter", "derogatory", "informal"], "glosses": ["fixture second sense"]},
        {"tags": ["neuter", "obsolete"], "glosses": ["fixture old sense"]},
        {
            "form_of": [{"word": "missing"}],
            "tags": ["form-of", "feminine"],
            "glosses": ["reference only"],
        },
    ]
    d, *_ = artifact(
        tmp_path,
        [
            entry(
                "sample",
                senses=senses,
                forms=[
                    form("samples", "plural"),
                    form("sampla", "plural"),
                    form("sampletje", "diminutive", "neuter"),
                    form("sampletjes", "diminutive", "plural"),
                ],
            )
        ],
    )
    try:
        c = d.lookup("sample")["candidates"][0]
        assert [s["articles"] for s in c["senses"]] == [["de"], ["het"], ["het"]]
        assert c["senses"][1]["labels"] == ["derogatory", "informal", "neuter"]
        assert "obsolete" in c["senses"][2]["labels"]
        assert c["plural"] == ["sampla", "samples"] and c["diminutive"] == ["sampletje"]
        assert {f["surface"] for f in c["forms"]} == {"sampla", "samples", "sampletje"}
        assert "reference only" not in str(c)
    finally:
        d.close()


@pytest.mark.parametrize(
    "surface,lemma",
    [
        ("huizen", "huis"),
        ("kinderen", "kind"),
        ("steden", "stad"),
        ("schepen", "schip"),
        ("musea", "museum"),
        ("jaren", "jaar"),
        ("jongens", "jongen"),
    ],
)
def test_explicit_reverse_form_and_competing_direct_entry(tmp_path, surface, lemma):
    d, *_ = artifact(
        tmp_path,
        [
            entry(lemma, forms=[form(surface, "plural")]),
            entry(surface, pos="verb"),
            entry(surface.title(), pos="name"),
        ],
    )
    try:
        result = d.lookup(surface)
        assert result["is_ambiguous"]
        assert {(c["lemma"], c["pos"]) for c in result["candidates"]} == {
            (lemma, "noun"),
            (surface, "verb"),
            (surface.title(), "name"),
        }
        assert result["candidates"][0]["lemma"] == surface
        assert result["candidates"][1]["lemma"] == lemma
        assert d.lookup(surface) == result
    finally:
        d.close()


def test_form_alt_chain_cycle_broken_and_lexicalized_diminutive(tmp_path):
    def refs(word, target, kind="form_of"):
        return entry(
            word,
            senses=[{kind: [{"word": target}], "tags": ["form-of"], "glosses": ["reference only"]}],
        )

    d, *_ = artifact(
        tmp_path,
        [
            entry("base"),
            refs("surface", "variant"),
            refs("variant", "base", "alt_of"),
            refs("a", "b"),
            refs("b", "a"),
            refs("broken", "absent"),
            entry("meisje", forms=[form("meisjes", "plural")]),
            refs("meisje", "meid"),
            entry("meid"),
        ],
    )
    try:
        result = d.lookup("surface")["candidates"]
        assert [c["lemma"] for c in result] == ["base"]
        assert result[0]["relationships"][0]["kind"] == "alt_of"
        assert not d.lookup("a")["candidates"] and not d.lookup("broken")["candidates"]
        assert {c["lemma"] for c in d.lookup("meisje")["candidates"]} == {"meid", "meisje"}
    finally:
        d.close()


@pytest.mark.parametrize(
    "word,query",
    [
        ("café", "CAFE\u0301"),
        ("naïef", "NAÏEF"),
        ("nazi's", "nazi's"),
        ("Zuid-Afrika", "zuid-afrika"),
        ("in feite", " in   feite "),
    ],
)
def test_normalization_without_apostrophe_or_hyphen_guessing(tmp_path, word, query):
    d, *_ = artifact(tmp_path, [entry(word)])
    try:
        assert [c["lemma"] for c in d.lookup(query)["candidates"]] == [word]
        if "'" in word:
            assert not d.lookup(word.replace("'", "’"))["candidates"]
        if "-" in word:
            assert not d.lookup(word.replace("-", ""))["candidates"]
    finally:
        d.close()


def test_missing_plural_missing_article_and_no_gender_on_adjective(tmp_path):
    d, *_ = artifact(tmp_path, [entry("unknown", tags=[]), entry("adjective", pos="adj")])
    try:
        c = d.lookup("unknown")["candidates"][0]
        assert not c["plural"] and not c["senses"][0]["articles"]
        assert not d.lookup("adjective")["candidates"][0]["senses"][0]["articles"]
    finally:
        d.close()


def test_builder_validation_atomicity_determinism_and_read_only_service(tmp_path, monkeypatch):
    d, source, output, snapshot, stats = artifact(
        tmp_path, [entry("base"), {"title": "redirect"}, entry("English") | {"lang_code": "en"}]
    )
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("network"))
    assert d.lookup("base")["available"]
    with pytest.raises(sqlite3.OperationalError):
        d.db.execute("DELETE FROM entries")
    d.close()
    assert stats["entries"] == 1 and stats["processed_records"] == 3
    assert output.stat().st_mode & 0o444 == 0o444
    with pytest.raises(FileExistsError):
        build(source, output, snapshot=snapshot)
    with pytest.raises(ValueError, match="checksum"):
        build(source, tmp_path / "bad.sqlite3")
    assert not (tmp_path / "bad.sqlite3").exists()
    before = output.read_bytes()
    assert build(source, output, snapshot=snapshot, overwrite=True)["sha256"] == stats["sha256"]
    assert before == output.read_bytes()
    assert not list(tmp_path.glob(".dutch-*"))


@pytest.mark.parametrize(
    "failure", ["missing", "corrupt", "schema", "source", "input", "tables", "index", "runtime"]
)
def test_artifact_failure_never_breaks_lookup(tmp_path, failure):
    d, _, output, snapshot, _ = artifact(tmp_path, [entry("base")])
    d.close()
    if failure == "missing":
        output.unlink()
    elif failure == "corrupt":
        output.write_bytes(b"not SQLite")
    else:
        with sqlite3.connect(output) as db:
            if failure == "schema":
                db.execute("UPDATE metadata SET value='999' WHERE key='schema_version'")
            elif failure == "source":
                db.execute("UPDATE metadata SET value='other' WHERE key='subset_sha256'")
            elif failure == "input":
                db.execute("UPDATE metadata SET value='wrong' WHERE key='input_sha256'")
            elif failure == "index":
                db.execute("DROP INDEX surfaces_key")
            elif failure == "tables":
                db.execute("DROP TABLE forms")
    d = DutchDictionary(output, snapshot=snapshot)
    if failure == "runtime":
        with sqlite3.connect(output) as db:
            db.execute("DROP TABLE forms")
    try:
        result = d.lookup("base")
        assert not result["available"] and not result["candidates"]
    finally:
        d.close()


def test_full_raw_gzip_filter_and_failed_build_does_not_publish(tmp_path):
    import gzip

    source, output = tmp_path / "full.jsonl.gz", tmp_path / "full.sqlite3"
    records = [{"title": "redirect"}, entry("sample"), entry("foreign") | {"lang_code": "en"}]
    with gzip.open(source, "wt", encoding="utf-8") as stream:
        for r in records:
            stream.write(json.dumps(r) + "\n")
    digest = hashlib.file_digest(source.open("rb"), "sha256").hexdigest()
    snapshot = {**SNAPSHOT, "compressed_sha256": digest}
    stats = build(source, output, source_kind="compressed", snapshot=snapshot)
    d = DutchDictionary(output, snapshot=snapshot)
    try:
        assert stats["entries"] == 1 and d.lookup("sample")["available"]
        assert not d.lookup("foreign")["candidates"]
        plans = [
            r[3]
            for r in d.db.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM entries WHERE key=?", ("sample",)
            )
        ]
        assert any("entries_key" in p for p in plans)
    finally:
        d.close()
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"lang_code": "nl", "word": "invalid"}) + "\n")
    snapshot = {
        **SNAPSHOT,
        "subset_sha256": hashlib.file_digest(bad.open("rb"), "sha256").hexdigest(),
    }
    with pytest.raises(KeyError):
        build(bad, tmp_path / "failed.sqlite3", snapshot=snapshot)
    assert not (tmp_path / "failed.sqlite3").exists() and not list(tmp_path.glob(".dutch-*"))


def test_case_order_uses_canonical_unicode_and_conflicting_sense_stays_nullable(tmp_path):
    d, *_ = artifact(
        tmp_path,
        [entry("café"), entry("Café", pos="name"), entry("conflict", tags=["masculine", "neuter"])],
    )
    try:
        assert d.lookup("Cafe\u0301")["candidates"][0]["lemma"] == "Café"
        assert d.lookup("cafe\u0301")["candidates"][0]["lemma"] == "café"
        assert d.lookup("conflict")["is_ambiguous"]
        sense = d.lookup("conflict")["candidates"][0]["senses"][0]
        assert sense["article"] is None and sense["articles"] == ["de", "het"]
    finally:
        d.close()
