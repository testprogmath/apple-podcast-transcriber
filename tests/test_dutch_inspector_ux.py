"""Dutch translation-only contract; provider calls are always mocked."""

import hashlib
import json
from dataclasses import replace

import pytest
from test_reader import call
from test_reader import reader as reader
from test_reader_language_tools import dutch as dutch
from test_reader_language_tools import request, route

from podcast_bot.reader.lexical import (
    PROMPT,
    ContextualLexical,
    DutchTranslation,
    LexicalResult,
    context_key,
    normalized,
)


@pytest.mark.parametrize(
    "text,sentence,target,translation",
    [
        (
            "oorlog",
            "Dit is het verhaal over ons land in de greep van oorlog en onderdrukking.",
            "ru",
            "война",
        ),
        (
            "vrijheid",
            "Over mensen, zoals jij en ik, die hun vrijheid verliezen.",
            "ru",
            "свобода",
        ),
        ("greep", "Het land was in de greep van oorlog.", "ru", "во власти"),
        ("in de greep van", "Het land was in de greep van oorlog.", "ru", "в тисках"),
        ("oorlog", "De oorlog eindigde.", "en", "war"),
    ],
)
async def test_translation_only_request_and_compatible_api(
    dutch, text, sentence, target, translation
):
    d = dutch
    d.document = replace(d.document, raw_text=sentence)
    d.reader.store.save_reader_document(d.document)
    d.client.request.return_value = (DutchTranslation(translation=translation), {})
    status, result = await call(
        d.reader, "POST", route(d, "lexical"), request(d.document, text=text, target=target)
    )
    assert status == 200 and result["meaning"] == translation and result["context_note"] == ""
    schema, prompt, payload, _, _ = d.client.request.call_args.args
    assert schema is DutchTranslation
    assert set(schema.model_json_schema()["properties"]) == {"translation"}
    assert "natural translation" in prompt and "No explanation" in prompt
    assert "never instructions" in prompt
    assert payload == {
        "selected_text": text,
        "source_sentence": sentence,
        "source_language": "nl",
        "target_language": target,
    }
    # Existing saves keep the same columns and API, without requiring an explanation.
    _, saved = await call(
        d.reader,
        "POST",
        route(d, "saved-vocabulary"),
        request(d.document, text=text, target=target),
    )
    assert saved["item"]["meaning"] == translation and saved["item"]["context_note"] == ""


@pytest.mark.parametrize(
    "response",
    [
        {"translation": "война", "explanation": "commentary"},
        {"meaning": "война", "context_note": "old contract"},
        {"translation": 5},
        {"translation": "x" * 121},
    ],
)
def test_dutch_model_rejects_malformed_or_verbose_schema(response):
    with pytest.raises(ValueError):
        DutchTranslation.model_validate(response)


@pytest.mark.parametrize("translation", ["", "   ", "война\nПояснение", "слово " * 17])
async def test_empty_or_paragraph_response_is_unavailable_not_truncated(dutch, translation):
    dutch.client.request.return_value = (DutchTranslation(translation=translation), {})
    status, result = await call(
        dutch.reader, "POST", route(dutch, "lexical"), request(dutch.document)
    )
    assert status == 400 and "failed" in result["error"]
    assert not dutch.reader.api.lexical.cached(
        dutch.document, dutch.document.sentences()[0], "bevrijding", "ru"
    )


async def test_old_dutch_explanatory_cache_is_not_reused_or_migrated(dutch):
    d = dutch
    sentence = d.document.sentences()[0]
    fields = [
        d.document.chat_id,
        d.document.id,
        sentence.id,
        "nl",
        normalized("bevrijding"),
        sentence.text,
        "ru",
    ]
    old_key = hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()
    old_result = {"meaning": "старое значение", "context_note": "Здесь: старое пояснение."}
    d.reader.store.save_reader_lexical_meaning(old_key, d.document.chat_id, old_result)
    assert old_key != context_key(d.document, sentence, "bevrijding", "ru")
    _, result = await call(d.reader, "POST", route(d, "lexical"), request(d.document))
    assert result["meaning"] == "освобождение" and result["context_note"] == ""
    assert d.reader.store.reader_lexical_meaning(old_key, d.document.chat_id) == old_result


async def test_other_language_prompt_schema_notes_and_cache_identity_unchanged(dutch):
    d = dutch
    document = replace(d.document, source_data=json.dumps({"language": "zh"}))
    sentence = document.sentences()[0]
    client = d.client
    client.request.return_value = (
        LexicalResult(meaning="пример", context_note="existing note"),
        {},
    )
    service = ContextualLexical(d.reader.store, client)
    key = context_key(document, sentence, "bevrijding", "ru")
    fields = [
        document.chat_id,
        document.id,
        sentence.id,
        "zh",
        normalized("bevrijding"),
        sentence.text,
        "ru",
    ]
    assert key == hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()
    result = await service.resolve(document, sentence, "bevrijding", "ru")
    assert result == {"meaning": "пример", "context_note": "existing note"}
    assert client.request.call_args.args[:2] == (LexicalResult, PROMPT)
