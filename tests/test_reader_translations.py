import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_reader import call, valid_init_data
from test_reader import reader as reader

from podcast_bot.reader.documents import from_text
from podcast_bot.reader.translations import (
    MAX_CONTEXT,
    SentenceTranslations,
    TranslationResult,
    clean_translation,
)
from podcast_bot.storage import Storage

RUSSIAN = "Всем привет, добро пожаловать обратно, это Mami Chinese."


@pytest.fixture
def translation(reader):
    client = SimpleNamespace(
        request=AsyncMock(return_value=(TranslationResult(translation=RUSSIAN), {}))
    )
    service = SentenceTranslations(reader.store, client)
    reader.api.translations = service
    return SimpleNamespace(reader=reader, client=client, service=service)


def route(document, sentence_id=0):
    return f"/api/reader/{document.id}/sentences/{sentence_id}/translation"


def with_study(reader, tmp_path, pairs, native="ru"):
    (tmp_path / "study.json").write_text(
        json.dumps(
            {
                "passages": [
                    {"source": source, "translation": translated} for source, translated in pairs
                ]
            }
        )
    )
    (tmp_path / "metadata.json").write_text(
        json.dumps({"study_settings": {"native_language": native}})
    )
    document = replace(reader.document, source_reference=str(tmp_path))
    reader.store.save_reader_document(document)
    return document


async def test_exact_study_beats_cache_without_llm(translation, tmp_path):
    r = translation.reader
    text = r.document.sentences()[0].text
    r.store.save_reader_translation(r.document.id, 0, text, "Старый перевод.")
    document = with_study(r, tmp_path, [(text.replace("，", "，\n "), RUSSIAN)])
    status, data = await call(r, "POST", route(document), {})
    assert status == 200
    assert data == {"sentence_id": 0, "translation": RUSSIAN, "source": "study"}
    translation.client.request.assert_not_called()


@pytest.mark.parametrize("kind", ["different", "partial", "conflict", "english"])
async def test_uncertain_or_non_russian_study_is_not_reused(translation, tmp_path, kind):
    r = translation.reader
    source = r.document.sentences()[0].text
    pairs = [(source, "Сохранённый перевод.")]
    if kind == "different":
        pairs = [(source.replace("。", "！"), "Другой перевод.")]
    if kind == "partial":
        pairs = [(source[:3], "Неполный перевод.")]
    if kind == "conflict":
        pairs.append((source, "Противоречивый перевод."))
    document = with_study(r, tmp_path, pairs, "en" if kind == "english" else "ru")
    _, data = await call(r, "POST", route(document), {})
    assert data["source"] == "generated"
    translation.client.request.assert_awaited_once()


async def test_direct_mixed_source_generated_cached_and_persisted(translation):
    r = translation.reader
    status, first = await call(r, "POST", route(r.document), {})
    assert status == 200
    assert first["translation"] == RUSSIAN
    assert first["source"] == "generated"
    assert (await call(r, "POST", route(r.document), {}))[1] == first
    translation.client.request.assert_awaited_once()
    payload = translation.client.request.call_args.args[2]
    assert payload["TARGET"] == r.document.sentences()[0].text
    assert "Mami Chinese" in payload["TARGET"]
    assert payload["previous"] == ""
    assert payload["next"] == r.document.sentences()[1].text
    reopened = Storage(r.store.root)
    assert reopened.reader_translation(r.document.id, 0, payload["TARGET"]) == RUSSIAN
    reopened.close()
    assert (
        r.store.db.execute("SELECT count(*) FROM reader_sentence_translations").fetchone()[0] == 1
    )


async def test_stale_cache_and_neighbor_context(translation):
    r = translation.reader
    r.store.save_reader_translation(r.document.id, 2, "Другой исходник", "Неверный перевод.")
    status, _ = await call(r, "POST", route(r.document, 2), {})
    assert status == 200
    payload = translation.client.request.call_args.args[2]
    sentences = r.document.sentences()
    assert payload == {
        "previous": sentences[1].text[-MAX_CONTEXT:],
        "TARGET": sentences[2].text,
        "next": sentences[3].text[:MAX_CONTEXT],
    }
    assert r.store.reader_translation(r.document.id, 2, sentences[2].text) == RUSSIAN
    assert r.store.reader_translation(r.document.id, 2, "Другой исходник") is None


