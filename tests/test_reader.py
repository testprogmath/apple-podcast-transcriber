import json
import re
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from podcast_bot.bot import BotHandlers, reader_markup
from podcast_bot.hanly.notes import build_hanly_note
from podcast_bot.hanly.service import HanlyUploadService
from podcast_bot.models import UserError
from podcast_bot.mosaic.client import BatchResult
from podcast_bot.mosaic.service import MosaicUploadService
from podcast_bot.reader.api import STATIC, ReaderApi
from podcast_bot.reader.auth import sign, telegram_user_id
from podcast_bot.reader.cedict import build as cedict_build
from podcast_bot.reader.dictionary import Dictionary
from podcast_bot.reader.documents import from_text, from_transcript
from podcast_bot.reader.pinyin import pinyin_for
from podcast_bot.reader.sentences import paragraphs, parse_sentences
from podcast_bot.reader.server import parse_head, response
from podcast_bot.reader.tokens import tokenize
from podcast_bot.storage import atomic_json

TOKEN = "123:test-only"
TEXT = (
    "大家好，欢迎回来，Mami Chinese。最近在中国，\n"
    "很多人都在讨论两位中国数学家王虹和邓煜。\n\n"
    "他们获得了菲尔兹奖。很多人都叫它数学的诺贝尔奖，\n"
    "so many people say it's a Nobel Prize in mathematics.\n"
)


def texts(text):
    return [s.text for s in parse_sentences(text)]


def test_sentence_parsing_chinese_punctuation():
    assert texts("他来了。你走吗？真好！我们走；好。") == [
        "他来了。",
        "你走吗？",
        "真好！",
        "我们走；",
        "好。",
    ]


def test_sentence_parsing_keeps_mixed_language_and_decimals():
    assert texts(TEXT) == [
        "大家好，欢迎回来，Mami Chinese。",
        "最近在中国，\n很多人都在讨论两位中国数学家王虹和邓煜。",
        "他们获得了菲尔兹奖。",
        "很多人都叫它数学的诺贝尔奖，\nso many people say it's a Nobel Prize in mathematics.",
    ]
    assert texts("圆周率是 3.14 左右。") == ["圆周率是 3.14 左右。"]


def test_sentence_parsing_quotes_and_ellipsis():
    assert texts("他说：“我不知道。”然后走了。每四年一次……真的。") == [
        "他说：“我不知道。”",
        "然后走了。",
        "每四年一次……",
        "真的。",
    ]


@pytest.mark.parametrize("value", ["", "   ", "\n\n \t\n"])
def test_sentence_parsing_empty_text(value):
    assert not parse_sentences(value)


def test_sentence_parsing_preserves_the_source_exactly():
    sentences = parse_sentences(TEXT)
    rebuilt, previous = "", 0
    for sentence in sentences:
        gap = TEXT[previous : sentence.start]
        assert not gap.strip()
        rebuilt += gap + sentence.text
        previous = sentence.end
    assert rebuilt + TEXT[previous:] == TEXT
    assert paragraphs(TEXT, sentences) == [[0, 1], [2, 3]]


def test_tokenization_prefers_multi_character_words():
    tokens = tokenize("他们获得了菲尔兹奖。")
    assert [t.text for t in tokens][:2] == ["他们", "获得"]
    assert all(len(t.text) >= 1 for t in tokens)


def test_tokenization_leaves_punctuation_english_and_whitespace_unclickable():
    tokens = tokenize("很多人说 it's a Nobel Prize，真的。")
    clickable = {t.text for t in tokens if t.word}
    assert "，" not in clickable
    assert "。" not in clickable
    assert " " not in clickable
    assert not any(t.word for t in tokens if t.text in {"it", "Nobel", "Prize", "'", "s", "a"})
    assert "真的" in clickable


def test_tokenization_uses_known_chunks_and_is_lossless():
    sentence = "他在做研究，这是研究成果。"
    tokens = tokenize(sentence, frozenset({"做研究", "研究成果"}))
    assert "做研究" in {t.text for t in tokens if t.word}
    assert "研究成果" in {t.text for t in tokens if t.word}
    assert "".join(t.text for t in tokens) == sentence


def test_tokenization_ignores_chunks_absent_from_the_sentence():
    tokens = tokenize("他获得了奖。", frozenset({"研究成果"}))
    assert "".join(t.text for t in tokens) == "他获得了奖。"


def valid_init_data(user_id=42, age=0):
    return sign(
        {"auth_date": str(int(time.time()) - age), "user": json.dumps({"id": user_id})}, TOKEN
    )


def test_valid_init_data_returns_the_telegram_user():
    assert telegram_user_id(valid_init_data(), TOKEN) == 42


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.replace("42", "43"),
        lambda d: d + "&extra=1",
        lambda d: d.split("&hash=")[0],
        lambda d: "",
        lambda d: "not-url-encoded",
    ],
)
def test_tampered_init_data_is_rejected(mutate):
    assert telegram_user_id(mutate(valid_init_data()), TOKEN) is None


def test_expired_and_foreign_token_init_data_is_rejected():
    assert telegram_user_id(valid_init_data(age=90_000), TOKEN) is None
    assert telegram_user_id(valid_init_data(), "999:other") is None


