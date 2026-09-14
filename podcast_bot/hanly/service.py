import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from ..identity import collection_name, hanly_identity
from ..models import UserError
from ..storage import Storage
from ..study.models import StudyMaterial
from ..study.service import pack_valid
from .auth import HanlyError
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


@dataclass(frozen=True)
class NoteOutcome:
    glyph: str
    action: str
    error: str | None = None


class HanlyUploadService:
    def __init__(self, storage: Storage, client: HanlyClient):
        self.storage, self.client = storage, client
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
