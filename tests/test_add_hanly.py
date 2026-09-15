import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from test_hanly import (
    API,
    ID_TOKEN,
    KEY,
    TOKEN,
    NoteAPI,
    client,
    collection,
    document,
    firestore_calls,
)
from test_hanly import auth as _auth

from podcast_bot.bot import BotHandlers
from podcast_bot.hanly.auth import HanlyAuth, HanlyError
from podcast_bot.hanly.cards import ManualCards, note_text
from podcast_bot.hanly.client import VerificationError, parse_document
from podcast_bot.hanly.manual import MAX_LENGTH, distinct_name, parse_glyph
from podcast_bot.hanly.notes import build_hanly_note
from podcast_bot.hanly.service import HanlyUploadService
from podcast_bot.models import UserError
from podcast_bot.reader.dictionary import DictionaryEntry
from podcast_bot.storage import Storage

SENTENCE = "塞翁失马，焉知非福"
MEANING = "Нет худа без добра: неудача может обернуться удачей."
EXAMPLE = "塞翁失马，焉知非福，别灰心。"
EXAMPLE_RU = "Нет худа без добра, не унывай."


@pytest.fixture
def auth(tmp_path):
    return _auth.__wrapped__(tmp_path)


@pytest.fixture
def service(auth, store):
    api = API()
    return HanlyUploadService(store, client(auth, api)), api


def store_rows(svc):
    return svc.storage.db.execute("SELECT count(*) FROM hanly_manual_collections").fetchone()[0]


def glyphs_of(api, identifier):
    return parse_document(api.doc).collections[identifier]["glyphs"]


async def send(handlers, text, user_id=42):
    status = SimpleNamespace(edit_text=AsyncMock())
    message = SimpleNamespace(text=text, reply_text=AsyncMock(return_value=status))
    await handlers.handle(
        SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=user_id),
            effective_chat=SimpleNamespace(id=42, type="private"),
        ),
        SimpleNamespace(),
    )
    return message, status


def replied(message, status) -> str:
    if status.edit_text.call_args:
        return status.edit_text.call_args.args[0]
    return message.reply_text.call_args.args[0]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/add_hanly 辛苦了", "辛苦了"),
        ("/add_hanly 不知不觉", "不知不觉"),
        (f"/add_hanly {SENTENCE}", SENTENCE),
        ("/add_hanly   我们下次再聊。  ", "我们下次再聊。"),
        ("/add_hanly 这是 AI 技术", "这是 AI 技术"),
        ("/add_hanly  这是  AI  技术 ", "这是  AI  技术"),
        ("/add_hanly@StudyBot 辛苦了", "辛苦了"),
        ("/add_hanly 咕噜咕噜转的螺丝钉", "咕噜咕噜转的螺丝钉"),
        ("/add_hanly " + "汉" * MAX_LENGTH, "汉" * MAX_LENGTH),
    ],
)
def test_the_whole_argument_becomes_one_glyph(text, expected):
    assert parse_glyph(text) == expected


@pytest.mark.parametrize(
    "text",
    ["/add_hanly", "/add_hanly ", "/add_hanly 　\t ", "/add_hanly " + "汉" * (MAX_LENGTH + 1)],
)
def test_unusable_arguments_are_refused(text):
    with pytest.raises(UserError):
        parse_glyph(text)


@pytest.mark.parametrize("text", ["/add_hanly hello there", "/add_hanly 123"])
def test_a_card_without_chinese_is_refused(text):
    with pytest.raises(UserError, match="Chinese"):
        parse_glyph(text)


def test_missing_argument_answers_with_usage():
    with pytest.raises(UserError, match=r"/add_hanly 不知不觉"):
        parse_glyph("/add_hanly")


def test_a_multi_line_argument_is_refused():
    with pytest.raises(UserError, match="one line"):
        parse_glyph("/add_hanly 辛苦了\n不知不觉")


async def test_a_sentence_is_one_card_not_its_words(service):
    svc, api = service
    result = await svc.add_manual_glyph(SENTENCE)
    assert glyphs_of(api, result.collection_id) == [SENTENCE]
    assert not result.present


async def test_an_expression_no_dictionary_knows_is_accepted(service):
    svc, api = service
    result = await svc.add_manual_glyph("咕噜咕噜转的螺丝钉")
    assert glyphs_of(api, result.collection_id) == ["咕噜咕噜转的螺丝钉"]