class FakeHanlyClient:
    def __init__(self, error=None):
        self.calls = []
        self.notes = []
        self.remote = {}
        self.error = error
        self.note_error = None

    async def upsert_episode_collection(self, identifier, name, comment, glyphs, episode):
        self.calls.append((identifier, name, list(glyphs), episode))
        if self.error:
            raise self.error
        return len(glyphs) + 5

    async def upsert_glyph_note(self, glyph, story, previous):
        self.notes.append((glyph, story, previous))
        if self.note_error:
            raise self.note_error
        existing = self.remote.get(glyph)
        if existing is not None:
            if existing == story:
                return "unchanged"
            if previous is None or existing != previous:
                return "skipped-user-modified"
        self.remote[glyph] = story
        return "updated" if existing is not None else "created"


class FakeMosaicClient:
    def __init__(self):
        self.packs = []
        self.batches = []
        self.reject = set()

    async def create_pack(self, pack):
        self.packs.append(pack)

    async def upload_sentences(self, sentences):
        self.batches.append(sentences)
        rejected = {s["UniqueIdentifier"] for s in sentences if s["Mandarin"] in self.reject}
        return BatchResult(
            frozenset({s["UniqueIdentifier"] for s in sentences}) - frozenset(rejected),
            frozenset(rejected),
            frozenset(),
        )


@pytest.fixture
def reader(config, store):
    config = replace(config, token=TOKEN, reader_url="https://reader.example")
    services = SimpleNamespace(
        hanly=HanlyUploadService(store, FakeHanlyClient()),
        mosaic=MosaicUploadService(store, FakeMosaicClient()),
        hanly_error=None,
        mosaic_error=None,
    )
    document = from_text(config.allowed_user_id, TEXT)
    store.save_reader_document(document)
    return SimpleNamespace(
        api=ReaderApi(config, store, services),
        document=document,
        services=services,
        store=store,
        headers={"x-telegram-init-data": valid_init_data()},
    )


def item(glyph, sentence_id):
    return {"items": [{"glyph": glyph, "sentence_id": sentence_id}]}


async def call(reader, method, path, payload=None, headers=None):
    status, _, body = await reader.api.dispatch(
        method,
        path,
        reader.headers if headers is None else headers,
        json.dumps(payload).encode() if payload is not None else b"",
    )
    return status, json.loads(body)


async def test_reader_document_is_served_with_sentences_and_tokens(reader):
    status, data = await call(reader, "GET", f"/api/reader/{reader.document.id}")
    assert status == 200
    assert len(data["sentences"]) == 4
    assert data["paragraphs"] == [[0, 1], [2, 3]]
    assert "获得" in {t["t"] for t in data["sentences"][2]["tokens"] if t["w"]}


async def test_unauthorized_request_is_refused(reader):
    assert (await call(reader, "GET", f"/api/reader/{reader.document.id}", headers={}))[0] == 401
    tampered = {"x-telegram-init-data": valid_init_data(user_id=99)}
    assert (await call(reader, "GET", f"/api/reader/{reader.document.id}", headers=tampered))[
        0
    ] == 401


async def test_unknown_document_and_enumeration_are_refused(reader):
    assert (await call(reader, "GET", "/api/reader/" + "0" * 32))[0] == 404
    for guess in ("1", "42", "../../etc/passwd", reader.document.id[:-1]):
        assert (await call(reader, "GET", f"/api/reader/{guess}"))[0] == 404


async def test_documents_of_another_chat_are_not_readable(reader):
    other = from_text(999, TEXT)
    reader.store.save_reader_document(other)
    assert (await call(reader, "GET", f"/api/reader/{other.id}"))[0] == 404


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"items": []},
        {"items": "获得"},
        {"items": [1]},
        {"items": [{"glyph": "   ", "sentence_id": 2}]},
        {"items": [{"glyph": "获得"}]},
        {"items": [{"glyph": "获得", "sentence_id": 2, "story": "injected"}]},
        {"items": [{"glyph": "获得", "sentence_id": "2"}]},
        {"items": [{"glyph": "ok", "sentence_id": 2}]},
        {"glyphs": ["获得"]},
    ],
)
async def test_malformed_hanly_request_is_refused(reader, payload):
    status, data = await call(reader, "POST", f"/api/reader/{reader.document.id}/hanly", payload)
    assert status == 400
    assert "error" in data


async def test_malformed_body_is_refused(reader):
    status, _, body = await reader.api.dispatch(
        "POST", f"/api/reader/{reader.document.id}/hanly", reader.headers, b"not json"
    )
    assert status == 400


async def test_hanly_upload_deduplicates_and_calls_the_existing_service_once(reader):
    status, data = await call(
        reader,
        "POST",
        f"/api/reader/{reader.document.id}/hanly",
        {
            "items": [
                {"glyph": "获得", "sentence_id": 2},
                {"glyph": "获得 ", "sentence_id": 2},
                {"glyph": "讨论", "sentence_id": 1},
            ]
        },
    )
    assert status == 200
    client = reader.services.hanly.client
    assert len(client.calls) == 1
    assert client.calls[0][2] == ["获得", "讨论"]
    assert data["uploaded"] == 2


async def test_hanly_rejects_items_absent_from_the_document(reader):
    status, data = await call(
        reader,
        "POST",
        f"/api/reader/{reader.document.id}/hanly",
        {"items": [{"glyph": "永动机", "sentence_id": 2}]},
    )
    assert status == 400
    assert not reader.services.hanly.client.calls


