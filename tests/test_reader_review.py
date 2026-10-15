"""Deterministic authentic-context review, API ownership, migrations and retries."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from test_reader import call
from test_reader import reader as reader

from podcast_bot.reader.documents import from_text
from podcast_bot.reader.review import Review, ReviewError, exact_span, exercise_for
from podcast_bot.storage import Storage

TIME = datetime(2030, 1, 1, tzinfo=UTC)
TEXT = "🙂 Het land was in de greep van oorlog, in de greep van angst."


@pytest.fixture
def context(reader):
    doc = from_text(reader.document.chat_id, TEXT, "Authentic context", language="nl")
    reader.store.save_reader_document(doc)
    sentence = replace(doc.sentences()[0], source_start=1.5, source_end=7.2)
    text = "in de greep van"
    start = TEXT.rindex(text)
    item = reader.store.save_vocabulary(
        doc, sentence, text, "ru", {"meaning": "во власти"}, (start, start + len(text))
    )
    review = Review(reader.store, "test-secret", lambda: TIME)
    return reader, doc, sentence, item, review


def grade_data(card, grade="good"):
    return {"grade": grade, "exercise": card["exercise"], "interaction": card["interaction"]}


def test_lazy_state_preserves_vocabulary_and_exact_context(context):
    reader, doc, sentence, item, review = context
    before = reader.store.saved_vocabulary(doc.chat_id, doc.id)
    batch = review.batch(doc.chat_id)
    card = batch["items"][0]
    assert batch["due"] == 1 and batch["limit"] == 10
    assert card["kind"] == "expression" and card["exercise"] == "recognition"
    assert card["text"] == TEXT and card["span"][0] == TEXT.rindex(item["text"])
    assert card["sentence_id"] == sentence.id and card["document_id"] == doc.id
    assert (card["source_start"], card["source_end"]) == (1.5, 7.2)
    assert reader.store.saved_vocabulary(doc.chat_id, doc.id) == before
    review.batch(doc.chat_id)
    assert reader.store.db.execute("SELECT count(*) FROM reader_review_state").fetchone()[0] == 1


@pytest.mark.parametrize(
    "grade,days,ease,lapses",
    [
        ("again", 10 / 1440, 2.3, 1),
        ("hard", 1, 2.35, 0),
        ("good", 1, 2.5, 0),
        ("easy", 4, 2.65, 0),
    ],
)
def test_schedule_first_and_repeated_reviews(context, grade, days, ease, lapses):
    _, doc, _, _, review = context
    card = review.batch(doc.chat_id)["items"][0]
    result = review.grade(doc.chat_id, card["id"], grade_data(card, grade))
    assert datetime.fromisoformat(result["due_at"]) == TIME + timedelta(days=days)
    assert result["interval_days"] == days and result["ease"] == ease
    assert result["review_count"] == 1 and result["lapse_count"] == lapses
    assert not review.batch(doc.chat_id)["items"]
    review.clock = lambda: TIME + timedelta(days=5)
    later = review.batch(doc.chat_id)["items"][0]
    assert later["exercise"] == "cloze"
    result2 = review.grade(doc.chat_id, later["id"], grade_data(later))
    assert result2["review_count"] == 2
    assert result2["interval_days"] == max(1, days * ease)
    history = review.db.execute("SELECT * FROM reader_review_history ORDER BY id").fetchall()
    assert len(history) == 2 and all(h["owner_id"] == doc.chat_id for h in history)
    assert json.loads(history[1]["previous_state"])["review_count"] == 1


def test_idempotency_stale_conflicting_and_owner(context):
    _, doc, _, _, review = context
    card = review.batch(doc.chat_id)["items"][0]
    data = grade_data(card)
    result = review.grade(doc.chat_id, card["id"], data)
    assert review.grade(doc.chat_id, card["id"], data) == result
    for owner, payload, status in [
        (doc.chat_id + 1, data, 404),
        (doc.chat_id, grade_data(card, "easy"), 409),
        (doc.chat_id, {**data, "interaction": "f" * 64}, 409),
        (doc.chat_id, {**data, "next_due_at": "tomorrow"}, 400),
    ]:
        with pytest.raises(ReviewError) as error:
            review.grade(owner, card["id"], payload)
        assert error.value.status == status
    assert review.db.execute("SELECT count(*) FROM reader_review_history").fetchone()[0] == 1
    assert review.batch(doc.chat_id + 1)["due"] == 0


def test_concurrent_retries_commit_once(context):
    reader, doc, _, _, review = context
    card = review.batch(doc.chat_id)["items"][0]

    def submit(_):
        storage = Storage(reader.store.root)
        try:
            return Review(storage, "test-secret", lambda: TIME).grade(
                doc.chat_id, card["id"], grade_data(card)
            )
        finally:
            storage.db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(submit, range(2)))
    assert first == second
    assert review.db.execute("SELECT count(*) FROM reader_review_history").fetchone()[0] == 1


def test_bounded_deterministic_queue(context):
    reader, doc, _, _, review = context
    for i in range(14):
        other = from_text(doc.chat_id, f"Een authentieke zin {i}.", language="nl")
        reader.store.save_reader_document(other)
        review.save_sentence(other, other.sentences()[0], "ru")
    batch = review.batch(doc.chat_id)
    assert batch["due"] == 15 and len(batch["items"]) == 10
    assert [c["id"] for c in batch["items"]] == [
        c["id"] for c in review.batch(doc.chat_id)["items"]
    ]
    review.grade(doc.chat_id, batch["items"][0]["id"], grade_data(batch["items"][0]))
    assert review.batch(doc.chat_id)["due"] == 14


def test_legacy_spans_and_exercise_policy():
    assert exact_span({"source_sentence": "Een woord, woord.", "text": "woord"}) is None
    assert exact_span({"source_sentence": "Een woord.", "text": "woord"}) == [4, 9]
    assert (
        exact_span({"source_sentence": "Een woord.", "text": "woord"}, {"start": 0, "end": 3})
        is None
    )
    assert [exercise_for(i, "expression", [4, 9], True) for i in range(6)] == [
        "recognition",
        "cloze",
        "listening",
        "cloze",
        "recognition",
        "listening",
    ]
    assert all(exercise_for(i, "sentence", None, False) == "recognition" for i in range(9))
    assert all(exercise_for(i, "sentence", None, True) != "cloze" for i in range(9))
    assert exercise_for(2, "sentence", None, True) == "listening"
    assert exercise_for(2, "expression", [4, 9], False) == "recognition"


def test_sentence_distinct_idempotent_timing_and_rename(context):
    reader, doc, sentence, item, review = context
    saved = review.save_sentence(doc, sentence, "ru")
    assert review.save_sentence(doc, sentence, "ru") == saved
    cards = review.batch(doc.chat_id)["items"]
    assert {c["kind"] for c in cards} == {"expression", "sentence"}
    assert reader.store.saved_vocabulary(doc.chat_id, doc.id) == [item]
    reader.store.rename_reader_document(doc.id, doc.chat_id, "Renamed")
    sentence_card = next(c for c in review.batch(doc.chat_id)["items"] if c["kind"] == "sentence")
    assert sentence_card["title"] == "Renamed" and sentence_card["span"] is None
    assert sentence_card["text"] == TEXT and sentence_card["source_start"] == 1.5
    review.delete_sentence(doc.chat_id + 1, saved["id"])
    assert review.batch(doc.chat_id)["due"] == 2
    review.delete_sentence(doc.chat_id, saved["id"])
    assert review.batch(doc.chat_id)["due"] == 1


async def test_reveal_reuses_meaning_and_translation_only_lazy_generation(context):
    reader, doc, sentence, _, review = context
    review.save_sentence(doc, sentence, "ru")
    reader.store.save_reader_translation(doc.id, sentence.id, sentence.text, "Перевод.")
    lexical, translations = AsyncMock(), AsyncMock()
    cards = review.batch(doc.chat_id)["items"]
    assert (await review.answer(doc.chat_id, cards[0]["id"], lexical, translations))[
        "meaning"
    ] == "во власти"
    assert (await review.answer(doc.chat_id, cards[1]["id"], lexical, translations))[
        "meaning"
    ] == "Перевод."
    lexical.resolve.assert_not_called()
    translations.resolve.assert_not_called()
    with pytest.raises(ReviewError):
        await review.answer(doc.chat_id + 1, cards[0]["id"], lexical, translations)
    reader.store.db.execute("UPDATE saved_vocabulary SET meaning='' WHERE id=1")
    reader.store.db.commit()
    lexical.resolve.return_value = {"meaning": "новое значение", "context_note": ""}
    await review.answer(doc.chat_id, cards[0]["id"], lexical, translations)
    await review.answer(doc.chat_id, cards[0]["id"], lexical, translations)
    lexical.resolve.assert_awaited_once()


def test_reopen_schema_is_backward_compatible(context):
    reader, doc, _, _, review = context
    before = reader.store.saved_vocabulary(doc.chat_id, doc.id)
    card = review.batch(doc.chat_id)["items"][0]
    result = review.grade(doc.chat_id, card["id"], grade_data(card))
    reader.store.db.close()
    reopened = Storage(reader.store.root)
    assert reopened.saved_vocabulary(doc.chat_id, doc.id) == before
    assert Review(reopened, "test-secret", lambda: TIME).batch(doc.chat_id)["due"] == 0
    assert (
        reopened.db.execute("SELECT due_at FROM reader_review_state").fetchone()[0]
        == result["due_at"]
    )


async def test_api_save_batch_reveal_grade_auth_and_sentence(context):
    reader, doc, sentence, _, _ = context
    base = f"/api/reader/{doc.id}"
    status, saved = await call(
        reader, "POST", base + "/saved-sentences", {"sentence_id": sentence.id}
    )
    assert status == 200
    status, duplicate = await call(
        reader, "POST", base + "/saved-sentences", {"sentence_id": sentence.id}
    )
    assert saved == duplicate
    status, batch = await call(reader, "POST", "/api/reader/review", {"listening": False})
    assert status == 200 and batch["due"] == 2
    lexical_card = next(c for c in batch["items"] if c["kind"] == "expression")
    path = f"/api/reader/review/{lexical_card['id']}"
    assert (await call(reader, "POST", path + "/answer", {}))[1]["meaning"] == "во власти"
    assert (await call(reader, "POST", path + "/grade", grade_data(lexical_card)))[0] == 200
    assert (await call(reader, "POST", path + "/grade", grade_data(lexical_card)))[0] == 200
    assert (await call(reader, "GET", "/api/reader/review", headers={}))[0] == 401
    assert (
        await call(reader, "POST", path + "/grade", {**grade_data(lexical_card), "due_at": "x"})
    )[0] == 400
    assert (await call(reader, "DELETE", base + f"/saved-sentences/{saved['item']['id']}"))[
        0
    ] == 200


async def test_api_exact_selected_occurrence_new_vocabulary_and_ownership(reader):
    doc = from_text(reader.document.chat_id, "woord, woord.", language="nl")
    reader.store.save_reader_document(doc)
    reader.api.lexical.cached = lambda *args: {"meaning": "слово"}
    payload = {"sentence_id": 0, "start": 7, "end": 12, "text": "woord"}
    status, _ = await call(reader, "POST", f"/api/reader/{doc.id}/saved-vocabulary", payload)
    assert status == 200
    batch = (await call(reader, "GET", "/api/reader/review"))[1]
    assert batch["items"][0]["span"] == [7, 12]
    foreign = from_text(doc.chat_id + 1, "Andere zin.", language="nl")
    reader.store.save_reader_document(foreign)
    assert (
        await call(reader, "POST", f"/api/reader/{foreign.id}/saved-sentences", {"sentence_id": 0})
    )[0] == 404
    review = Review(reader.store, reader.api.config.token)
    review.save_sentence(foreign, foreign.sentences()[0], "ru")
    foreign_card = review.batch(foreign.chat_id)["items"][0]
    base = f"/api/reader/review/{foreign_card['id']}"
    assert (await call(reader, "POST", base + "/answer", {}))[0] == 404
    assert (await call(reader, "POST", base + "/grade", grade_data(foreign_card)))[0] == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"grade": [], "exercise": "cloze", "interaction": "x"},
        {"grade": "good", "exercise": [], "interaction": "x"},
        {"grade": "good", "exercise": "cloze", "interaction": []},
    ],
)
async def test_malformed_grade_returns_400(context, payload):
    reader, doc, _, _, _ = context
    card = (await call(reader, "GET", "/api/reader/review"))[1]["items"][0]
    assert (await call(reader, "POST", f"/api/reader/review/{card['id']}/grade", payload))[0] == 400


def test_upgrade_from_database_without_review_tables_preserves_existing_rows(reader):
    doc = from_text(reader.document.chat_id, "Een woord, woord.", language="nl")
    reader.store.save_reader_document(doc)
    reader.store.save_vocabulary(doc, doc.sentences()[0], "woord", "ru", {"meaning": "слово"})
    before = reader.store.saved_vocabulary(doc.chat_id, doc.id)
    with reader.store.db:
        for table in (
            "reader_review_history",
            "reader_review_state",
            "reader_saved_spans",
            "reader_saved_sentences",
        ):
            reader.store.db.execute(f"DROP TABLE {table}")
    reader.store.db.close()
    reopened = Storage(reader.store.root)
    review = Review(reopened, "test-secret", lambda: TIME)
    card = review.batch(doc.chat_id)["items"][0]
    assert card["span"] is None and card["exercise"] == "recognition"
    assert reopened.saved_vocabulary(doc.chat_id, doc.id) == before
    assert review.batch(doc.chat_id, True)["due"] == 1


def test_deleted_sentence_interaction_cannot_grade_a_new_target(context):
    _, doc, sentence, _, review = context
    saved = review.save_sentence(doc, sentence, "ru")
    card = next(c for c in review.batch(doc.chat_id)["items"] if c["kind"] == "sentence")
    review.delete_sentence(doc.chat_id, saved["id"])
    review.save_sentence(doc, sentence, "ru")
    new_card = next(c for c in review.batch(doc.chat_id)["items"] if c["kind"] == "sentence")
    assert new_card["id"] != card["id"]
    with pytest.raises(ReviewError):
        review.grade(doc.chat_id, new_card["id"], grade_data(card))


@pytest.mark.parametrize("value", ["not-a-token", "а" * 64, 123, {}])
async def test_invalid_interaction_is_400(context, value):
    reader, _, _, _, _ = context
    card = (await call(reader, "GET", "/api/reader/review"))[1]["items"][0]
    data = {**grade_data(card), "interaction": value}
    assert (await call(reader, "POST", f"/api/reader/review/{card['id']}/grade", data))[0] == 400


async def test_chinese_review_remains_disabled_and_client_cannot_choose_exercise(reader):
    doc = reader.document
    assert (
        await call(
            reader,
            "POST",
            f"/api/reader/{doc.id}/saved-sentences",
            {"sentence_id": doc.sentences()[0].id},
        )
    )[0] == 400
    reader.store.save_vocabulary(
        doc, doc.sentences()[0], doc.sentences()[0].text, "ru", {"meaning": "test"}
    )
    assert (await call(reader, "GET", "/api/reader/review"))[1]["due"] == 0
    dutch = from_text(doc.chat_id, "Een woord.", language="nl")
    reader.store.save_reader_document(dutch)
    reader.store.save_vocabulary(dutch, dutch.sentences()[0], "woord", "ru", {"meaning": "слово"})
    card = (await call(reader, "GET", "/api/reader/review"))[1]["items"][0]
    data = {**grade_data(card), "exercise": "cloze"}
    assert (await call(reader, "POST", f"/api/reader/review/{card['id']}/grade", data))[0] == 409


def test_invalid_saved_span_rolls_back_vocabulary(reader):
    doc = from_text(reader.document.chat_id, "Een woord.", language="nl")
    reader.store.save_reader_document(doc)
    with pytest.raises(ValueError):
        reader.store.save_vocabulary(doc, doc.sentences()[0], "woord", "ru", {}, (0, 3))
    assert reader.store.saved_vocabulary(doc.chat_id, doc.id) == []


def test_vocabulary_delete_cannot_orphan_span_or_rebind_to_new_occurrence(context):
    import sqlite3

    reader, doc, sentence, item, review = context
    card = review.batch(doc.chat_id)["items"][0]
    review.grade(doc.chat_id, card["id"], grade_data(card))
    with pytest.raises(sqlite3.IntegrityError), reader.store.db:
        reader.store.db.execute("DELETE FROM saved_vocabulary WHERE id=?", (item["id"],))
    repeated = reader.store.save_vocabulary(
        doc,
        sentence,
        item["text"],
        "ru",
        {"meaning": "во власти"},
        (TEXT.index(item["text"]), TEXT.index(item["text"]) + len(item["text"])),
    )
    assert repeated["id"] == item["id"]
    state, saved = review.target(doc.chat_id, card["id"])
    assert review.card(state, saved, False)["span"] == card["span"]
    assert state["review_count"] == 1
    assert reader.store.db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert not reader.store.db.execute("PRAGMA foreign_key_check").fetchall()


def test_unscheduled_vocabulary_delete_is_blocked_by_saved_span(context):
    import sqlite3

    reader, _, _, item, _ = context
    with pytest.raises(sqlite3.IntegrityError), reader.store.db:
        reader.store.db.execute("DELETE FROM saved_vocabulary WHERE id=?", (item["id"],))
    assert reader.store.db.execute("SELECT count(*) FROM reader_saved_spans").fetchone()[0] == 1


def test_explicit_child_cleanup_allows_resave_with_new_review_identity(context):
    reader, doc, sentence, item, review = context
    card = review.batch(doc.chat_id)["items"][0]
    review.grade(doc.chat_id, card["id"], grade_data(card))
    with reader.store.db:
        reader.store.db.execute(
            "DELETE FROM reader_review_history WHERE target_id=?", (card["id"],)
        )
        reader.store.db.execute("DELETE FROM reader_review_state WHERE id=?", (card["id"],))
        reader.store.db.execute(
            "DELETE FROM reader_saved_spans WHERE saved_item_id=?", (item["id"],)
        )
        reader.store.db.execute("DELETE FROM saved_vocabulary WHERE id=?", (item["id"],))
    start = TEXT.index(item["text"])
    new_item = reader.store.save_vocabulary(
        doc, sentence, item["text"], "ru", {}, (start, start + len(item["text"]))
    )
    new_card = review.batch(doc.chat_id)["items"][0]
    assert (
        new_item["id"] == item["id"]
    )  # Parent ID reuse is safe only after explicit child cleanup.
    assert new_card["id"] > card["id"] and new_card["span"][0] == start
    with pytest.raises(ReviewError):
        review.grade(doc.chat_id, new_card["id"], grade_data(card))


def test_sentence_service_delete_cleans_history_and_state_with_enforced_foreign_keys(context):
    reader, doc, sentence, _, review = context
    saved = review.save_sentence(doc, sentence, "ru")
    card = next(c for c in review.batch(doc.chat_id)["items"] if c["kind"] == "sentence")
    review.grade(doc.chat_id, card["id"], grade_data(card))
    review.delete_sentence(doc.chat_id, saved["id"])
    for table, column in (
        ("reader_saved_sentences", "id"),
        ("reader_review_state", "id"),
        ("reader_review_history", "target_id"),
    ):
        key = saved["id"] if table == "reader_saved_sentences" else card["id"]
        assert not reader.store.db.execute(
            f"SELECT 1 FROM {table} WHERE {column}=?", (key,)
        ).fetchone()
    assert not reader.store.db.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize(
    "table", ["reader_saved_spans", "reader_review_state", "reader_review_history"]
)
def test_open_rejects_preexisting_orphan_review_links(tmp_path, table):
    import sqlite3

    storage = Storage(tmp_path)
    storage.db.execute("PRAGMA foreign_keys=OFF")
    with storage.db:
        if table == "reader_saved_spans":
            storage.db.execute("INSERT INTO reader_saved_spans VALUES (999,0,5)")
        elif table == "reader_review_state":
            storage.db.execute(
                "INSERT INTO reader_review_state(owner_id,vocabulary_id,due_at) VALUES (42,999,'2030-01-01')"
            )
        else:
            storage.db.execute(
                "INSERT INTO reader_review_history(owner_id,target_id,interaction,exercise_type,grade,reviewed_at,previous_state,resulting_state) VALUES (42,999,'x','recognition','good','2030-01-01','{}','{}')"
            )
    storage.db.close()
    with pytest.raises(
        sqlite3.IntegrityError, match=f"Invalid Reader review references in {table}"
    ):
        Storage(tmp_path)
