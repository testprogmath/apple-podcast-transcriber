import socket
import sqlite3

import pytest

from podcast_bot.reader.cedict import build, parse_line, source_version, tone_marks
from podcast_bot.reader.dictionary import Dictionary
from podcast_bot.reader.enrich import CONTEXTUAL, enrich

SAMPLE = """# CC-CEDICT sample used only by these tests
#! version=1
#! entries=9
#! date=2026-09-14T05:30:59Z
學習 学习 [xue2 xi2] /to learn; to study/
銀行 银行 [yin2 hang2] /bank/CL:家[jia1]/
行 行 [hang2] /row; line/line of business/
行 行 [xing2] /to walk; to go/OK!/
一律 一律 [yi1 lu:4] /same; identical/uniformly; all; without exception/
女兒 女儿 [nu:3 er2] /daughter/
辛苦 辛苦 [xin1 ku3] /exhausting; hard/
3D打印 3D打印 [san1 D da3 yin4] /to 3D print/
武漢 武汉 [Wu3 han4] /Wuhan, capital of Hubei/
malformed line without brackets
"""


@pytest.fixture
def database(tmp_path):
    source = tmp_path / "cedict_ts.u8"
    source.write_text(SAMPLE, encoding="utf-8")
    target = tmp_path / "cedict.sqlite3"
    assert build(source, target) == 9
    return target


@pytest.fixture
def dictionary(database):
    instance = Dictionary(database)
    yield instance
    instance.close()


def test_parser_reads_all_four_fields():
    entry = parse_line("學習 学习 [xue2 xi2] /to learn; to study/")
    assert entry.traditional == "學習"
    assert entry.simplified == "学习"
    assert entry.numeric_pinyin == "xue2 xi2"
    assert entry.definitions == ("to learn; to study",)


def test_parser_splits_multiple_definitions():
    entry = parse_line("銀行 银行 [yin2 hang2] /bank/CL:家[jia1]/")
    assert entry.definitions == ("bank", "CL:家[jia1]")


@pytest.mark.parametrize(
    "line", ["# comment", "", "   ", "malformed line", "没有 没有 [mei2 you3] no slashes"]
)
def test_parser_skips_comments_and_malformed_lines(line):
    assert parse_line(line) is None


def test_parser_reads_the_snapshot_version_header(tmp_path):
    source = tmp_path / "cedict_ts.u8"
    source.write_text(SAMPLE, encoding="utf-8")
    assert source_version(source)["date"] == "2026-09-14T05:30:59Z"
    assert source_version(source)["entries"] == "9"


@pytest.mark.parametrize(
    ("numeric", "expected"),
    [
        ("ma1 ma2 ma3 ma4 ma5", "mā má mǎ mà ma"),
        ("yan2 jiu1 cheng2 guo3", "yán jiū chéng guǒ"),
        ("xin1 ku3 le5", "xīn kǔ le"),
        ("hao3 r5", "hǎo r"),
        ("ou3", "ǒu"),
        ("hui4", "huì"),
        ("liu2", "liú"),
    ],
)
def test_numeric_pinyin_becomes_tone_marks(numeric, expected):
    assert tone_marks(numeric) == expected


@pytest.mark.parametrize(
    ("numeric", "expected"),
    [("lu:4", "lǜ"), ("nu:3", "nǚ"), ("lve4", "lüè"), ("nu:e4", "nüè"), ("U:1", "Ǖ")],
)
def test_u_umlaut_conventions_are_normalised(numeric, expected):
    assert tone_marks(numeric) == expected


@pytest.mark.parametrize(("numeric", "expected"), [("Wu3 han4", "Wǔ hàn"), ("An1", "Ān")])
def test_capitalisation_is_preserved(numeric, expected):
    assert tone_marks(numeric) == expected


def test_tokens_without_a_tone_digit_pass_through():
    assert tone_marks("san1 D da3 yin4") == "sān D dǎ yìn"
    assert tone_marks("11 Qu1") == "11 Qū"
    assert tone_marks("") == ""


def test_simplified_and_traditional_both_resolve(dictionary):
    assert dictionary.lookup("学习").pinyin == "xué xí"
    assert dictionary.lookup("學習").pinyin == "xué xí"
    assert dictionary.lookup("学习").definitions == ("to learn; to study",)


def test_unknown_entry_is_clean(dictionary):
    assert dictionary.lookup("完全不存在的词组") is None
    assert dictionary.lookup("") is None
    assert dictionary.lookup_many([]) == {}


def test_multiple_entries_pick_a_deterministic_primary_and_keep_alternatives(dictionary):
    entry = dictionary.lookup("行")
    assert entry.pinyin == "háng", "lowest source id wins"
    assert [alternative[0] for alternative in entry.alternatives] == ["xíng"]
    assert dictionary.lookup("行").pinyin == dictionary.lookup("行").pinyin


