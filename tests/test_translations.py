import pytest
from test_study import PINYIN, SOURCE, material_for

from podcast_bot.models import UserError
from podcast_bot.study.chunking import split_blocks, validate_chunk
from podcast_bot.study.translations import han_ratio, untranslated, wrong_language

SOURCE_ZH = "他们获得了菲尔兹奖。"
GOOD_RU = "Они получили премию Филдса."


def test_an_identical_translation_is_detected():
    assert untranslated(SOURCE_ZH, SOURCE_ZH, "ru")
    assert untranslated(SOURCE_ZH, " 他们获得了菲尔兹奖。 ", "ru"), (
        "whitespace does not disguise it"
    )


def test_a_chinese_paraphrase_is_detected():
    assert untranslated(SOURCE_ZH, "他们获得了数学界最高奖项之一。", "ru")


def test_a_real_translation_passes():
    assert not untranslated(SOURCE_ZH, GOOD_RU, "ru")
    assert not untranslated(SOURCE_ZH, "They received the Fields Medal.", "en")


def test_a_translation_may_quote_chinese_terms():
    assert not untranslated(SOURCE_ZH, "Они получили премию Филдса (菲尔兹奖).", "ru")
    assert han_ratio("Они получили премию Филдса (菲尔兹奖).") < 0.5


def test_chinese_output_is_allowed_when_the_native_language_is_chinese():
    assert not untranslated(SOURCE_ZH, "他们获得了数学界最高奖项之一。", "zh")


@pytest.mark.parametrize("value", ["", "   ", None])
def test_an_empty_translation_counts_as_untranslated(value):
    assert untranslated(SOURCE_ZH, value, "ru")


def test_wrong_language_flags_meanings_left_in_chinese():
    assert wrong_language("菲尔兹奖，数学界最有名的国际奖项之一。", "ru")
    assert not wrong_language("Премия Филдса — одна из самых известных наград.", "ru")
    assert not wrong_language("菲尔兹奖，数学界最有名的奖项。", "zh")


def blocks_and_material():
    blocks = split_blocks(SOURCE)
    material = material_for([{"id": b.id, "text": b.text} for b in blocks])
    assert material.vocabulary, "fixture must contain a vocabulary item to validate"
    for index, passage in enumerate(material.passages):
        passage.translation = ["Услышав это, ты подумал про себя?", "Давайте распределим роли."][
            index
        ]
        for line in passage.lines:
            line.pinyin = PINYIN[0]
    return material, blocks


def test_validation_accepts_a_properly_translated_chunk():
    material, blocks = blocks_and_material()
    validate_chunk(material, blocks, "zh", "ru")


def test_validation_rejects_a_passage_translation_that_repeats_the_source():
    material, blocks = blocks_and_material()
    material.passages[0].translation = material.passages[0].source
    with pytest.raises(UserError, match="instead of a ru-language translation"):
        validate_chunk(material, blocks, "zh", "ru")


def test_validation_rejects_an_example_translation_left_in_chinese():
    material, blocks = blocks_and_material()
    material.vocabulary[0].example_translation = material.vocabulary[0].example
    with pytest.raises(UserError, match="not translated into the configured native language"):
        validate_chunk(material, blocks, "zh", "ru")


def test_validation_rejects_a_vocabulary_meaning_left_in_chinese():
    material, blocks = blocks_and_material()
    material.vocabulary[0].meaning = "嘀咕就是心里小声说话的意思。"
    with pytest.raises(UserError, match="not written in the configured native language"):
        validate_chunk(material, blocks, "zh", "ru")


def test_validation_still_accepts_chinese_when_native_is_chinese():
    material, blocks = blocks_and_material()
    material.vocabulary[0].meaning = "嘀咕就是心里小声说话的意思。"
    material.passages[0].translation = "他们得到了菲尔兹奖项。"
    material.vocabulary[0].example_translation = "心里小声说话。"
    validate_chunk(material, blocks, "zh", "zh")