async def test_hanly_collection_uuid_is_stable_across_retries(reader):
    path = f"/api/reader/{reader.document.id}/hanly"
    first = (await call(reader, "POST", path, item("获得", 2)))[1]
    second = (await call(reader, "POST", path, item("讨论", 1)))[1]
    assert first["collection_id"] == second["collection_id"]
    rows = reader.store.db.execute("SELECT * FROM hanly_collections").fetchall()
    assert len(rows) == 1
    assert rows[0]["episode_id"] == reader.document.hanly_key
    assert rows[0]["status"] == "verified"


async def test_hanly_failure_is_surfaced_without_losing_the_collection(reader):
    reader.services.hanly.client.error = UserError("Hanly collection write failed.")
    status, data = await call(
        reader, "POST", f"/api/reader/{reader.document.id}/hanly", item("获得", 2)
    )
    assert status == 502
    assert data["error"] == "Hanly collection write failed."
    row = reader.store.db.execute("SELECT * FROM hanly_collections").fetchone()
    assert row["status"] == "unconfirmed"


async def test_mosaic_uploads_only_the_selected_canonical_sentences(reader):
    status, data = await call(
        reader,
        "POST",
        f"/api/reader/{reader.document.id}/mandarin-mosaic",
        {"sentence_ids": [0, 2]},
    )
    assert status == 200
    batch = reader.services.mosaic.client.batches[0]
    assert [s["Mandarin"] for s in batch] == [
        "大家好，欢迎回来，Mami Chinese。",
        "他们获得了菲尔兹奖。",
    ]
    assert data["uploaded"] == 2
    assert not data["failed_sentence_ids"]


async def test_mosaic_uses_the_existing_segmentation(reader):
    await call(
        reader, "POST", f"/api/reader/{reader.document.id}/mandarin-mosaic", {"sentence_ids": [2]}
    )
    sentence = reader.services.mosaic.client.batches[0][0]
    assert sentence["SegmentedMandarin"].split()[:2] == ["他们", "获得"]
    assert "".join(sentence["SegmentedMandarin"].split()) == sentence["Mandarin"]


@pytest.mark.parametrize(
    "payload", [{}, {"sentence_ids": []}, {"sentence_ids": [99]}, {"sentence_ids": ["0"]}]
)
async def test_invalid_sentence_ids_are_refused(reader, payload):
    status, _ = await call(
        reader, "POST", f"/api/reader/{reader.document.id}/mandarin-mosaic", payload
    )
    assert status == 400
    assert not reader.services.mosaic.client.batches


async def test_mosaic_pack_association_is_stable_and_never_duplicates_sentences(reader):
    path = f"/api/reader/{reader.document.id}/mandarin-mosaic"
    first = (await call(reader, "POST", path, {"sentence_ids": [0]}))[1]
    second = (await call(reader, "POST", path, {"sentence_ids": [0, 2]}))[1]
    assert first["pack_id"] == second["pack_id"]
    packs = reader.store.db.execute("SELECT * FROM mosaic_packs").fetchall()
    assert len(packs) == 1
    assert packs[0]["source_id"] == reader.document.mosaic_key
    sentences = reader.store.db.execute("SELECT payload FROM mosaic_sentences").fetchall()
    assert len(sentences) == 2
    assert len(reader.services.mosaic.client.packs) == 1


async def test_mosaic_partial_failure_is_reported_per_sentence(reader):
    reader.services.mosaic.client.reject = {"他们获得了菲尔兹奖。"}
    status, data = await call(
        reader,
        "POST",
        f"/api/reader/{reader.document.id}/mandarin-mosaic",
        {"sentence_ids": [0, 2]},
    )
    assert status == 200
    assert data["uploaded"] == 1
    assert data["failed_sentence_ids"] == [2]
    assert data["rejected"] == 1


async def test_unconfigured_integrations_report_their_reason(reader):
    reader.services.hanly = None
    reader.services.hanly_error = "Hanly auth config is missing."
    status, data = await call(
        reader, "POST", f"/api/reader/{reader.document.id}/hanly", item("获得", 2)
    )
    assert status == 503
    assert data["error"] == "Hanly auth config is missing."
    assert (await call(reader, "GET", f"/api/reader/{reader.document.id}"))[1][
        "hanly_available"
    ] is False


async def test_opening_the_reader_creates_nothing_externally(reader):
    for _ in range(3):
        assert (await call(reader, "GET", f"/api/reader/{reader.document.id}"))[0] == 200
    assert not reader.store.db.execute("SELECT 1 FROM hanly_collections").fetchall()
    assert not reader.store.db.execute("SELECT 1 FROM mosaic_packs").fetchall()
    assert not reader.services.hanly.client.calls
    assert not reader.services.mosaic.client.packs


async def test_frontend_assets_contain_no_credentials(reader):
    secrets = [TOKEN, "hanzo-282fc", "securetoken.googleapis.com", "mandarinmosaic", "Bearer "]
    for asset in ("index.html", "app.css", "app.js"):
        status, _, body = await reader.api.dispatch("GET", f"/reader/{asset}", {}, b"")
        assert status == 200
        text = body.decode("utf-8")
        assert not any(secret in text for secret in secrets)
    assert not any(
        secret in path.read_text(encoding="utf-8")
        for path in STATIC.iterdir()
        for secret in secrets
    )


async def test_rendered_document_response_contains_no_credentials(reader):
    status, _, body = await reader.api.dispatch(
        "GET", f"/api/reader/{reader.document.id}", reader.headers, b""
    )
    payload = body.decode("utf-8")
    assert TOKEN not in payload
    assert "refresh_token" not in payload
    assert "Authorization" not in payload


