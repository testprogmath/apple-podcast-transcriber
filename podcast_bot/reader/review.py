"""Authentic-context review. A target owns one schedule, independent of exercise type."""

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta

from .languages import CAPABILITIES, capabilities

GRADES = {"again", "hard", "good", "easy"}


class ReviewError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def schedule(state, grade, timestamp):
    """Small interval/ease heuristic; intentionally not FSRS. See docs/reader-review.md."""
    if grade not in GRADES:
        raise ReviewError(400, "Choose Again, Hard, Good or Easy.")
    interval, ease = state["interval_days"], state["ease"]
    if grade == "again":
        interval, ease = 10 / 1440, max(1.3, ease - 0.2)
    elif grade == "hard":
        interval, ease = max(1, interval * 1.2), max(1.3, ease - 0.15)
    elif grade == "good":
        interval = max(1, interval * ease)
    else:
        interval, ease = max(4, interval * ease * 1.3), min(3, ease + 0.15)
    interval = min(3650, interval)
    return {
        "due_at": (timestamp + timedelta(days=interval)).isoformat(),
        "interval_days": interval,
        "ease": ease,
        "review_count": state["review_count"] + 1,
        "lapse_count": state["lapse_count"] + int(grade == "again"),
        "last_reviewed_at": timestamp.isoformat(),
    }


def exact_span(item, stored=None):
    if stored:
        start, end = stored["start"], stored["end"]
        if item["source_sentence"][start:end] == item["text"]:
            return [start, end]
        return None
    # Legacy rows have no selection offsets. Only unambiguous exact matches are safe.
    source, text = item["source_sentence"], item["text"]
    start = source.find(text)
    if start >= 0 and source.find(text, start + 1) < 0:
        return [start, start + len(text)]
    return None


def exercise_for(count, kind, span, listening):
    if count == 0:
        return "recognition"
    if listening and count % 3 == 2:
        return "listening"
    if kind != "sentence" and span and count % 2 == 1:
        return "cloze"
    return "recognition"


