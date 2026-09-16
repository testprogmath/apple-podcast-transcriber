import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from ..identity import collection_name, hanly_identity
from ..models import UserError
from ..storage import Storage
from ..study.models import StudyMaterial
from ..study.service import pack_valid
from . import manual
from .auth import HanlyError
from .cards import note_text
from .client import HanlyClient, merge_glyphs

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HanlyResult:
    collection_id: str
    name: str
    requested: int
    total: int

    def message(self):
        return f"Hanly:\n✓ {self.name}\n✓ {self.requested} selected words/expressions verified ({self.total} cards in collection)"


NOTE_REPORT = {
    "created": "Note: added",
    "updated": "Note: updated",
    "unchanged": "Note: already there",
    "skipped-user-modified": "Note: kept yours",
    "failed": "Note: could not be written",
}


@dataclass(frozen=True)
class ManualResult:
    collection_id: str
    name: str
    glyph: str
    present: bool
    total: int
    pinyin: str = ""
    note: str = ""

    def message(self):
        state = "Already in Hanly" if self.present else "Added to Hanly"
        lines = [f"✓ {state}", "", self.glyph]
        if self.pinyin:
            lines.append(self.pinyin)
        lines.append(f"Collection: {self.name}")
        if self.note in NOTE_REPORT:
            lines.append(NOTE_REPORT[self.note])
        return "\n".join(lines)


@dataclass(frozen=True)
class NoteOutcome:
    glyph: str
    action: str
    error: str | None = None


class HanlyUploadService:
    def __init__(self, storage: Storage, client: HanlyClient, cards=None):
        self.storage, self.client, self.cards = storage, client, cards
        self._lock = asyncio.Lock()

    async def merge_collection(
        self, episode: str, name: str, comment: str, glyphs: list[str]
    ) -> HanlyResult:
        """One stable collection UUID per identity, committed before any network mutation."""
        async with self._lock:
            return await self._merge(episode, name, comment, glyphs)

    async def _merge(self, episode: str, name: str, comment: str, glyphs: list[str]) -> HanlyResult:
        with self.storage.db:
            self.storage.db.execute(
                "INSERT OR IGNORE INTO hanly_collections VALUES (?,?,?)",
                (episode, str(uuid4()), "pending"),
            )
            row = self.storage.db.execute(
                "SELECT uuid FROM hanly_collections WHERE episode_id=?", (episode,)
            ).fetchone()
            self.storage.db.execute(
                "UPDATE hanly_collections SET status='unconfirmed' WHERE episode_id=?",
                (episode,),
            )
        total = await self.client.upsert_episode_collection(
            row["uuid"], name, comment, glyphs, episode
        )
        with self.storage.db:
            self.storage.db.execute(
                "UPDATE hanly_collections SET status='verified' WHERE episode_id=?", (episode,)
            )
        return HanlyResult(row["uuid"], name, len(glyphs), total)

    async def add_manual_glyph(self, glyph: str) -> ManualResult:
        """One arbitrary string, merged verbatim into the stable manual collection.

        The card is the primary operation. Its study note is enrichment written afterwards,
        outside the lock write_notes takes for itself, and never turns a stored card into a
        failure.
        """
        async with self._lock:
            result = await self._add_manual(glyph)
        if self.cards is None:
            return result
        try:
            card = await self.cards.card(glyph)
            story = note_text(card)
        except Exception as exc:
            log.error("glyph=%s note=context-failed exception_type=%s", glyph, type(exc).__name__)
            return result
        if not story:
            return result
        outcomes = await self.write_notes([(glyph, story)], "manual")
        return replace(result, pinyin=card["pinyin"], note=outcomes[0].action if outcomes else "")

    async def _add_manual(self, glyph: str) -> ManualResult:
        stored = self.storage.manual_collection(manual.KEY)
        document = await self.client.get_collections_document()
        row = self.storage.reserve_manual_collection(
            manual.KEY,
            stored["uuid"] if stored else str(uuid4()),
            stored["name"] if stored else manual.distinct_name(document.collections),
        )
        collection = document.collections.get(row["uuid"])
        present = bool(
            collection
            and not collection["deleted"]
            and glyph in {g.strip() for g in collection["glyphs"]}
        )
        total = await self.client.upsert_episode_collection(
            row["uuid"], row["name"], manual.COMMENT, [glyph], manual.KEY
        )
        self.storage.confirm_manual_collection(manual.KEY)
        return ManualResult(row["uuid"], row["name"], glyph, present, total)

    async def write_notes(
        self, notes: list[tuple[str, str]], document_id: str = ""
    ) -> list[NoteOutcome]:
        """Secondary enrichment: one independent result per glyph, never raising."""
        outcomes = []
        async with self._lock:
            for glyph, story in notes:
                try:
                    action = await self.client.upsert_glyph_note(
                        glyph, story, self.storage.hanly_note(glyph)
                    )
                except Exception as exc:
                    log.error(
                        "reader=%s glyph=%s note=failed exception_type=%s",
                        document_id,
                        glyph,
                        type(exc).__name__,
                    )
                    message = (
                        str(exc)
                        if isinstance(exc, UserError)
                        else "Hanly note enrichment failed. Retry the upload for this item."
                    )
                    outcomes.append(NoteOutcome(glyph, "failed", message))
                    continue
                if action != "skipped-user-modified":
                    self.storage.save_hanly_note(glyph, story)
                log.info("reader=%s glyph=%s note=%s", document_id, glyph, action)
                outcomes.append(NoteOutcome(glyph, action))
        return outcomes

    async def upload(self, path: Path) -> HanlyResult:
        if not pack_valid(path):
            raise HanlyError(
                "Hanly requires a complete study pack. Send a link or use /regenerate."
            )
        try:
            metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
            if metadata["study_settings"]["target_language"] != "zh":
                raise HanlyError("Hanly upload requires a Chinese study pack.")
            material = StudyMaterial.model_validate_json(
                (path / "study.json").read_text(encoding="utf-8")
            )
            transcript = (path / "transcript.txt").read_bytes()
            if hashlib.sha256(transcript).hexdigest() != metadata["transcript_sha256"]:
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError, ValidationError):
            raise HanlyError("Hanly study data failed validation. Use /regenerate.") from None
        text = transcript.decode("utf-8")
        glyphs = merge_glyphs([], [v.term for v in material.vocabulary])
        if not glyphs:
            raise HanlyError("This study pack has no selected Hanly vocabulary or expressions.")
        if any(g not in text for g in glyphs):
            raise HanlyError("A selected Hanly item is absent from the canonical transcript.")
        glyphs.sort(key=text.index)
        try:
            episode = hanly_identity(metadata)
        except UserError as exc:
            raise HanlyError(str(exc)) from None
        comment = str(metadata.get("apple_url") or episode)[:1000]
        return await self.merge_collection(episode, collection_name(metadata), comment, glyphs)