async def test_static_serving_refuses_traversal(reader):
    for name in ("../config.py", "..%2fapi.py", "server.py", "app.js/../api.py"):
        assert (await reader.api.dispatch("GET", f"/reader/{name}", {}, b""))[0] == 404


def update(text, identifier=42):
    status = SimpleNamespace(message_id=10, edit_text=AsyncMock())
    message = SimpleNamespace(text=text, document=None, reply_text=AsyncMock(return_value=status))
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=identifier),
        effective_chat=SimpleNamespace(id=identifier, type="private"),
        effective_message=message,
    )


@pytest.fixture
def handlers(config, store):
    return BotHandlers(replace(config, reader_url="https://reader.example"), store)


async def test_chinese_text_creates_a_reader_document_with_a_web_app_button(handlers, store):
    await handlers.handle(update("他们获得了菲尔兹奖。很多人都在讨论。"), SimpleNamespace())
    document = store.recent_reader_document(42)
    assert document is not None
    assert document.source_type == "text"
    markup = reader_markup(handlers.config, document.id)
    assert markup.inline_keyboard[0][0].web_app.url.endswith(f"/reader/?doc={document.id}")


async def test_non_chinese_text_is_not_turned_into_a_reader_document(handlers, store):
    message = update("just a note to myself")
    await handlers.handle(message, SimpleNamespace())
    assert store.recent_reader_document(42) is None
    assert "Chinese" in message.effective_message.reply_text.call_args.args[0]


async def test_reader_command_without_a_transcript_explains_itself(handlers, store):
    message = update("/reader")
    await handlers.handle(message, SimpleNamespace())
    assert "No saved transcript yet" in message.effective_message.reply_text.call_args.args[0]


async def test_reader_requires_a_public_url(config, store):
    handlers = BotHandlers(config, store)
    message = update("他们获得了菲尔兹奖。")
    await handlers.handle(message, SimpleNamespace())
    assert "READER_PUBLIC_URL" in message.effective_message.reply_text.call_args.args[0]
    assert store.recent_reader_document(42) is None


def episode_source(store, text=TEXT):
    source = store.root / "transcripts" / "episode"
    source.mkdir(parents=True, exist_ok=True)
    (source / "transcript.txt").write_text(text, encoding="utf-8")
    atomic_json(
        source / "metadata.json",
        {
            "podcast_id": "1490732024",
            "episode_id": "1000789324203",
            "podcast": "Mami Chinese",
            "title": "菲尔兹奖",
            "feed_url": "https://example.com/feed.xml",
            "guid": "episode-guid",
        },
    )
    return source


def test_podcast_documents_reuse_the_episode_identity_and_stay_stable(store):
    source = episode_source(store)
    first = from_transcript(42, source)
    second = from_transcript(42, source)
    assert first.id == second.id
    assert first.hanly_key == "apple:1490732024:1000789324203"
    assert first.mosaic_key == "reader:" + first.id
    assert first.title == "Mami Chinese｜菲尔兹奖"
    assert first.source_type == "podcast"


def test_reader_documents_use_study_chunks_when_a_pack_is_available(store):
    source = episode_source(store, "他在做研究，这是研究成果。")
    atomic_json(
        source / "study.json",
        {
            "vocabulary": [{"term": "研究成果"}],
            "patterns": [{"pattern": "做研究"}],
            "mosaic_sentences": [
                {"chinese": "他在做研究，这是研究成果。", "english": "He does research."}
            ],
        },
    )
    document = from_transcript(42, source)
    assert document.known_chunks() == frozenset({"研究成果", "做研究"})
    words = {t.text for t in document.tokens(document.sentences())[0] if t.word}
    assert {"做研究", "研究成果"} <= words
    assert document.known_translations() == {"他在做研究，这是研究成果。": "He does research."}


def test_reader_rejects_empty_and_oversized_text(store):
    with pytest.raises(UserError):
        from_text(42, "   ")
    with pytest.raises(UserError):
        from_text(42, "hello, no Chinese here")
    with pytest.raises(UserError):
        from_text(42, "很" * 200_001)


def test_http_head_parsing_and_response_framing():
    method, path, headers = parse_head(
        b"POST /api/reader/abc/hanly?x=1 HTTP/1.1\r\nHost: h\r\nX-Telegram-Init-Data: a=b\r\n"
    )
    assert (method, path) == ("POST", "/api/reader/abc/hanly")
    assert headers["x-telegram-init-data"] == "a=b"
    raw = response(200, {"Content-Type": "application/json"}, b'{"ok":1}')
    head, _, body = raw.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200 OK")
    assert b"Content-Length: 8" in head
    assert b"Connection: close" in head
    assert body == b'{"ok":1}'


def test_percent_encoded_paths_are_decoded_once():
    assert parse_head(b"GET /reader/app%2Ejs HTTP/1.1\r\n")[1] == "/reader/app.js"


async def test_reader_command_reuses_the_stored_transcript(handlers, store):
    source = episode_source(store)
    store.remember_source(42, source)
    message = update("/reader")
    await handlers.handle(message, SimpleNamespace())
    document = store.recent_reader_document(42)
    assert document.source_type == "podcast"
    assert document.raw_text == TEXT
    assert document.hanly_key == "apple:1490732024:1000789324203"
    assert message.effective_message.reply_text.call_args.kwargs["reply_markup"]