async def test_concurrent_requests_share_one_call(translation):
    r = translation.reader
    started, finish = asyncio.Event(), asyncio.Event()

    async def request(*args):
        started.set()
        await finish.wait()
        return TranslationResult(translation=RUSSIAN), {}

    translation.client.request.side_effect = request
    first = asyncio.create_task(call(r, "POST", route(r.document), {}))
    await started.wait()
    second = asyncio.create_task(call(r, "POST", route(r.document), {}))
    await asyncio.sleep(0)
    finish.set()
    assert (await first) == (await second)
    translation.client.request.assert_awaited_once()
    assert not translation.service.pending


@pytest.mark.parametrize("failure", [RuntimeError("secret detail"), TimeoutError()])
async def test_failure_is_safe_retryable_and_not_cached(translation, failure):
    r = translation.reader
    translation.client.request.side_effect = failure
    status, data = await call(r, "POST", route(r.document), {})
    assert status == 400
    assert "Retry" in data["error"] and "secret" not in data["error"]
    assert r.store.reader_translation(r.document.id, 0, r.document.sentences()[0].text) is None
    translation.client.request.side_effect = None
    assert (await call(r, "POST", route(r.document), {}))[0] == 200
    assert translation.client.request.await_count == 2


async def test_generation_has_real_timeout(translation, monkeypatch):
    monkeypatch.setattr("podcast_bot.reader.translations.TIMEOUT", 0.01)

    async def slow(*args):
        await asyncio.sleep(10)

    translation.client.request.side_effect = slow
    r = translation.reader
    assert (await call(r, "POST", route(r.document), {}))[0] == 400
    assert not translation.service.pending


@pytest.mark.parametrize("identifier", ["-1", "x", "1.2", "true", "01", "999999", "9999999"])
async def test_invalid_sentence_never_calls_model(translation, identifier):
    r = translation.reader
    status, _ = await call(r, "POST", route(r.document, identifier), {})
    assert status in {400, 404}
    translation.client.request.assert_not_called()


@pytest.mark.parametrize("payload", [{"text": "injected"}, {"sentence_id": 0}, [], None, "text"])
async def test_body_injection_and_malformed_json(translation, payload):
    r = translation.reader
    status, _, _ = await r.api.dispatch(
        "POST", route(r.document), r.headers, json.dumps(payload).encode()
    )
    assert status == 400
    translation.client.request.assert_not_called()


async def test_malformed_raw_body(translation):
    r = translation.reader
    assert (await r.api.dispatch("POST", route(r.document), r.headers, b"{"))[0] == 400
    translation.client.request.assert_not_called()


@pytest.mark.parametrize("auth", ["", "bad", valid_init_data(99), valid_init_data(age=90000)])
async def test_translation_auth(translation, auth):
    r = translation.reader
    status, _ = await call(r, "POST", route(r.document), {}, {"x-telegram-init-data": auth})
    assert status == 401
    translation.client.request.assert_not_called()


async def test_document_ownership_and_missing_document(translation):
    r = translation.reader
    other = from_text(99, "你好。")
    r.store.save_reader_document(other)
    for document in (other, replace(other, id="f" * 32)):
        assert (await call(r, "POST", route(document), {}))[0] == 404
    translation.client.request.assert_not_called()


async def test_length_bounds_and_no_eager_translation(translation):
    r = translation.reader
    assert (await call(r, "GET", f"/api/reader/{r.document.id}"))[0] == 200
    document = from_text(42, "中" * 3001 + "。")
    r.store.save_reader_document(document)
    assert (await call(r, "POST", route(document), {}))[0] == 400
    translation.client.request.assert_not_called()


@pytest.mark.parametrize("value", ["", "你好。", "English only", "Я" * 6001])
def test_bad_translation_response(value):
    with pytest.raises(ValueError):
        clean_translation("你好。", value)


def test_translation_whitespace_normalization():
    assert clean_translation("你好。", "  Привет!\n  Как дела?  ") == "Привет! Как дела?"


def test_translation_migration_is_additive(tmp_path):
    storage = Storage(tmp_path)
    document = from_text(42, "你好。")
    storage.save_reader_document(document)
    storage.db.execute("DROP TABLE reader_sentence_translations")
    storage.db.commit()
    storage.close()
    storage = Storage(tmp_path)
    assert storage.reader_document(document.id) == document
    assert storage.reader_translation(document.id, 0, "你好。") is None
    storage.close()