async def test_the_second_add_reuses_the_collection_and_keeps_order(service):
    svc, api = service
    first = await svc.add_manual_glyph("辛苦了")
    second = await svc.add_manual_glyph("不知不觉")
    assert second.collection_id == first.collection_id
    assert glyphs_of(api, first.collection_id) == ["辛苦了", "不知不觉"]
    assert store_rows(svc) == 1


async def test_the_same_text_twice_leaves_one_card(service):
    svc, api = service
    await svc.add_manual_glyph("辛苦了")
    repeated = await svc.add_manual_glyph("辛苦了")
    assert repeated.present
    assert glyphs_of(api, repeated.collection_id) == ["辛苦了"]
    assert "Already in Hanly" in repeated.message()


async def test_identity_survives_a_storage_reload(auth, store):
    api = API()
    first = await HanlyUploadService(store, client(auth, api)).add_manual_glyph("辛苦了")
    restarted = Storage(store.root)
    try:
        service = HanlyUploadService(restarted, client(HanlyAuth.load(auth.path), api))
        second = await service.add_manual_glyph("不知不觉")
    finally:
        restarted.close()
    assert second.collection_id == first.collection_id
    assert glyphs_of(api, first.collection_id) == ["辛苦了", "不知不觉"]


async def test_identity_is_not_keyed_by_message_or_glyph(auth, store, config):
    api = API()
    handlers = BotHandlers(config, store)
    handlers.hanly = HanlyUploadService(store, client(auth, api))
    await send(handlers, "/add_hanly 辛苦了")
    await send(handlers, "/add_hanly 不知不觉")
    await send(handlers, f"/add_hanly {SENTENCE}")
    rows = store.db.execute("SELECT uuid, name, status FROM hanly_manual_collections").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "verified"
    assert glyphs_of(api, rows[0]["uuid"]) == ["辛苦了", "不知不觉", SENTENCE]


async def test_a_user_collection_of_the_same_name_is_never_adopted(auth, store):
    api = API()
    mine = str(uuid4())
    api.doc = document({mine: collection(mine, ["我的"], name="Manual imports")})
    result = await HanlyUploadService(store, client(auth, api)).add_manual_glyph("辛苦了")
    assert result.collection_id != mine
    assert result.name == "Manual imports (bot)"
    assert glyphs_of(api, mine) == ["我的"]
    assert glyphs_of(api, result.collection_id) == ["辛苦了"]


def test_a_deleted_user_collection_does_not_push_the_name_aside():
    taken = {"a": collection("a", [], name="Manual imports", deleted=True)}
    assert distinct_name(taken) == "Manual imports"
    live = {"a": collection("a", [], name="Manual imports")}
    assert distinct_name(live) == "Manual imports (bot)"
    both = {
        "a": collection("a", [], name="Manual imports"),
        "b": collection("b", [], name="Manual imports (bot)"),
    }
    assert distinct_name(both) == "Manual imports (bot 2)"


@pytest.mark.parametrize("removal", ["hard", "soft"])
async def test_a_collection_deleted_in_hanly_is_restored_under_the_same_uuid(auth, store, removal):
    api = API()
    service = HanlyUploadService(store, client(auth, api))
    first = await service.add_manual_glyph("辛苦了")
    parsed = parse_document(api.doc).collections
    if removal == "hard":
        del parsed[first.collection_id]
    else:
        parsed[first.collection_id]["deleted"] = True
    api.doc = document(parsed)
    # The same text again: a card the user can no longer see is restored, not reported as present.
    second = await service.add_manual_glyph("辛苦了")
    assert second.collection_id == first.collection_id
    assert not second.present
    restored = parse_document(api.doc).collections[first.collection_id]
    assert restored["deleted"] is False
    assert restored["glyphs"] == ["辛苦了"]
    assert (await service.add_manual_glyph("辛苦了")).present


async def test_the_write_uses_the_existing_conditional_protocol(service):
    svc, api = service
    result = await svc.add_manual_glyph(SENTENCE)
    assert [r.method for r in firestore_calls(api)] == ["GET", "GET", "PATCH", "GET"]
    patch = [r for r in firestore_calls(api) if r.method == "PATCH"][0]
    assert patch.url.params.get_list("updateMask.fieldPaths") == [
        "all_user_collections",
        "all_user_collections_timestamp",
    ]
    assert patch.url.params["currentDocument.updateTime"]
    fields = json.loads(patch.content)["fields"]
    assert set(fields) == {"all_user_collections", "all_user_collections_timestamp"}
    assert SENTENCE in fields["all_user_collections"]["stringValue"]
    assert parse_document(api.doc).collections["Favorites"]["glyphs"] == ["我"]
    assert result.total == 1