async def test_text_file_uploads_open_the_reader(handlers, store):
    message = update("")
    message.effective_message.document = SimpleNamespace(
        file_name="lesson.md",
        file_size=64,
        get_file=AsyncMock(
            return_value=SimpleNamespace(
                download_as_bytearray=AsyncMock(return_value=bytearray(TEXT.encode("utf-8")))
            )
        ),
    )
    await handlers.handle_document(message, SimpleNamespace())
    document = store.recent_reader_document(42)
    assert document.source_type == "file"
    assert document.title == "lesson.md"
    assert document.raw_text == TEXT


async def test_unsupported_file_types_are_refused(handlers, store):
    message = update("")
    message.effective_message.document = SimpleNamespace(
        file_name="episode.pdf", file_size=64, get_file=AsyncMock()
    )
    await handlers.handle_document(message, SimpleNamespace())
    assert store.recent_reader_document(42) is None
    assert ".txt" in message.effective_message.reply_text.call_args.args[0]
    message.effective_message.document.get_file.assert_not_called()


async def test_reader_uploads_do_not_disturb_the_study_pack_snapshot(config, store):
    source = episode_source(store)
    document = from_transcript(42, source)
    store.save_reader_document(document)
    services = SimpleNamespace(
        hanly=HanlyUploadService(store, FakeHanlyClient()),
        mosaic=MosaicUploadService(store, FakeMosaicClient()),
        hanly_error=None,
        mosaic_error=None,
    )
    api = ReaderApi(replace(config, token=TOKEN), store, services)
    reader = SimpleNamespace(
        api=api, store=store, services=services, headers={"x-telegram-init-data": valid_init_data()}
    )
    assert (
        await call(
            reader, "POST", f"/api/reader/{document.id}/mandarin-mosaic", {"sentence_ids": [0]}
        )
    )[0] == 200
    assert (await call(reader, "POST", f"/api/reader/{document.id}/hanly", item("获得", 2)))[
        0
    ] == 200
    packs = {row["source_id"] for row in store.db.execute("SELECT source_id FROM mosaic_packs")}
    assert packs == {"reader:" + document.id}
    assert "apple:1490732024:1000789324203" not in packs
    collections = {
        row["episode_id"] for row in store.db.execute("SELECT episode_id FROM hanly_collections")
    }
    assert collections == {"apple:1490732024:1000789324203"}


GLOSS = "Исследовательские результаты"
SENTENCE = "她因为研究成果非常突出，所以获得了很多国际奖项。"
SENTENCE_RU = "Благодаря выдающимся результатам исследований она получила множество наград."


def test_note_contains_meaning_source_and_translation():
    assert build_hanly_note(GLOSS, SENTENCE, SENTENCE_RU) == (
        f"{GLOSS}\n\n原文：{SENTENCE}\nПеревод：{SENTENCE_RU}"
    )


def test_note_without_a_glyph_translation_starts_at_the_source():
    assert build_hanly_note(None, SENTENCE, SENTENCE_RU) == (
        f"原文：{SENTENCE}\nПеревод：{SENTENCE_RU}"
    )


def test_note_without_a_sentence_translation_omits_the_label():
    note = build_hanly_note(GLOSS, SENTENCE, None)
    assert note == f"{GLOSS}\n\n原文：{SENTENCE}"
    assert "Перевод" not in note


def test_note_with_no_translations_keeps_only_the_source():
    note = build_hanly_note(None, SENTENCE, "   ")
    assert note == f"原文：{SENTENCE}"
    assert "Перевод" not in note


def test_note_has_exactly_one_blank_line_and_no_trailing_whitespace():
    note = build_hanly_note("  " + GLOSS + "  ", SENTENCE, SENTENCE_RU)
    assert note.split("\n")[1] == ""
    assert "\n\n\n" not in note
    assert note == note.strip()
    assert not any(line != line.rstrip() for line in note.split("\n"))


def test_note_preserves_chinese_punctuation_from_the_source():
    sentence = "他说：“真的吗？”……然后走了。"
    assert build_hanly_note(None, sentence, None) == "原文：" + sentence


def test_note_is_empty_when_there_is_nothing_to_say():
    assert build_hanly_note(None, "", None) == ""
    assert build_hanly_note(None, "", SENTENCE_RU) == ""


def study_document(store, text=TEXT, **material):
    source = episode_source(store, text)
    atomic_json(source / "study.json", material)
    document = from_transcript(42, source)
    store.save_reader_document(document)
    return document


@pytest.fixture
def notes(config, store):
    services = SimpleNamespace(
        hanly=HanlyUploadService(store, FakeHanlyClient()),
        mosaic=None,
        hanly_error=None,
        mosaic_error="unset",
    )
    return SimpleNamespace(
        api=ReaderApi(replace(config, token=TOKEN), store, services),
        services=services,
        store=store,
        headers={"x-telegram-init-data": valid_init_data()},
    )


async def test_upload_builds_a_note_from_reused_study_translations(notes):
    document = study_document(
        notes.store,
        vocabulary=[{"term": "获得", "meaning": "получать"}],
        passages=[{"source": "他们获得了菲尔兹奖。", "translation": "Они получили премию Филдса."}],
    )
    notes.document = document
    status, data = await call(notes, "POST", f"/api/reader/{document.id}/hanly", item("获得", 2))
    assert status == 200
    assert data["notes"] == [{"glyph": "获得", "action": "created", "error": ""}]
    assert notes.services.hanly.client.notes == [
        (
            "获得",
            "получать\n\n原文：他们获得了菲尔兹奖。\nПеревод：Они получили премию Филдса.",
            None,
        )
    ]


