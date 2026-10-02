from pathlib import Path

import pytest
from test_reader import call, item
from test_reader import lexical as lexical

from podcast_bot.reader.cedict import build
from podcast_bot.reader.dictionary import Dictionary
from podcast_bot.reader.documents import from_file, from_text
from podcast_bot.reader.enrich import enrich
from podcast_bot.reader.tokens import _cut, tokenize

SENTENCE = "春天先看牡丹花，接着就是芍药花，北京的春天多么美丽呀！"
SAMPLE = """牡 牡 [mu3] /male/
牡丹 牡丹 [mu3 dan1] /tree peony (Paeonia suffruticosa)/
芍藥 芍药 [shao2 yao4] /Chinese peony (Paeonia lactiflora)/
花 花 [hua1] /flower/
玫瑰 玫瑰 [mei2 gui1] /rose/
研究 研究 [yan2 jiu1] /research/
成果 成果 [cheng2 guo3] /result/
研究成果 研究成果 [yan2 jiu1 cheng2 guo3] /research findings/
中國 中国 [Zhong1 guo2] /China/
人民 人民 [ren2 min2] /the people/
中國人民 中国人民 [Zhong1 guo2 ren2 min2] /Chinese people/
電話 电话 [dian4 hua4] /telephone/
打電話 打电话 [da3 dian4 hua4] /to make a phone call/
沒關係 没关系 [mei2 guan1 xi5] /it does not matter/
不知不覺 不知不觉 [bu4 zhi1 bu4 jue2] /unconsciously/
看 看 [kan4] /to look/
"""


@pytest.fixture
def dictionary(tmp_path):
    source = tmp_path / "sample.u8"
    source.write_text(SAMPLE)
    path = tmp_path / "sample.sqlite3"
    build(source, path)
    d = Dictionary(path)
    yield d
    d.close()


def words(text, dictionary, known=frozenset()):
    return [t.text for t in tokenize(text, known, dictionary) if t.word]


def test_reproduce_overlay_not_jieba():
    assert "牡丹花" in _cut(SENTENCE)
    pieces = words(SENTENCE, None, frozenset({"丹花"}))
    assert pieces[pieces.index("牡") + 1] == "丹花"


def test_regression_and_bad_interior_claim(dictionary):
    for known in (frozenset(), frozenset({"丹花"})):
        tokens = tokenize(SENTENCE, known, dictionary)
        assert "牡丹花" in [t.text for t in tokens]
        assert "芍药花" in [t.text for t in tokens]
        assert "牡" not in [t.text for t in tokens]
        assert "丹花" not in [t.text for t in tokens]
        assert "".join(t.text for t in tokens) == SENTENCE
        assert all(SENTENCE[t.start : t.end] == t.text for t in tokens)


@pytest.mark.parametrize("text", ["打电话", "没关系", "不知不觉", "玫瑰花", "芍藥花"])
def test_useful_chunks(dictionary, text):
    assert words(text, dictionary) == [text]


def test_overlaps_are_not_longest_match(dictionary, monkeypatch):
    monkeypatch.setattr("podcast_bot.reader.tokens._cut", lambda s: list(s))
    assert words("中国人民", dictionary) == ["中国", "人民"]
    assert words("研究成果", dictionary, frozenset({"研究成果"})) == ["研究成果"]
    assert words("牡丹花", dictionary, frozenset({"牡丹"})) == ["牡丹", "花"]
    assert words("看花", dictionary) == ["看", "花"]
    assert words("电话花", dictionary) == ["电话", "花"]
    assert words("牡丹花", dictionary) == words("牡丹花", dictionary)


@pytest.mark.parametrize("boundary", list("，。！？；：、 ,.!?;:\t\n") + ["English", "2026", "😊"])
def test_boundaries(dictionary, boundary):
    text = "牡丹" + boundary + "花"
    tokens = tokenize(text, frozenset({text}), dictionary)
    assert "".join(t.text for t in tokens) == text
    assert all(boundary not in t.text for t in tokens if t.word)
    assert all(text[t.start : t.end] == t.text for t in tokens)