class Review:
    def __init__(self, storage, secret, clock=None):
        self.storage, self.db, self.secret = storage, storage.db, secret
        self.clock = clock or (lambda: datetime.now(UTC))

    def initialize(self, owner):
        languages = [k for k, v in CAPABILITIES.items() if v["internal_save"]]
        placeholders = ",".join("?" for _ in languages)
        with self.db:
            for table, column in (
                ("saved_vocabulary", "vocabulary_id"),
                ("reader_saved_sentences", "sentence_id"),
            ):
                self.db.execute(
                    f"INSERT OR IGNORE INTO reader_review_state(owner_id,{column},due_at) "
                    f"SELECT owner_id,id,created FROM {table} WHERE owner_id=? "
                    f"AND language IN ({placeholders})",
                    (owner, *languages),
                )

    def save_sentence(self, document, sentence, language):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO reader_saved_sentences "
                "(owner_id,document_id,sentence_id,language,source_sentence,source_start,source_end,"
                "target_language,created) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    document.chat_id,
                    document.id,
                    sentence.id,
                    document.language,
                    sentence.text,
                    sentence.source_start,
                    sentence.source_end,
                    language,
                    self.clock().isoformat(),
                ),
            )
        return dict(
            self.db.execute(
                "SELECT * FROM reader_saved_sentences WHERE owner_id=? AND document_id=? "
                "AND sentence_id=? AND source_sentence=?",
                (document.chat_id, document.id, sentence.id, sentence.text),
            ).fetchone()
        )

    def delete_sentence(self, owner, identifier):
        with self.db:
            target = self.db.execute(
                "SELECT id FROM reader_review_state WHERE owner_id=? AND sentence_id=?",
                (owner, identifier),
            ).fetchone()
            if target:
                self.db.execute(
                    "DELETE FROM reader_review_history WHERE owner_id=? AND target_id=?",
                    (owner, target["id"]),
                )
                self.db.execute(
                    "DELETE FROM reader_review_state WHERE owner_id=? AND id=?",
                    (owner, target["id"]),
                )
            self.db.execute(
                "DELETE FROM reader_saved_sentences WHERE owner_id=? AND id=?", (owner, identifier)
            )

    def target(self, owner, identifier):
        state = self.db.execute(
            "SELECT * FROM reader_review_state WHERE owner_id=? AND id=?", (owner, identifier)
        ).fetchone()
        if not state:
            raise ReviewError(404, "Unknown review target.")
        table, key = (
            ("saved_vocabulary", state["vocabulary_id"])
            if state["vocabulary_id"]
            else ("reader_saved_sentences", state["sentence_id"])
        )
        item = self.db.execute(
            f"SELECT * FROM {table} WHERE owner_id=? AND id=?", (owner, key)
        ).fetchone()
        if not item:
            raise ReviewError(404, "Unknown saved context.")
        return dict(state), dict(item)

    def token(self, state, exercise):
        value = f"{state['owner_id']}:{state['id']}:{state['review_count']}:{exercise}"
        return hmac.new(self.secret.encode(), value.encode(), hashlib.sha256).hexdigest()

    def card(self, state, item, listening):
        sentence = state["sentence_id"] is not None
        stored = (
            None
            if sentence
            else self.db.execute(
                "SELECT start,end FROM reader_saved_spans WHERE saved_item_id=?", (item["id"],)
            ).fetchone()
        )
        span = None if sentence else exact_span(item, stored)
        kind = "sentence" if sentence else ("expression" if " " in item["text"] else "lexical")
        exercise = exercise_for(state["review_count"], kind, span, listening)
        document = self.storage.reader_document(item["document_id"])
        return {
            "id": state["id"],
            "kind": kind,
            "language": item["language"],
            "speech_language": capabilities(item["language"])["speech_language"],
            "text": item["source_sentence"],
            "target": item.get("text", ""),
            "span": span,
            "document_id": item["document_id"],
            "sentence_id": item["sentence_id"],
            "title": document.title
            if document and document.chat_id == state["owner_id"]
            else "Reader context",
            "source_start": item["source_start"],
            "source_end": item["source_end"],
            "exercise": exercise,
            "interaction": self.token(state, exercise),
        }

    def batch(self, owner, listening=False):
        self.initialize(owner)
        timestamp = self.clock().isoformat()
        rows = self.db.execute(
            "SELECT id FROM reader_review_state WHERE owner_id=? AND due_at<=? ORDER BY due_at,id LIMIT 10",
            (owner, timestamp),
        ).fetchall()
        summary = self.db.execute(
            "SELECT COUNT(CASE WHEN due_at<=? THEN 1 END) AS due,MIN(due_at) AS next_due_at "
            "FROM reader_review_state WHERE owner_id=?",
            (timestamp, owner),
        ).fetchone()
        return {
            **dict(summary),
            "items": [self.card(*self.target(owner, r["id"]), listening) for r in rows],
            "limit": 10,
        }

    async def answer(self, owner, identifier, lexical, translations):
        state, item = self.target(owner, identifier)
        document = self.storage.reader_document(item["document_id"])
        if not document or document.chat_id != owner:
            raise ReviewError(404, "Original document is unavailable.")
        sentence = next(
            (
                s
                for s in document.sentences()
                if s.id == item["sentence_id"] and s.text == item["source_sentence"]
            ),
            None,
        )
        if state["sentence_id"]:
            cached = self.storage.reader_translation(
                document.id, item["sentence_id"], item["source_sentence"], item["target_language"]
            )
            if cached:
                return {"meaning": cached}
            if sentence:
                result = await translations.resolve(document, sentence, item["target_language"])
                return {"meaning": result["translation"]}
        else:
            if item["meaning"]:
                return {"meaning": item["meaning"]}
            if sentence:
                result = await lexical.resolve(
                    document, sentence, item["text"], item["target_language"]
                )
                with self.db:
                    self.db.execute(
                        "UPDATE saved_vocabulary SET meaning=?,context_note=? WHERE id=? AND owner_id=?",
                        (result["meaning"], result["context_note"], item["id"], owner),
                    )
                return {"meaning": result["meaning"]}
        return {"meaning": "Meaning unavailable for this original context."}

    def grade(self, owner, identifier, data):
        if (
            set(data) != {"grade", "exercise", "interaction"}
            or not isinstance(data.get("grade"), str)
            or data["grade"] not in GRADES
        ):
            raise ReviewError(400, "Send grade, exercise and interaction only.")
        exercise, interaction = data["exercise"], data["interaction"]
        if (
            not isinstance(exercise, str)
            or exercise not in {"recognition", "cloze", "listening"}
            or not isinstance(interaction, str)
            or not re.fullmatch(r"[0-9a-f]{64}", interaction)
        ):
            raise ReviewError(400, "Invalid review interaction.")
        # Serialize across SQLite connections. Both history and scheduling commit together.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state, item = self.target(owner, identifier)
            previous = self.db.execute(
                "SELECT * FROM reader_review_history WHERE interaction=? AND owner_id=? AND target_id=?",
                (interaction, owner, identifier),
            ).fetchone()
            if previous:
                if previous["grade"] != data["grade"] or previous["exercise_type"] != exercise:
                    raise ReviewError(409, "This interaction was already graded differently.")
                result = json.loads(previous["resulting_state"])
            else:
                valid_exercises = {
                    self.card(state, item, enabled)["exercise"] for enabled in (True, False)
                }
                if exercise not in valid_exercises or not hmac.compare_digest(
                    interaction, self.token(state, exercise)
                ):
                    raise ReviewError(409, "Review changed. Reopen Review to refresh.")
                timestamp = self.clock()
                if state["due_at"] > timestamp.isoformat():
                    raise ReviewError(409, "This target is not due.")
                result = schedule(state, data["grade"], timestamp)
                self.db.execute(
                    "INSERT INTO reader_review_history(owner_id,target_id,interaction,exercise_type,grade,"
                    "reviewed_at,previous_state,resulting_state) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        owner,
                        identifier,
                        interaction,
                        exercise,
                        data["grade"],
                        timestamp.isoformat(),
                        json.dumps(state),
                        json.dumps(result),
                    ),
                )
                assignments = ",".join(f"{key}=?" for key in result)
                self.db.execute(
                    f"UPDATE reader_review_state SET {assignments} WHERE id=? AND owner_id=?",
                    (*result.values(), identifier, owner),
                )
            self.db.commit()
            return result
        except Exception:
            self.db.rollback()
            raise