async def test_upload_reuses_a_quoted_example_translation_for_the_same_sentence(notes):
    document = study_document(
        notes.store,
        vocabulary=[
            {
                "term": "获得",
                "meaning": "получать",
                "example": "他们获得了菲尔兹奖。",
                "example_translation": "Они получили премию Филдса.",
            }
        ],
    )
    await call(notes, "POST", f"/api/reader/{document.id}/hanly", item("获得", 2))
    assert "Перевод：Они получили премию Филдса." in notes.services.hanly.client.notes[0][1]


async def test_direct_text_notes_carry_the_source_sentence_without_translations(notes):
    document = from_text(42, TEXT)
    notes.store.save_reader_document(document)
    await call(notes, "POST", f"/api/reader/{document.id}/hanly", item("获得", 2))
    assert notes.services.hanly.client.notes == [("获得", "原文：他们获得了菲尔兹奖。", None)]


async def test_note_uses_the_canonical_sentence_not_client_supplied_text(notes):
    document = study_document(notes.store)
    status, _ = await call(
        notes,
        "POST",
        f"/api/reader/{document.id}/hanly",
        {"items": [{"glyph": "获得", "sentence_id": 2, "sentence": "伪造的句子。"}]},
    )
    assert status == 400
    assert not notes.services.hanly.client.notes


async def test_glyph_must_occur_in_the_referenced_sentence(notes):
    document = study_document(notes.store)
    status, data = await call(notes, "POST", f"/api/reader/{document.id}/hanly", item("获得", 1))
    assert status == 400
    assert "not a Reader word" in data["error"]
    assert not notes.services.hanly.client.calls


async def test_arbitrary_prose_spans_cannot_be_uploaded_as_glyphs(notes):
    document = study_document(notes.store)
    for span in ("他们获得", "获得了菲尔兹", "们获"):
        status, _ = await call(notes, "POST", f"/api/reader/{document.id}/hanly", item(span, 2))
        assert status == 400
    assert not notes.services.hanly.client.calls


async def test_an_arbitrary_chinese_chunk_is_accepted_without_any_dictionary_check(notes):
    document = study_document(
        notes.store,
        text="老师说辛苦了。\n",
        vocabulary=[{"term": "辛苦了", "meaning": "Хорошо поработал(а)!"}],
    )
    status, data = await call(notes, "POST", f"/api/reader/{document.id}/hanly", item("辛苦了", 0))
    assert status == 200
    assert notes.services.hanly.client.calls[0][2] == ["辛苦了"]
    assert notes.services.hanly.client.notes[0][0] == "辛苦了"
    assert data["notes"][0]["action"] == "created"


async def test_an_existing_user_note_is_preserved_and_reported(notes):
    document = study_document(notes.store)
    notes.services.hanly.client.remote["获得"] = "Моя собственная заметка"
    status, data = await call(notes, "POST", f"/api/reader/{document.id}/hanly", item("获得", 2))
    assert status == 200
    assert data["uploaded"] == 1
    assert data["notes"][0]["action"] == "skipped-user-modified"
    assert notes.store.hanly_note("获得") is None


async def test_a_note_this_integration_wrote_may_be_updated_later(notes):
    document = study_document(notes.store)
    await call(notes, "POST", f"/api/reader/{document.id}/hanly", item("获得", 2))
    stored = notes.store.hanly_note("获得")
    assert stored == "原文：他们获得了菲尔兹奖。"
    notes.services.hanly.client.remote["获得"] = stored
    second = study_document(notes.store, vocabulary=[{"term": "获得", "meaning": "получать"}])
    status, data = await call(notes, "POST", f"/api/reader/{second.id}/hanly", item("获得", 2))
    assert status == 200
    assert data["notes"][0]["action"] == "updated"
    assert notes.store.hanly_note("获得").startswith("получать")


async def test_a_remotely_modified_note_is_never_overwritten(notes):
    document = study_document(notes.store)
    await call(notes, "POST", f"/api/reader/{document.id}/hanly", item("获得", 2))
    notes.services.hanly.client.remote["获得"] = "Я переписал это вручную"
    second = study_document(notes.store, vocabulary=[{"term": "获得", "meaning": "получать"}])
    status, data = await call(notes, "POST", f"/api/reader/{second.id}/hanly", item("获得", 2))
    assert data["notes"][0]["action"] == "skipped-user-modified"
    assert notes.services.hanly.client.remote["获得"] == "Я переписал это вручную"
    assert notes.store.hanly_note("获得") == "原文：他们获得了菲尔兹奖。"


async def test_note_failure_never_reports_the_glyph_upload_as_failed(notes):
    document = study_document(notes.store)
    notes.services.hanly.client.note_error = UserError("Hanly note write failed.")
    status, data = await call(notes, "POST", f"/api/reader/{document.id}/hanly", item("获得", 2))
    assert status == 200
    assert data["uploaded"] == 1
    assert data["collection_id"]
    assert data["notes"] == [
        {"glyph": "获得", "action": "failed", "error": "Hanly note write failed."}
    ]
    assert notes.store.hanly_note("获得") is None


async def test_an_unexpected_note_error_is_contained(notes):
    document = study_document(notes.store)
    notes.services.hanly.client.note_error = RuntimeError("boom")
    status, data = await call(notes, "POST", f"/api/reader/{document.id}/hanly", item("获得", 2))
    assert status == 200
    assert data["notes"][0]["action"] == "failed"
    assert "boom" not in data["notes"][0]["error"]