async def test_a_conflict_is_retried_without_losing_a_concurrent_card(service):
    svc, api = service
    first = await svc.add_manual_glyph("辛苦了")
    api.conflicts = 1
    await svc.add_manual_glyph("不知不觉")
    final = parse_document(api.doc)
    assert final.collections[first.collection_id]["glyphs"] == ["辛苦了", "用户添加", "不知不觉"]
    assert final.collections["Favorites"]["glyphs"] == ["我", "并发修改"]


async def test_two_commands_in_flight_both_survive(auth, store, config):
    api = API()

    async def interleaved(request):
        await asyncio.sleep(0)
        return api(request)

    handlers = BotHandlers(config, store)
    handlers.hanly = HanlyUploadService(store, client(auth, interleaved))
    await asyncio.gather(send(handlers, "/add_hanly 辛苦了"), send(handlers, "/add_hanly 不知不觉"))
    rows = store.db.execute("SELECT uuid FROM hanly_manual_collections").fetchall()
    assert len(rows) == 1
    assert sorted(glyphs_of(api, rows[0]["uuid"])) == sorted(["辛苦了", "不知不觉"])


async def test_an_unverified_write_is_not_reported_as_success(service):
    svc, api = service
    api.fail_verify = "glyph"
    with pytest.raises(VerificationError):
        await svc.add_manual_glyph("辛苦了")
    assert (
        svc.storage.db.execute("SELECT status FROM hanly_manual_collections").fetchone()["status"]
        == "unconfirmed"
    )


async def test_identity_is_committed_before_the_first_network_write(auth, store):
    api = API()

    def handler(request):
        if request.method == "PATCH":
            assert (
                store.db.execute("SELECT count(*) FROM hanly_manual_collections").fetchone()[0] == 1
            )
        return api(request)

    result = await HanlyUploadService(store, client(auth, handler)).add_manual_glyph("辛苦了")
    assert result.collection_id in parse_document(api.doc).collections


async def test_a_rejected_id_token_is_refreshed_once_and_the_card_still_lands(auth, store):
    api = API()
    rejected = []

    def handler(request):
        if request.url.host == "firestore.googleapis.com" and not rejected:
            rejected.append(request)
            api.calls.append(request)
            return httpx.Response(401, text=KEY + TOKEN + ID_TOKEN)
        return api(request)

    result = await HanlyUploadService(store, client(auth, handler)).add_manual_glyph("辛苦了")
    assert sum(r.url.host == "securetoken.googleapis.com" for r in api.calls) == 2
    assert glyphs_of(api, result.collection_id) == ["辛苦了"]


async def test_telegram_reports_the_new_card_then_the_repeat(auth, store, config):
    handlers = BotHandlers(config, store)
    handlers.hanly = HanlyUploadService(store, client(auth, API()))
    added = replied(*await send(handlers, "/add_hanly 辛苦了"))
    assert added == "✓ Added to Hanly\n\n辛苦了\nCollection: Manual imports"
    again = replied(*await send(handlers, "/add_hanly 辛苦了"))
    assert again == "✓ Already in Hanly\n\n辛苦了\nCollection: Manual imports"
    assert store.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


async def test_telegram_guides_a_missing_argument(auth, store, config):
    api = API()
    handlers = BotHandlers(config, store)
    handlers.hanly = HanlyUploadService(store, client(auth, api))
    assert replied(*await send(handlers, "/add_hanly")) == (
        "Send a Chinese word, phrase or sentence:\n/add_hanly 不知不觉"
    )
    assert not api.calls


async def test_telegram_refuses_an_oversized_argument_before_any_call(auth, store, config):
    api = API()
    handlers = BotHandlers(config, store)
    handlers.hanly = HanlyUploadService(store, client(auth, api))
    answer = replied(*await send(handlers, "/add_hanly " + "汉" * (MAX_LENGTH + 1)))
    assert str(MAX_LENGTH) in answer
    assert not api.calls


async def test_telegram_reports_a_hanly_failure_concisely(auth, store, config):
    api = API()
    api.status = 500
    handlers = BotHandlers(config, store)
    handlers.hanly = HanlyUploadService(store, client(auth, api))
    answer = replied(*await send(handlers, "/add_hanly 辛苦了"))
    assert answer == "Hanly could not read collections. Use /hanly to retry."
    assert TOKEN not in answer and KEY not in answer and ID_TOKEN not in answer


