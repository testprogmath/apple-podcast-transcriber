"""Reader exports prepare English before any Mosaic mutation; all clients are mocked."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_reader import call
from test_reader import reader as reader

from podcast_bot.models import UserError
from podcast_bot.reader.translations import SentenceTranslations, TranslationResult


def translator(reader, *results):
    client = SimpleNamespace(
        request=AsyncMock(
            side_effect=[
                value
                if isinstance(value, Exception)
                else (TranslationResult(translation=value), {})
                for value in results
            ]
        )
    )
    reader.api.translations = SentenceTranslations(reader.store, client)
    return client.request


async def upload(reader, ids=(0, 2)):
    return await call(
        reader,
        "POST",
        f"/api/reader/{reader.document.id}/mandarin-mosaic",
        {"sentence_ids": list(ids)},
    )


def snapshots(reader):
    return [tuple(row) for row in reader.store.db.execute("SELECT * FROM mosaic_sentences")]


async def test_generates_english_even_when_russian_is_cached(reader):
    sentence = reader.document.sentences()[0]
    reader.store.save_reader_translation(
        reader.document.id, sentence.id, sentence.text, "Всем привет!", "ru"
    )
    request = translator(
        reader, "Hello everyone, welcome back to Mami Chinese.", "They received the Fields Medal."
    )
    assert (await upload(reader))[0] == 200
    batch = reader.services.mosaic.client.batches[0]
    assert [s["English"] for s in batch] == [
        "Hello everyone, welcome back to Mami Chinese.",
        "They received the Fields Medal.",
    ]
    assert [s["Mandarin"] for s in batch] == [reader.document.sentences()[i].text for i in (0, 2)]
    assert request.await_count == 2
    assert all("natural English" in c.args[1] for c in request.await_args_list)
    original = snapshots(reader)
    assert (await upload(reader))[0] == 200
    assert snapshots(reader) == original
    assert request.await_count == 2
    assert len(reader.services.mosaic.client.batches) == 1


async def test_partial_translation_failure_sends_nothing_and_retry_reuses_cache(reader):
    request = translator(
        reader, "Welcome back.", RuntimeError("provider down"), "They received the Fields Medal."
    )
    assert (await upload(reader))[0] == 502
    assert not snapshots(reader)
    assert not reader.store.db.execute("SELECT * FROM mosaic_packs").fetchall()
    assert not reader.services.mosaic.client.packs
    assert not reader.services.mosaic.client.batches
    assert (await upload(reader))[0] == 200
    assert request.await_count == 3
    assert [s["English"] for s in reader.services.mosaic.client.batches[0]] == [
        "Welcome back.",
        "They received the Fields Medal.",
    ]


@pytest.mark.parametrize(
    "translation", ["", "   ", "Всем привет!", "大家好，欢迎回来，Mami Chinese。"]
)
async def test_invalid_translation_blocks_mosaic(reader, translation):
    translator(reader, translation)
    assert (await upload(reader, (0,)))[0] == 502
    assert not snapshots(reader)
    assert not reader.services.mosaic.client.packs
    assert not reader.services.mosaic.client.batches


async def test_english_cache_works_without_translation_client(reader):
    sentence = reader.document.sentences()[0]
    reader.store.save_reader_translation(
        reader.document.id, sentence.id, sentence.text, "Welcome back.", "en"
    )
    assert (await upload(reader, (0,)))[0] == 200
    assert reader.services.mosaic.client.batches[0][0]["English"] == "Welcome back."


async def test_pending_retry_preserves_original_payload_without_retranslation(reader):
    service = reader.services.mosaic
    text = reader.document.sentences()[0].text
    service.stage(reader.document.mosaic_key, "Existing pack", "Description", [(text, "Welcome.")])
    original = json.loads(snapshots(reader)[0][2])
    request = translator(reader)
    assert (await upload(reader, (0,)))[0] == 200
    assert service.client.batches[0] == [original]
    request.assert_not_awaited()


@pytest.mark.parametrize("status", ["pending", "uploaded"])
async def test_legacy_blank_records_are_not_repaired(reader, status):
    service = reader.services.mosaic
    text = reader.document.sentences()[0].text
    service.stage(reader.document.mosaic_key, "Existing pack", "Description", [(text, "")])
    with reader.store.db:
        reader.store.db.execute("UPDATE mosaic_sentences SET status=?", (status,))
        reader.store.db.execute("UPDATE mosaic_packs SET status='created'")
    original = snapshots(reader)
    request = translator(reader)
    response, data = await upload(reader, (0,))
    assert response == (502 if status == "pending" else 200)
    if status == "pending":
        assert "no English translation" in data["error"]
    assert snapshots(reader) == original
    assert not service.client.packs
    assert not service.client.batches
    request.assert_not_awaited()


async def test_service_refuses_new_blank_payload_before_staging(reader):
    with pytest.raises(UserError, match="English translation is required"):
        await reader.services.mosaic.upload_sentences_for(
            reader.document.mosaic_key, "Title", "Description", [("你好。", " ")]
        )
    assert not snapshots(reader)
    assert not reader.services.mosaic.client.packs


async def test_exact_study_english_needs_no_generation(reader, monkeypatch):
    text = reader.document.sentences()[0].text
    monkeypatch.setattr(
        type(reader.document),
        "material",
        lambda self: {"mosaic_sentences": [{"chinese": text, "english": "Welcome back."}]},
    )
    request = translator(reader)
    assert (await upload(reader, (0,)))[0] == 200
    assert reader.services.mosaic.client.batches[0][0]["English"] == "Welcome back."
    request.assert_not_awaited()
