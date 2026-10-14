import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from test_reader import call, valid_init_data
from test_reader import reader as reader

from podcast_bot.reader.documents import from_text
from podcast_bot.reader.languages import dutch_tokens
from podcast_bot.reader.lexical import ContextualLexical, LexicalResult
from podcast_bot.reader.translations import SentenceTranslations, TranslationResult
from podcast_bot.storage import Storage

TEXT = "Met de bevrijding van Nederland komt er een einde. Het land was in de greep van oorlog."


@pytest.fixture
def dutch(reader):
    # Source-neutral: tools must work on a Dutch pasted document as well as NPO.
    document = replace(reader.document, raw_text=TEXT, source_data=json.dumps({"language": "nl"}))
    reader.store.save_reader_document(document)
    client = SimpleNamespace(
        request=AsyncMock(
            return_value=(
                LexicalResult(meaning="освобождение", context_note="Здесь: освобождение страны."),
                {},
            )
        )
    )
    reader.api.lexical = ContextualLexical(reader.store, client)
    return SimpleNamespace(reader=reader, document=document, client=client)


def request(document, text="bevrijding", sentence_index=0, target="ru"):
    sentence = document.sentences()[sentence_index]
    start = sentence.text.index(text)
    return {
        "sentence_id": sentence.id,
        "start": start,
        "end": start + len(text),
        "text": text,
        "target_language": target,
    }


def route(d, endpoint):
    return f"/api/reader/{d.document.id}/{endpoint}"


async def test_language_capabilities_and_source_spans(dutch):
    with (
        patch(
            "podcast_bot.reader.documents.segment",
            side_effect=AssertionError("Chinese segmentation"),
        ),
        patch("podcast_bot.reader.api.enrich", side_effect=AssertionError("Chinese enrichment")),
    ):
        status, result = await call(dutch.reader, "GET", f"/api/reader/{dutch.document.id}")
    assert status == 200
    assert result["capabilities"] == {
        "lexical_inspector": "contextual",
        "speech_language": "nl-NL",
        "pinyin": False,
        "hanly": False,
        "mosaic": False,
        "internal_save": True,
    }
    assert (
        not result["hanly_available"]
        and not result["mosaic_available"]
        and result["glossary"] == {}
    )
    token = next(t for t in result["sentences"][0]["tokens"] if t["t"] == "bevrijding")
    assert token["w"] and TEXT[token["start"] : token["end"]] == "bevrijding"
    dutch.client.request.assert_not_called()


@pytest.mark.parametrize(
    "text,words",
    [
        ("🙂 cafés, naïef; efficiënt!", ["cafés", "naïef", "efficiënt"]),
        ("nazi's en auto's; ’s nachts.", ["nazi's", "en", "auto's", "s", "nachts"]),
        ("Zuid-Afrika: één.", ["Zuid-Afrika", "één"]),
    ],
)
def test_dutch_token_boundaries(text, words):
    tokens = dutch_tokens(text)
    assert [t.text for t in tokens if t.word] == words
    assert "".join(t.text for t in tokens) == text
    assert all(text[t.start : t.end] == t.text for t in tokens)


async def test_lookup_contract_cache_and_context(dutch):
    d = dutch
    payload = request(d.document)
    a = await call(d.reader, "POST", route(d, "lexical"), payload)
    b = await call(d.reader, "POST", route(d, "lexical"), payload)
    assert a == b and a[0] == 200
    assert d.client.request.await_count == 1
    schema, instructions, data, model, budget = d.client.request.call_args.args
    assert schema is LexicalResult and "entire sentence" in instructions
    assert data == {
        "selected_text": "bevrijding",
        "source_sentence": d.document.sentences()[0].text,
        "source_language": "nl",
        "target_language": "ru",
    }
    assert budget == 900
    other = replace(d.document, raw_text="De bevrijding was onverwacht.")
    d.reader.store.save_reader_document(other)
    await call(d.reader, "POST", route(d, "lexical"), request(other))
    assert d.client.request.await_count == 2
    # Both caches survive a storage reconnect.
    restored = Storage(d.reader.store.root)
    assert ContextualLexical(restored).cached(other, other.sentences()[0], "bevrijding", "ru")
    restored.close()


async def test_coalesced_lookup(dutch):
    d = dutch
    payload = request(d.document)
    results = await asyncio.gather(
        *(call(d.reader, "POST", route(d, "lexical"), payload) for _ in range(3))
    )
    assert all(r[0] == 200 for r in results) and d.client.request.await_count == 1