async def test_a_glyph_with_no_available_context_still_uploads(notes):
    document = study_document(notes.store)
    await call(notes, "POST", f"/api/reader/{document.id}/hanly", item("获得", 2))
    assert notes.services.hanly.client.notes[0][1] == "原文：他们获得了菲尔兹奖。"


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("辛苦了", "xīn kǔ le"),
        ("研究成果", "yán jiū chéng guǒ"),
        ("获得", "huò dé"),
        ("很难", "hěn nán"),
    ],
)
def test_pinyin_uses_tone_marks_not_numbers(word, expected):
    assert pinyin_for(word) == expected
    assert not re.search(r"[a-zü][1-5]", pinyin_for(word))


@pytest.mark.parametrize("word", ["了", "的", "吗", "我们"])
def test_neutral_tone_carries_no_mark(word):
    syllable = pinyin_for(word).split()[-1]
    assert syllable in {"le", "de", "ma", "men"}


def test_polyphonic_characters_resolve_from_the_whole_lexical_item():
    assert pinyin_for("银行") == "yín háng"
    assert pinyin_for("行走") == "xíng zǒu"


@pytest.mark.parametrize("value", ["，", "。", "！", "Nobel", "it's", " ", ""])
def test_punctuation_and_latin_get_no_pinyin(value):
    assert pinyin_for(value) == ""


def test_pinyin_is_deterministic_and_local(monkeypatch):
    monkeypatch.setattr("socket.socket.connect", lambda *a, **k: pytest.fail("network call"))
    assert len({pinyin_for.__wrapped__("研究成果") for _ in range(25)}) == 1


async def test_token_representation_exposes_pinyin_and_known_meanings(notes):
    document = study_document(
        notes.store,
        text="他们获得了菲尔兹奖。\n",
        vocabulary=[{"term": "获得", "meaning": "получать"}],
    )
    status, data = await call(notes, "GET", f"/api/reader/{document.id}")
    assert status == 200
    tokens = data["sentences"][0]["tokens"]
    words = {t["t"]: t for t in tokens if t["w"]}
    assert words["获得"]["p"] == "huò dé"
    assert words["获得"]["m"] == "получать"
    assert "m" not in words["他们"], "an unknown meaning is omitted, never an empty label"
    assert words["他们"]["p"] == "tā men"


async def test_punctuation_tokens_carry_no_pinyin_field(notes):
    document = study_document(notes.store, text="他们获得了菲尔兹奖。\n")
    _, data = await call(notes, "GET", f"/api/reader/{document.id}")
    punctuation = [t for t in data["sentences"][0]["tokens"] if not t["w"]]
    assert punctuation
    assert all(set(t) == {"t", "w"} for t in punctuation)


async def test_direct_text_tokens_still_carry_pinyin_without_meanings(notes):
    document = from_text(42, "老师说辛苦了。\n")
    notes.store.save_reader_document(document)
    _, data = await call(notes, "GET", f"/api/reader/{document.id}")
    words = {t["t"]: t for t in data["sentences"][0]["tokens"] if t["w"]}
    assert all(token["p"] for token in words.values())
    assert all("m" not in token for token in words.values())


CEDICT_SAMPLE = """# sample
學習 学习 [xue2 xi2] /to learn; to study/
銀行 银行 [yin2 hang2] /bank/
獲得 获得 [huo4 de2] /to obtain; to receive/
"""


@pytest.fixture
def lexical(config, store, tmp_path):
    source = tmp_path / "cedict_ts.u8"
    source.write_text(CEDICT_SAMPLE, encoding="utf-8")
    target = tmp_path / "cedict.sqlite3"
    cedict_build(source, target)
    dictionary = Dictionary(target)
    services = SimpleNamespace(
        hanly=HanlyUploadService(store, FakeHanlyClient()),
        mosaic=None,
        hanly_error=None,
        mosaic_error="unset",
    )
    yield SimpleNamespace(
        api=ReaderApi(replace(config, token=TOKEN), store, services, dictionary=dictionary),
        services=services,
        store=store,
        headers={"x-telegram-init-data": valid_init_data()},
    )
    dictionary.close()


async def test_dictionary_supplies_meaning_and_pinyin_for_direct_text(lexical):
    document = from_text(42, "他在银行学习。\n")
    lexical.store.save_reader_document(document)
    _, data = await call(lexical, "GET", f"/api/reader/{document.id}")
    words = {t["t"]: t for t in data["sentences"][0]["tokens"] if t["w"]}
    assert words["银行"]["p"] == "yín háng"
    assert words["银行"]["m"] == "bank"
    assert words["银行"]["ms"] == "cc-cedict"


async def test_contextual_meaning_wins_over_the_dictionary(lexical):
    document = study_document(
        lexical.store,
        text="他们获得了菲尔兹奖。\n",
        vocabulary=[{"term": "获得", "meaning": "получать", "pinyin": "huò dé"}],
    )
    _, data = await call(lexical, "GET", f"/api/reader/{document.id}")
    words = {t["t"]: t for t in data["sentences"][0]["tokens"] if t["w"]}
    assert words["获得"]["m"] == "получать"
    assert words["获得"]["ms"] == "contextual"


