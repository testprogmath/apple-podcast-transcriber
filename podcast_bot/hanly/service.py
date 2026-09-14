import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from ..storage import Storage
from ..study.models import StudyMaterial
from ..study.service import pack_valid
from .auth import HanlyError
from .client import HanlyClient, merge_glyphs


@dataclass(frozen=True)
class HanlyResult:
    collection_id: str
    name: str
    requested: int
    total: int

    def message(self):
        return f"Hanly:\n✓ {self.name}\n✓ {self.requested} selected words/expressions verified ({self.total} cards in collection)"


class HanlyUploadService:
    def __init__(self, storage: Storage, client: HanlyClient):
        self.storage, self.client = storage, client
        self._lock = asyncio.Lock()

    async def upload(self, path: Path) -> HanlyResult:
        async with self._lock:
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
            if all(str(metadata.get(k, "")).isdigit() for k in ("podcast_id", "episode_id")):
                episode = f"apple:{metadata['podcast_id']}:{metadata['episode_id']}"
            elif metadata.get("feed_url") and metadata.get("guid"):
                episode = (
                    "rss:"
                    + hashlib.sha256(
                        json.dumps([metadata["feed_url"], metadata["guid"]]).encode()
                    ).hexdigest()
                )
            else:
                raise HanlyError("Hanly upload requires a stable episode identity in metadata.")
            name = f"{metadata.get('podcast', 'Podcast')[:40]}｜{metadata.get('title', 'Episode')[:80]}"
            comment = str(metadata.get("apple_url") or episode)[:1000]
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