@pytest.mark.parametrize(
    "bad",
    [{"meaning": "x", "extra": "bad"}, {"meaning": 23}, {"meaning": ""}, {"meaning": "x" * 201}],
)
def test_model_schema_rejects_malformed(bad):
    with pytest.raises(ValueError):
        LexicalResult.model_validate(bad)


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("private secret"),
        LexicalResult(meaning="  "),
        LexicalResult(meaning="English only"),
    ],
)
async def test_lookup_failure_safe_and_retryable(dutch, failure):
    if isinstance(failure, Exception):
        dutch.client.request.side_effect = failure
    else:
        dutch.client.request.return_value = (failure, {})
    status, result = await call(
        dutch.reader, "POST", route(dutch, "lexical"), request(dutch.document)
    )
    assert status == 400 and "private secret" not in result["error"]
    assert (await call(dutch.reader, "GET", f"/api/reader/{dutch.document.id}"))[0] == 200
    assert (
        await call(dutch.reader, "POST", route(dutch, "saved-vocabulary"), request(dutch.document))
    )[0] == 200
    dutch.client.request.side_effect = None
    dutch.client.request.return_value = (LexicalResult(meaning="освобождение"), {})
    assert (await call(dutch.reader, "POST", route(dutch, "lexical"), request(dutch.document)))[
        0
    ] == 200


async def test_saved_word_provenance_idempotency_and_reopen(dutch):
    d = dutch
    await call(d.reader, "POST", route(d, "lexical"), request(d.document))
    status, a = await call(d.reader, "POST", route(d, "saved-vocabulary"), request(d.document))
    _, b = await call(d.reader, "POST", route(d, "saved-vocabulary"), request(d.document))
    item = a["item"]
    assert status == 200 and item["id"] == b["item"]["id"]
    assert (
        item["owner_id"] == 42 and item["language"] == "nl" and item["document_id"] == d.document.id
    )
    assert item["source_sentence"] == d.document.sentences()[0].text
    assert item["text"] == item["normalized_text"] == "bevrijding"
    assert item["meaning"] == "освобождение" and item["context_note"]
    assert item["created"] == b["item"]["created"]
    _, lookup = await call(d.reader, "POST", route(d, "lexical"), request(d.document))
    assert lookup["saved"]
    _, read = await call(d.reader, "GET", f"/api/reader/{d.document.id}")
    assert len(read["saved_vocabulary"]) == 1
    restored = Storage(d.reader.store.root)
    assert restored.saved_vocabulary(42, d.document.id)[0]["id"] == item["id"]
    restored.close()


async def test_same_surface_different_sentence_not_collapsed(dutch):
    d = dutch
    doc = replace(d.document, raw_text="De greep was sterk. In de greep van oorlog.")
    d.reader.store.save_reader_document(doc)
    for i in [0, 1]:
        await call(d.reader, "POST", route(d, "saved-vocabulary"), request(doc, "greep", i))
    assert len(d.reader.store.saved_vocabulary(42, doc.id)) == 2


async def test_phrase_exact_source_trimming_and_unchanged_tokens(dutch):
    d = dutch
    before = d.document.tokens(d.document.sentences())
    span = request(d.document, " in de greep van ", 1)
    status, result = await call(d.reader, "POST", route(d, "lexical"), span)
    assert status == 200 and result["text"] == "in de greep van"
    assert d.client.request.call_args.args[2]["selected_text"] == "in de greep van"
    _, saved = await call(d.reader, "POST", route(d, "saved-vocabulary"), span)
    assert saved["item"]["text"] == "in de greep van"
    assert saved["item"]["source_sentence"] == d.document.sentences()[1].text
    assert before == d.reader.store.reader_document(d.document.id).tokens(d.document.sentences())
    assert d.reader.store.reader_lexical_overrides(d.document.id) == {}


@pytest.mark.parametrize(
    "mutation",
    [
        {"text": "invented"},
        {"start": -1},
        {"end": 999},
        {"sentence_id": 999},
        {"source_sentence": "forged"},
        {"start": True},
        {"target_language": []},
        {"target_language": "fr"},
    ],
)
async def test_invalid_or_forged_span_refused(dutch, mutation):
    payload = {**request(dutch.document), **mutation}
    for endpoint in ["lexical", "saved-vocabulary"]:
        status, _ = await call(dutch.reader, "POST", route(dutch, endpoint), payload)
        assert status in {400, 409}
    dutch.client.request.assert_not_called()


async def test_other_owner_cannot_lookup_save_or_read(dutch):
    foreign = replace(dutch.document, id="b" * 32, chat_id=99)
    dutch.reader.store.save_reader_document(foreign)
    for endpoint, method, payload in [
        ("lexical", "POST", request(foreign)),
        ("saved-vocabulary", "POST", request(foreign)),
        ("saved-vocabulary", "GET", None),
    ]:
        status, _ = await call(
            dutch.reader, method, f"/api/reader/{foreign.id}/{endpoint}", payload
        )
        assert status == 404
    assert dutch.reader.store.saved_vocabulary(99, dutch.document.id) == []
    assert (
        await call(
            dutch.reader,
            "POST",
            route(dutch, "lexical"),
            request(dutch.document),
            headers={"x-telegram-init-data": valid_init_data(99)},
        )
    )[0] == 401
    dutch.client.request.assert_not_called()