def test_meaning_precedence(dictionary):
    result = enrich(["牡丹花", "芍药花", "不存在词", "牡丹"], {}, {}, dictionary)
    assert result["牡丹花"].pinyin == "mǔ dān huā"
    assert result["芍药花"].pinyin == "sháo yào huā"
    assert result["牡丹花"].meaning_source == "compositional"
    assert result["牡丹花"].english == ("牡丹: tree peony (Paeonia suffruticosa) + 花: flower",)
    assert result["牡丹"].meaning_source == "dictionary"
    assert result["不存在词"].meaning_source == "missing"
    contextual = enrich(["牡丹花"], {"牡丹花": "пионы"}, {}, dictionary)["牡丹花"]
    assert contextual.native() == ("пионы",)
    assert contextual.meaning_source == "contextual"


@pytest.mark.parametrize("source", ["paste", "file"])
async def test_real_api_popup_upload_and_known_state(lexical, dictionary, source):
    lexical.api.dictionary = dictionary
    doc = (
        from_text(42, SENTENCE) if source == "paste" else from_file(42, SENTENCE.encode(), "a.txt")
    )
    lexical.store.save_reader_document(doc)
    status, data = await call(lexical, "GET", f"/api/reader/{doc.id}")
    assert status == 200
    sentence = data["sentences"][0]
    assert sentence["text"] == SENTENCE
    token = next(t for t in sentence["tokens"] if t["t"] == "牡丹花")
    assert SENTENCE[token["start"] : token["end"]] == "牡丹花"
    entry = data["glossary"]["牡丹花"]
    assert entry["p"] == "mǔ dān huā"
    assert entry["es"] == entry["meaning_source"] == "compositional"
    status, _ = await call(lexical, "POST", f"/api/reader/{doc.id}/hanly", item("牡丹花", 0))
    assert status == 200
    assert lexical.services.hanly.client.calls[0][2] == ["牡丹花"]
    status, _ = await call(
        lexical,
        "POST",
        f"/api/reader/{doc.id}/vocabulary-state",
        {"glyph": "牡丹花", "state": "known"},
    )
    assert status == 200


def test_pinned_dictionary_pipeline(tmp_path):
    root = Path(__file__).resolve().parents[1]
    path = tmp_path / "actual.sqlite3"
    assert build(root / "dictionary/cedict_ts.u8.gz", path) == 125061
    d = Dictionary(path)
    try:
        assert d.lookup("牡丹花") is None
        assert d.lookup("芍药花") is None
        assert d.lookup("丹花") is None
        assert "牡丹花" in words(SENTENCE, d, frozenset({"丹花"}))
        assert "芍药花" in words(SENTENCE, d)
        assert "芍藥花" in words("看芍藥花。", d)
    finally:
        d.close()


def test_weak_botanical_lookalike_does_not_compose(dictionary):
    from podcast_bot.reader.tokens import compound_parts

    entries = dictionary.lexical_entries().copy()
    entries["粉红"] = ("rose-colored",)
    assert compound_parts("粉红花", entries) is None
    entries["花"] = ("surname Hua",)
    assert compound_parts("牡丹花", entries) is None


def test_index_is_reused_and_missing_dictionary_degrades(tmp_path, dictionary):
    assert dictionary.lexical_entries() is dictionary.lexical_entries()
    empty = Dictionary(tmp_path / "missing.sqlite3")
    assert words("看花。", empty) == ["看花"]  # unchanged segmenter fallback
    assert enrich(["陌生词"], {}, {}, empty)["陌生词"].meaning_source == "missing"


def test_exact_and_contextual_meanings_beat_composition(dictionary):
    from types import SimpleNamespace

    russian = SimpleNamespace(
        lookup_many=lambda glyphs: {"牡丹花": SimpleNamespace(definitions=("пион",))}
    )
    result = enrich(["牡丹花"], {}, {}, dictionary, russian)["牡丹花"]
    assert result.native() == ("пион",)
    assert result.meaning_source == "dictionary"
    assert result.native_source == "bkrs"
    result = enrich(["牡丹花"], {"牡丹花": "цветы пиона"}, {}, dictionary, russian)["牡丹花"]
    assert result.native_source == "contextual"


def test_supported_long_candidate_beats_bad_fragments(dictionary, monkeypatch):
    monkeypatch.setattr("podcast_bot.reader.tokens._cut", lambda s: list(s))
    assert words("打电话", dictionary) == ["打电话"]
    assert words("不知不觉", dictionary) == ["不知不觉"]
    # A source-owned phrase remains stronger than generic competing dictionary words.
    assert words("中国人民", dictionary, frozenset({"中国人民"})) == ["中国人民"]