def test_definitions_merge_across_homographs_without_duplicates(dictionary):
    definitions = dictionary.lookup("行").definitions
    assert definitions == ("row; line", "line of business", "to walk; to go", "OK!")
    assert len(definitions) == len(set(definitions))


def test_batch_lookup_returns_one_entry_per_distinct_glyph(dictionary):
    found = dictionary.lookup_many(["学习", "银行", "学习", "不存在", "银行"])
    assert set(found) == {"学习", "银行"}
    assert found["银行"].pinyin == "yín háng"


def test_lookup_needs_no_network(dictionary, monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("network call"))
    assert dictionary.lookup("一律").pinyin == "yī lǜ"


def test_a_missing_database_degrades_cleanly(tmp_path):
    absent = Dictionary(tmp_path / "nothing.sqlite3")
    assert absent.available is False
    assert absent.lookup("学习") is None
    assert absent.lookup_many(["学习"]) == {}


def test_a_corrupt_database_degrades_cleanly(tmp_path):
    broken = tmp_path / "broken.sqlite3"
    broken.write_bytes(b"not a database")
    instance = Dictionary(broken)
    assert instance.available is False
    assert instance.lookup("学习") is None


def test_the_database_is_read_only_at_runtime(dictionary):
    with pytest.raises(sqlite3.OperationalError):
        dictionary._connection.execute("DELETE FROM entries")


def test_the_build_is_deterministic(tmp_path):
    source = tmp_path / "cedict_ts.u8"
    source.write_text(SAMPLE, encoding="utf-8")
    first, second = tmp_path / "a.sqlite3", tmp_path / "b.sqlite3"
    build(source, first)
    build(source, second)
    assert first.read_bytes() == second.read_bytes()


def test_the_build_indexes_both_written_forms(database):
    connection = sqlite3.connect(database)
    plan = connection.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM entries WHERE simplified = ?", ("学习",)
    ).fetchall()
    assert any("entries_simplified" in str(row) for row in plan)
    plan = connection.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM entries WHERE traditional = ?", ("學習",)
    ).fetchall()
    assert any("entries_traditional" in str(row) for row in plan)
    connection.close()


def test_a_contextual_meaning_is_preferred_for_the_native_track(dictionary):
    lexeme = enrich(["银行"], {"银行": "банк (контекст)"}, {}, dictionary)["银行"]
    assert lexeme.native() == ("банк (контекст)",)
    assert lexeme.native_source == CONTEXTUAL
    assert lexeme.english == ("bank", "CL:家[jia1]"), "English stays available alongside"


def test_the_english_track_is_populated_from_the_dictionary(dictionary):
    lexeme = enrich(["银行"], {}, {}, dictionary)["银行"]
    assert lexeme.english == ("bank", "CL:家[jia1]")
    assert lexeme.native() == (), "no contextual meaning and no Russian source configured"


def test_a_glyph_known_to_neither_source_carries_no_meaning(dictionary):
    lexeme = enrich(["研究成果"], {}, {}, dictionary)["研究成果"]
    assert lexeme.native() == () and lexeme.english == ()
    assert lexeme.native_source == ""
    assert lexeme.pinyin == "yán jiū chéng guǒ", "local fallback still produces pinyin"


def test_pinyin_precedence_prefers_study_then_dictionary_then_fallback(dictionary):
    assert enrich(["银行"], {}, {"银行": "yín háng (изучено)"}, dictionary)["银行"].pinyin == (
        "yín háng (изучено)"
    )
    assert enrich(["银行"], {}, {}, dictionary)["银行"].pinyin == "yín háng"
    assert enrich(["银行"], {}, {}, None)["银行"].pinyin == "yín háng"


def test_the_dictionary_resolves_polyphonic_readings_the_fallback_would_miss(dictionary):
    assert enrich(["银行"], {}, {}, dictionary)["银行"].pinyin == "yín háng"
    assert enrich(["行"], {}, {}, dictionary)["行"].pinyin == "háng"


def test_enrichment_looks_each_distinct_glyph_up_once(dictionary, monkeypatch):
    calls = []
    original = Dictionary.lookup_many
    monkeypatch.setattr(
        Dictionary,
        "lookup_many",
        lambda self, glyphs: calls.append(list(glyphs)) or original(self, glyphs),
    )
    result = enrich(["银行", "学习", "银行", "学习", "银行"], {}, {}, dictionary)
    assert len(calls) == 1
    assert calls[0] == ["银行", "学习"], "duplicates collapse before the query"
    assert set(result) == {"银行", "学习"}


def test_senses_are_bounded(dictionary):
    assert len(enrich(["行"], {}, {}, dictionary)["行"].english) <= 8