async def test_dutch_timing_saved_from_npo(dutch):
    from test_reader_npo import URL, client

    from podcast_bot.reader.npo import NpoSourceAdapter

    async with client() as c:
        doc = await NpoSourceAdapter(c).import_document(42, URL)
    dutch.reader.store.save_reader_document(doc)
    _, result = await call(
        dutch.reader, "POST", f"/api/reader/{doc.id}/saved-vocabulary", request(doc)
    )
    assert result["item"]["source_start"] == 9.4 and result["item"]["source_end"] == 16.04
    reloaded = dutch.reader.store.reader_document(doc.id)
    assert reloaded.sentences()[0].source_end == 16.04


async def test_dutch_sentence_translation_still_cached(dutch):
    client = SimpleNamespace(
        request=AsyncMock(
            return_value=(
                TranslationResult(translation="С освобождением Нидерландов наступает конец."),
                {},
            )
        )
    )
    dutch.reader.api.translations = SentenceTranslations(dutch.reader.store, client)
    sentence = dutch.document.sentences()[0]
    path = f"/api/reader/{dutch.document.id}/sentences/{sentence.id}/translation"
    a = await call(dutch.reader, "POST", path, {"language": "ru"})
    b = await call(dutch.reader, "POST", path, {"language": "ru"})
    assert a == b and a[0] == 200 and client.request.await_count == 1
    dutch.client.request.assert_not_called()


async def test_chinese_capabilities_and_exports_unchanged(reader):
    status, data = await call(reader, "GET", f"/api/reader/{reader.document.id}")
    assert (
        status == 200
        and data["capabilities"]["pinyin"]
        and data["capabilities"]["hanly"]
        and data["capabilities"]["mosaic"]
    )
    assert data["glossary"]
    assert (
        await call(
            reader,
            "POST",
            f"/api/reader/{reader.document.id}/lexical",
            request(reader.document, "大家好"),
        )
    )[0] == 400


@pytest.mark.parametrize(
    "endpoint", ["hanly", "mandarin-mosaic", "lexical-overrides", "selection", "vocabulary-state"]
)
async def test_dutch_cannot_enter_chinese_actions(dutch, endpoint):
    assert (await call(dutch.reader, "POST", route(dutch, endpoint), {}))[0] == 400


@pytest.mark.parametrize("source", ["pasted_text", "file"])
async def test_dutch_source_neutral_creation_api(reader, source):
    import base64

    payload = {"source_type": source, "language": "nl"}
    if source == "pasted_text":
        payload["text"] = "Het land was in de greep van oorlog."
    else:
        payload.update(
            filename="dutch.txt",
            content=base64.b64encode(b"Het land was in de greep van oorlog.").decode(),
        )
    status, data = await call(reader, "POST", "/api/reader/documents", payload)
    assert status == 200
    doc = reader.store.reader_document(data["id"])
    assert doc.language == "nl" and doc.source_type == source
    assert any(t.word and t.text == "greep" for t in doc.tokens(doc.sentences())[0])


@pytest.mark.parametrize("language", ["fr", [], None, True])
async def test_invalid_document_language(reader, language):
    status, _ = await call(
        reader,
        "POST",
        "/api/reader/documents",
        {"source_type": "pasted_text", "text": "Hallo.", "language": language},
    )
    assert status == 400


async def test_target_specific_cache_and_saved_state(dutch):
    d = dutch
    await call(d.reader, "POST", route(d, "lexical"), request(d.document))
    d.client.request.return_value = (
        LexicalResult(meaning="liberation", context_note="Liberation of the country."),
        {},
    )
    english = request(d.document, target="en")
    status, result = await call(d.reader, "POST", route(d, "lexical"), english)
    assert status == 200 and result["meaning"] == "liberation" and d.client.request.await_count == 2
    await call(d.reader, "POST", route(d, "lexical"), english)
    assert d.client.request.await_count == 2
    _, saved = await call(d.reader, "POST", route(d, "saved-vocabulary"), english)
    assert saved["item"]["target_language"] == "en" and saved["item"]["meaning"] == "liberation"


def test_combining_marks_preserve_word_boundary():
    text = "Een cafe\u0301."
    assert [t.text for t in dutch_tokens(text) if t.word] == ["Een", "cafe\u0301"]


async def test_migration_preserves_existing_chinese_data(tmp_path):
    import sqlite3

    old = Storage(tmp_path)
    document = from_text(42, "你好。")
    old.save_reader_document(document)
    old.save_reader_translation(document.id, document.sentences()[0].id, "你好。", "Привет.")
    old.close()
    # Simulate a pre-feature database: only the two new tables are absent.
    db = sqlite3.connect(tmp_path / "bot.sqlite3")
    db.execute("DROP TABLE reader_contextual_lexicon")
    db.execute("DROP TABLE saved_vocabulary")
    db.commit()
    db.close()
    migrated = Storage(tmp_path)
    assert migrated.reader_document(document.id) == document
    assert (
        migrated.reader_translation(document.id, document.sentences()[0].id, "你好。") == "Привет."
    )
    assert migrated.saved_vocabulary(42, document.id) == []
    migrated.close()