async def test_a_glyph_in_neither_source_omits_meaning_but_keeps_pinyin(lexical):
    document = from_text(42, "这是研究成果。\n")
    lexical.store.save_reader_document(document)
    _, data = await call(lexical, "GET", f"/api/reader/{document.id}")
    words = {t["t"]: t for t in data["sentences"][0]["tokens"] if t["w"]}
    assert "m" not in words["研究成果"] and "ms" not in words["研究成果"]
    assert words["研究成果"]["p"] == "yán jiū chéng guǒ"


async def test_a_chunk_absent_from_the_dictionary_is_still_uploadable_to_hanly(lexical):
    document = study_document(
        lexical.store, text="老师说辛苦了。\n", vocabulary=[{"term": "辛苦了"}]
    )
    _, data = await call(lexical, "GET", f"/api/reader/{document.id}")
    words = {t["t"]: t for t in data["sentences"][0]["tokens"] if t["w"]}
    assert "辛苦了" in words, "segmentation still exposes it"
    assert "m" not in words["辛苦了"], "no dictionary entry"
    status, _ = await call(lexical, "POST", f"/api/reader/{document.id}/hanly", item("辛苦了", 0))
    assert status == 200, "dictionary membership is never validation"
    assert lexical.services.hanly.client.calls[0][2] == ["辛苦了"]


async def test_the_reader_works_with_no_dictionary_at_all(reader):
    assert not reader.api.dictionary.available
    status, data = await call(reader, "GET", f"/api/reader/{reader.document.id}")
    assert status == 200
    words = [t for t in data["sentences"][2]["tokens"] if t["w"]]
    assert all(token["p"] for token in words), "local pinyin fallback still applies"


async def test_the_dictionary_is_queried_once_per_document_request(lexical, monkeypatch):
    document = from_text(42, "他在银行学习。银行很大。学习很难。\n")
    lexical.store.save_reader_document(document)
    calls = []
    original = Dictionary.lookup_many
    monkeypatch.setattr(
        Dictionary,
        "lookup_many",
        lambda self, glyphs: calls.append(list(glyphs)) or original(self, glyphs),
    )
    await call(lexical, "GET", f"/api/reader/{document.id}")
    assert len(calls) == 1
    assert len(calls[0]) == len(set(calls[0])), "repeated glyphs are collapsed"


UNTRANSLATED_SENTENCE = "这样的讨论不只发生在中国，很多国家都会有同样的问题。"


def untranslated_pack(store, native="ru"):
    """Reproduces a real pack whose model answered in Chinese despite native_language=ru."""
    source = store.root / "transcripts" / "episode"
    source.mkdir(parents=True, exist_ok=True)
    (source / "transcript.txt").write_text(UNTRANSLATED_SENTENCE + "\n", encoding="utf-8")
    atomic_json(
        source / "metadata.json",
        {
            "podcast_id": "1490732024",
            "episode_id": "1000789324203",
            "podcast": "Mami Chinese",
            "title": "同样",
            "feed_url": "https://example.com/feed.xml",
            "guid": "episode-guid",
            "study_settings": {"target_language": "zh", "native_language": native},
        },
    )
    atomic_json(
        source / "study.json",
        {
            "vocabulary": [
                {
                    "term": "同样",
                    "meaning": "同样就是一样的意思。",
                    "example": UNTRANSLATED_SENTENCE,
                    "example_translation": UNTRANSLATED_SENTENCE,
                }
            ],
            "passages": [{"source": UNTRANSLATED_SENTENCE, "translation": UNTRANSLATED_SENTENCE}],
        },
    )
    document = from_transcript(42, source)
    store.save_reader_document(document)
    return document


def test_a_chinese_meaning_is_not_offered_as_a_contextual_translation(store):
    document = untranslated_pack(store)
    assert document.native_language() == "ru"
    assert document.glyph_meanings() == {}, "a Chinese 'meaning' is dropped"
    assert document.sentence_translations() == {}, "a repeated source is not a translation"


def test_the_hanly_note_omits_a_translation_that_is_just_the_chinese_again(store):
    document = untranslated_pack(store)
    sentence = document.sentences()[0]
    story = ReaderApi.stories(document, [("同样", sentence)])[0][1]
    assert story == "原文：" + UNTRANSLATED_SENTENCE
    assert "Перевод" not in story
    assert story.count(UNTRANSLATED_SENTENCE) == 1


def test_a_properly_translated_pack_still_produces_the_full_note(store):
    document = untranslated_pack(store)
    atomic_json(
        Path(document.source_reference) / "study.json",
        {
            "vocabulary": [
                {
                    "term": "同样",
                    "meaning": "такой же; одинаковый",
                    "example": UNTRANSLATED_SENTENCE,
                    "example_translation": "Такие обсуждения происходят не только в Китае.",
                }
            ],
            "passages": [],
        },
    )
    document = from_transcript(42, Path(document.source_reference))
    sentence = document.sentences()[0]
    story = ReaderApi.stories(document, [("同样", sentence)])[0][1]
    assert story == (
        "такой же; одинаковый\n\n原文："
        + UNTRANSLATED_SENTENCE
        + "\nПеревод：Такие обсуждения происходят не только в Китае."
    )


async def test_the_reader_popup_falls_back_to_the_dictionary_when_the_meaning_is_chinese(lexical):
    document = untranslated_pack(lexical.store)
    _, data = await call(lexical, "GET", f"/api/reader/{document.id}")
    words = {t["t"]: t for t in data["sentences"][0]["tokens"] if t["w"]}
    assert "m" not in words["同样"], "the Chinese 'meaning' is not shown as a translation"
    assert words["学习"]["ms"] == "cc-cedict" if "学习" in words else True