async def test_an_unexpected_failure_stays_a_short_message(auth, store, config, caplog):
    handlers = BotHandlers(config, store)
    handlers.hanly = HanlyUploadService(store, client(auth, API()))
    handlers.hanly.add_manual_glyph = AsyncMock(side_effect=RuntimeError(TOKEN))
    answer = replied(*await send(handlers, "/add_hanly 辛苦了"))
    assert answer == "Could not add this to Hanly. Please try again."
    assert TOKEN not in caplog.text


async def test_the_command_is_unavailable_without_configured_hanly(store, config):
    handlers = BotHandlers(config, store)
    handlers.hanly_error = "Hanly auth config is malformed or inaccessible."
    assert replied(*await send(handlers, "/add_hanly 辛苦了")) == handlers.hanly_error


async def test_another_telegram_user_cannot_write_to_the_account(auth, store, config):
    api = API()
    handlers = BotHandlers(config, store)
    handlers.hanly = HanlyUploadService(store, client(auth, api))
    message = SimpleNamespace(text="/add_hanly 辛苦了", reply_text=AsyncMock())
    await handlers.handle(
        SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=99),
            effective_chat=SimpleNamespace(id=42, type="private"),
        ),
        SimpleNamespace(),
    )
    assert not api.calls
    assert not message.reply_text.called
    assert store.db.execute("SELECT count(*) FROM hanly_manual_collections").fetchone()[0] == 0


async def test_the_upload_path_for_episodes_is_untouched(service):
    svc, api = service
    await svc.add_manual_glyph("辛苦了")
    assert not svc.storage.db.execute("SELECT count(*) FROM hanly_collections").fetchone()[0]
    with pytest.raises(HanlyError):
        await svc.upload(svc.storage.root)


class Model:
    """One structured card-context response, with the requests it received."""

    def __init__(self, meaning=MEANING, example=EXAMPLE, translation=EXAMPLE_RU, error=None):
        self.reply = (meaning, example, translation)
        self.error = error
        self.payloads = []

    async def request(self, schema, instructions, payload, model, max_output):
        self.payloads.append(payload)
        if self.error:
            raise self.error
        meaning, example, translation = self.reply
        return schema(meaning=meaning, example=example, example_translation=translation), {}


class Glossary:
    def __init__(self, entries):
        self.entries = entries

    def lookup(self, glyph):
        senses = self.entries.get(glyph)
        return (
            DictionaryEntry(
                simplified=glyph,
                traditional=glyph,
                pinyin="",
                definitions=tuple(senses),
                alternatives=(),
            )
            if senses
            else None
        )


def test_the_reader_note_is_unchanged_when_no_reading_is_given():
    assert build_hanly_note("Значение", "他走了。", "Он ушёл.") == (
        "Значение\n\n原文：他走了。\nПеревод：Он ушёл."
    )


async def test_a_card_always_carries_its_reading(store):
    card = await ManualCards(store).card("咕噜咕噜转的螺丝钉")
    assert card["pinyin"] == "gū lū gū lū zhuǎn de luó sī dīng"
    assert not card["meaning"] and not card["example"]
    assert note_text(card) == "gū lū gū lū zhuǎn de luó sī dīng"


async def test_the_note_carries_reading_meaning_and_a_translated_example(store):
    card = await ManualCards(store, Model()).card(SENTENCE)
    assert note_text(card) == (
        f"sài wēng shī mǎ yān zhī fēi fú\n\n{MEANING}\n\n原文：{EXAMPLE}\nПеревод：{EXAMPLE_RU}"
    )


async def test_the_russian_dictionary_is_preferred_and_steers_the_example(store):
    model = Model(meaning="Выдуманное значение")
    glossary = Glossary({SENTENCE: ["нет худа без добра", "не было бы счастья"]})
    card = await ManualCards(store, model, glossary).card(SENTENCE)
    assert card["meaning"] == "нет худа без добра; не было бы счастья"
    assert model.payloads[0]["known_meaning"] == card["meaning"]
    assert model.payloads[0]["TERM"] == SENTENCE


async def test_a_generated_meaning_fills_what_the_dictionary_lacks(store):
    card = await ManualCards(store, Model(), Glossary({})).card(SENTENCE)
    assert card["meaning"] == MEANING
    assert not Glossary({}).lookup(SENTENCE)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"example": "他今天很忙。"},
        {"example": ""},
        {"example": SENTENCE + "，" + "他说得对啊" * 30},
        {"meaning": "Something in English only"},
        {"meaning": "塞翁失马的意思"},
        {"meaning": "塞翁失马，焉知非福，这个成语的意思 да"},
        {"meaning": "Очень длинное значение. " * 20},
        {"translation": EXAMPLE},
        {"translation": "只是中文"},
        {"translation": "Перевод. " * 60},
    ],
)
async def test_unusable_generated_context_is_dropped_not_stored(store, kwargs):
    card = await ManualCards(store, Model(**kwargs)).card(SENTENCE)
    assert not card["example"] and not card["example_translation"]
    assert card["pinyin"]
    assert store.manual_card(SENTENCE) is None


