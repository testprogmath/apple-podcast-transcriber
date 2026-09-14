import importlib.util
import pathlib

import pytest

from podcast_bot.reader.bkrs import RussianDictionary, plain_gloss
from podcast_bot.reader.dictionary import Dictionary
from podcast_bot.reader.enrich import BKRS, CEDICT, CONTEXTUAL, NONE, enrich

TOOL = pathlib.Path(__file__).parent.parent / "tools" / "build_bkrs_dictionary.py"
_spec = importlib.util.spec_from_file_location("build_bkrs_dictionary", TOOL)
builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(builder)

EXPORT = """﻿#NAME "大БКРС (test)"
#INDEX_LANGUAGE "Chinese"

认为
 rènwéi
 [m1]1) признать за...; принять за...[/m]
 [m1]2) полагать, считать[/m]

研究成果
 yánjiū chéngguǒ
 [m1]результаты исследований[/m]

加查
 jiāchá
 [m1][p]г.[/p] Джаца ([i]Тибетский[c] авт.[/c] р-н, КНР[/i])[/m]

学习
 xuéxí
 [m1]учиться, обучаться[*][ex]我学习汉语 я учу китайский[/ex][/*][/m]

没有定义
 méiyǒu dìngyì
"""


@pytest.fixture
def russian(tmp_path):
    source = tmp_path / "dabkrs.txt"
    source.write_text(EXPORT, encoding="utf-8")
    target = tmp_path / "bkrs.sqlite3"
    assert builder.build(source, target) == 4, "an entry without definitions is skipped"
    instance = RussianDictionary(target)
    yield instance
    instance.close()


def test_the_builder_reads_headword_pinyin_and_definitions(russian):
    entry = russian.lookup("认为")
    assert entry.pinyin == "rènwéi"
    assert entry.definitions == ("1) признать за...; принять за...; 2) полагать, считать",)


def test_a_compound_cc_cedict_lacks_is_present(russian):
    assert russian.lookup("研究成果").definitions == ("результаты исследований",)


def test_dsl_markup_is_stripped_for_display(russian):
    assert russian.lookup("加查").definitions == ("г. Джаца (Тибетский авт. р-н, КНР)",)


def test_examples_are_dropped_from_the_gloss(russian):
    assert russian.lookup("学习").definitions == ("учиться, обучаться",)
    assert "我学习汉语" not in russian.lookup("学习").definitions[0]


def test_plain_gloss_handles_markup_directly():
    assert plain_gloss("[m1]банк[/m]") == "банк"
    assert plain_gloss("[m1]а[/m]\n[m2]б[/m]") == "а; б"
    assert plain_gloss("[m1]x[/m]", limit=1) == "x"
    assert plain_gloss("") == ""


def test_an_unknown_headword_is_clean(russian):
    assert russian.lookup("辛苦了") is None
    assert russian.lookup_many([]) == {}


def test_an_unconfigured_russian_dictionary_is_inert(monkeypatch, tmp_path):
    monkeypatch.delenv("READER_DICTIONARY_RU", raising=False)
    assert RussianDictionary().available is False
    monkeypatch.setenv("READER_DICTIONARY_RU", str(tmp_path / "missing.sqlite3"))
    absent = RussianDictionary()
    assert absent.available is False
    assert absent.lookup("认为") is None


def cedict(tmp_path):
    source = tmp_path / "cedict_ts.u8"
    source.write_text(
        "# sample\n認為 认为 [ren4 wei2] /to think; to consider/\n銀行 银行 [yin2 hang2] /bank/\n",
        encoding="utf-8",
    )
    from podcast_bot.reader.cedict import build

    target = tmp_path / "cedict.sqlite3"
    build(source, target)
    return Dictionary(target)


def test_russian_beats_cc_cedict_but_never_a_contextual_meaning(russian, tmp_path):
    english = cedict(tmp_path)
    contextual = enrich(["认为"], {"认为": "считать (из урока)"}, {}, english, russian)["认为"]
    assert contextual.meaning == "считать (из урока)"
    assert contextual.meaning_source == CONTEXTUAL

    preferred = enrich(["认为"], {}, {}, english, russian)["认为"]
    assert preferred.meaning.startswith("1) признать за")
    assert preferred.meaning_source == BKRS
    english.close()


def test_cc_cedict_fills_in_where_russian_has_nothing(russian, tmp_path):
    english = cedict(tmp_path)
    lexeme = enrich(["银行"], {}, {}, english, russian)["银行"]
    assert lexeme.meaning == "bank"
    assert lexeme.meaning_source == CEDICT
    english.close()


def test_neither_source_leaves_the_meaning_absent(russian, tmp_path):
    english = cedict(tmp_path)
    lexeme = enrich(["辛苦了"], {}, {}, english, russian)["辛苦了"]
    assert lexeme.meaning == ""
    assert lexeme.meaning_source == NONE
    assert lexeme.pinyin == "xīn kǔ le", "local fallback still supplies pinyin"
    english.close()


def test_pinyin_comes_from_cc_cedict_not_the_unseparated_russian_form(russian, tmp_path):
    english = cedict(tmp_path)
    assert enrich(["认为"], {}, {}, english, russian)["认为"].pinyin == "rèn wéi"
    assert russian.lookup("认为").pinyin == "rènwéi", "the Russian source writes it unseparated"
    english.close()


def test_russian_alone_still_yields_a_fallback_pinyin(russian):
    assert enrich(["认为"], {}, {}, None, russian)["认为"].pinyin == "rèn wéi"
