import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from test_study import SOURCE, material_for, vocabulary

from podcast_bot.study.chunking import split_blocks, validate_chunk
from podcast_bot.study.client import OpenAIStudyClient, StudyRequests
from podcast_bot.study.lexical import LexicalChunk, validate_lexical
from podcast_bot.study.models import chunk_schema
from podcast_bot.study.quotes import QUOTE_FIELDS, SourceQuotes, catalogue
from podcast_bot.study.settings import StudySettings, study_key


def fixture_data():
    blocks = [{"id": b.id, "text": b.text} for b in split_blocks(SOURCE)]
    data = material_for(blocks).model_dump()
    data["patterns"] = [
        dict(
            pattern="会不会",
            meaning="question",
            usage="question",
            example=blocks[0]["text"],
            example_translation="translation",
        )
    ]
    note = dict(
        title="note",
        explanation="meaning",
        example=blocks[0]["text"],
        example_translation="translation",
        recommendation="recognize",
    )
    data["pragmatics"] = [note.copy()]
    data["cultural_references"] = [note.copy()]
    data["possible_asr_errors"] = [
        dict(
            original=blocks[0]["text"],
            suggested_correction="suggestion",
            confidence=0.1,
            reason="uncertain",
        )
    ]
    data["mosaic_sentences"] = [
        dict(
            chinese=blocks[0]["text"],
            english="translation",
            reason="useful",
            source_start=None,
            source_end=None,
        )
    ]
    for name, (source, ref) in QUOTE_FIELDS.items():
        for item in data[name]:
            del item[source]
            item[ref] = 0
    return blocks, data


def test_all_quotes_resolve_from_source_and_schema_forbids_model_quotes():
    blocks, data = fixture_data()
    quotes = SourceQuotes(chunk_schema(2), {"blocks": blocks})
    wire = quotes.schema.model_validate(data)
    result = quotes.resolve(wire)
    validate_chunk(result, split_blocks(SOURCE), "zh")
    for name, (source, ref) in QUOTE_FIELDS.items():
        assert getattr(getattr(result, name)[0], source) == blocks[0]["text"]
        properties = (
            quotes.schema.model_fields[name]
            .annotation.__args__[0]
            .model_json_schema()["properties"]
        )
        assert source not in properties and ref in properties
    assert result.possible_asr_errors[0].suggested_correction == "suggestion"
    assert SOURCE == "听到这儿，你心里会不会嘀咕一句？\n\n咱们分工合作吧。\n"


@pytest.mark.parametrize("mutation", ["unknown_id", "invented_quote"])
def test_invalid_references_and_free_text_rejected(mutation):
    blocks, data = fixture_data()
    if mutation == "unknown_id":
        data["vocabulary"][0]["example_id"] = 999
    else:
        data["vocabulary"][0]["example"] = "invented"
    quotes = SourceQuotes(chunk_schema(2), {"blocks": blocks})
    with pytest.raises(ValidationError):
        quotes.schema.model_validate(data)


@pytest.mark.parametrize(
    "text",
    [
        "你好！谢谢。",
        "A  useful phrase. Another example!",
        "Ein gutes Beispiel. Noch eines?",
        "Een mooi voorbeeld. Nog een!",
        "字" * 1200,
        "你好\n世界！",
    ],
)
def test_catalogue_never_changes_source(text):
    blocks = [{"id": 7, "text": text}]
    segments = catalogue(blocks)
    assert [s["id"] for s in segments] == list(range(len(segments)))
    assert all(s["text"] in text and len(s["text"]) <= 400 for s in segments)
    assert all(s["block_id"] == 7 for s in segments)
    assert "".join(s["text"].replace(" ", "").replace("\n", "") for s in segments) == text.replace(
        " ", ""
    ).replace("\n", "")


@pytest.mark.parametrize(
    "text,term",
    [
        ("Das ist erstaunlich!", "erstaunlich"),
        ("Dat is opmerkelijk!", "opmerkelijk"),
        ("That is remarkable!", "remarkable"),
    ],
)
def test_non_chinese_quotes(text, term):
    blocks = [{"id": 0, "text": text}]
    quotes = SourceQuotes(LexicalChunk, {"blocks": blocks})
    item = vocabulary().model_dump(exclude={"pinyin", "example"})
    item.update(term=term, example_id=0)
    result = quotes.resolve(quotes.schema.model_validate({"vocabulary": [item]}))
    validate_lexical(result, [SimpleNamespace(text=text)])
    assert result.vocabulary[0].example == text


async def test_client_resolves_before_checkpoint_without_additional_requests(store):
    blocks, data = fixture_data()

    async def parse(**kwargs):
        assert "quote_segments" in kwargs["input"][1]["content"]
        return SimpleNamespace(
            status="completed", output_parsed=kwargs["text_format"].model_validate(data), usage=None
        )

    parse_mock = AsyncMock(side_effect=parse)
    client = OpenAIStudyClient(SimpleNamespace(responses=SimpleNamespace(parse=parse_mock)))
    requests = StudyRequests(store, client)
    args = dict(
        job_id=1,
        key="quote-test",
        step="chunk-0",
        model="gpt-5.4-mini",
        schema=chunk_schema(2),
        instructions="study",
        payload={"blocks": blocks},
        validate=lambda m: validate_chunk(m, split_blocks(SOURCE), "zh"),
    )
    first = await requests.call(**args)
    second = await requests.call(**args)
    assert first == second and parse_mock.await_count == 1
    saved = store.db.execute("select result from study_steps where key='quote-test'").fetchone()[0]
    assert "example_id" not in saved and "example" in saved


def test_old_job_settings_still_get_new_cache_namespace():
    settings = StudySettings(prompt_version="study-v2-mosaic")
    old = hashlib.sha256(SOURCE.encode() + b"\0" + settings.to_json().encode()).hexdigest()
    assert study_key(SOURCE.encode(), settings) != old