async def test_a_model_failure_leaves_the_reading_and_the_dictionary_meaning(store):
    glossary = Glossary({SENTENCE: ["нет худа без добра"]})
    card = await ManualCards(store, Model(error=RuntimeError("boom")), glossary).card(SENTENCE)
    assert card["meaning"] == "нет худа без добра"
    assert card["pinyin"] and not card["example"]
    assert note_text(card) == "sài wēng shī mǎ yān zhī fēi fú\n\nнет худа без добра"


async def test_context_is_paid_for_once_and_reused(store):
    model = Model()
    cards = ManualCards(store, model)
    first = await cards.card(SENTENCE)
    second = await cards.card(SENTENCE)
    assert first == second
    assert len(model.payloads) == 1


async def test_a_disabled_study_model_still_writes_a_dictionary_note(store):
    glossary = Glossary({"辛苦了": ["спасибо за труды"]})
    card = await ManualCards(store, None, glossary).card("辛苦了")
    assert note_text(card) == "xīn kǔ le\n\nспасибо за труды"


class BothAPI:
    """Collections and personalizedStories behind one transport."""

    def __init__(self, stories=None):
        self.collections = API()
        self.notes = NoteAPI(stories)

    def __call__(self, request):
        if request.url.host == "securetoken.googleapis.com" or "/collections/data" in str(
            request.url.path
        ):
            return self.collections(request)
        return self.notes(request)


async def test_the_card_and_its_note_both_land(auth, store):
    api = BothAPI()
    service = HanlyUploadService(store, client(auth, api), ManualCards(store, Model()))
    result = await service.add_manual_glyph(SENTENCE)
    assert glyphs_of(api.collections, result.collection_id) == [SENTENCE]
    assert api.notes.stories[SENTENCE] == (
        f"sài wēng shī mǎ yān zhī fēi fú\n\n{MEANING}\n\n原文：{EXAMPLE}\nПеревод：{EXAMPLE_RU}"
    )
    assert result.note == "created"
    assert result.pinyin == "sài wēng shī mǎ yān zhī fēi fú"


async def test_a_note_you_edited_in_hanly_is_never_overwritten(auth, store):
    api = BothAPI({SENTENCE: "Моя собственная заметка"})
    service = HanlyUploadService(store, client(auth, api), ManualCards(store, Model()))
    result = await service.add_manual_glyph(SENTENCE)
    assert result.note == "skipped-user-modified"
    assert api.notes.stories[SENTENCE] == "Моя собственная заметка"
    assert glyphs_of(api.collections, result.collection_id) == [SENTENCE]
    assert "Note: kept yours" in result.message()


async def test_a_failing_note_never_turns_a_stored_card_into_a_failure(auth, store):
    api = BothAPI()
    api.notes.commit_status = 500
    service = HanlyUploadService(store, client(auth, api), ManualCards(store, Model()))
    result = await service.add_manual_glyph(SENTENCE)
    assert not result.present
    assert glyphs_of(api.collections, result.collection_id) == [SENTENCE]
    assert result.note == "failed"


async def test_a_broken_context_builder_never_breaks_the_card(auth, store):
    class Exploding:
        async def card(self, glyph):
            raise RuntimeError("boom")

    api = BothAPI()
    service = HanlyUploadService(store, client(auth, api), Exploding())
    result = await service.add_manual_glyph("辛苦了")
    assert glyphs_of(api.collections, result.collection_id) == ["辛苦了"]
    assert not result.note
    assert result.message() == "✓ Added to Hanly\n\n辛苦了\nCollection: Manual imports"


async def test_telegram_shows_the_reading_and_what_happened_to_the_note(auth, store, config):
    api = BothAPI()
    handlers = BotHandlers(config, store)
    handlers.hanly = HanlyUploadService(store, client(auth, api), ManualCards(store, Model()))
    assert replied(*await send(handlers, f"/add_hanly {SENTENCE}")) == (
        f"✓ Added to Hanly\n\n{SENTENCE}\nsài wēng shī mǎ yān zhī fēi fú\n"
        "Collection: Manual imports\nNote: added"
    )
