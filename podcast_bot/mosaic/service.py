"""Persistent upload snapshots. Each episode gets one pack, even after study regeneration."""

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ..models import UserError
from ..storage import Storage
from ..study.models import StudyMaterial
from ..study.service import pack_valid
from .client import MandarinMosaicClient, MosaicError
from .segmentation import segment_mandarin

IMAGE_URL = "https://images.pexels.com/photos/7657398/pexels-photo-7657398.jpeg"


@dataclass(frozen=True)
class UploadResult:
    pack_id: str
    name: str
    total: int
    uploaded: int
    rejected: int
    unconfirmed: int
    error: str | None = None

    def message(self) -> str:
        text = f"Mandarin Mosaic:\n✓ Pack created: {self.name}\n"
        text += f"{'✓' if self.uploaded == self.total else '⚠'} {self.uploaded}/{self.total} sentences uploaded"
        if self.rejected:
            text += f"\n{self.rejected} sentences were rejected by Mandarin Mosaic"
        if self.unconfirmed:
            text += f"\n{self.unconfirmed} sentences have unconfirmed delivery"
        if self.error:
            text += "\n" + self.error
        elif self.uploaded < self.total:
            text += "\nUse /mosaic to retry the remaining sentences."
        return text


class MosaicUploadService:
    def __init__(
        self,
        storage: Storage,
        client: MandarinMosaicClient,
        segmenter: Callable[[str], str] = segment_mandarin,
    ):
        self.storage, self.client, self.segmenter = storage, client, segmenter
        self._lock = asyncio.Lock()

    def _prepare(self, path: Path) -> str:
        if not pack_valid(path):
            raise UserError("No complete study pack yet. Send a link or use /regenerate.")
        metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        if metadata["study_settings"]["target_language"] != "zh":
            raise UserError("Mandarin Mosaic upload requires a Chinese study pack.")
        source_id = (
            f"apple:{metadata['podcast_id']}:{metadata['episode_id']}"
            if metadata.get("podcast_id") and metadata.get("episode_id")
            else "file:" + str(metadata.get("canonical_source", path.name))
        )
        if self.storage.db.execute(
            "SELECT 1 FROM mosaic_packs WHERE source_id=?", (source_id,)
        ).fetchone():
            return source_id
        material = StudyMaterial.model_validate_json(
            (path / "study.json").read_text(encoding="utf-8")
        )
        canonical = (path / "transcript.txt").read_bytes()
        if hashlib.sha256(canonical).hexdigest() != metadata["transcript_sha256"]:
            raise UserError("The saved study transcript failed validation. Use /regenerate.")
        text = canonical.decode("utf-8")
        if not material.mosaic_sentences:
            raise UserError("This study pack has no selected Mandarin Mosaic sentences.")
        pack_id, created = str(uuid4()), datetime.now(UTC).isoformat()
        name = f"{metadata.get('podcast', 'Podcast')} — {metadata.get('title', 'Episode')}"[:250]
        pack = {
            "Name": name,
            "Description": "Authentic sentences from the podcast transcript.",
            "ImageUrl": IMAGE_URL,
            "Created": created,
            "UniqueIdentifier": pack_id,
            "LastPropertiesChange": created,
            "Deleted": False,
            "ImportedFrom": source_id,
            "MakePublic": None,
        }
        sentences = []
        for sentence in material.mosaic_sentences:
            if (
                not sentence.chinese.strip()
                or sentence.chinese not in text
                or not sentence.english.strip()
            ):
                raise UserError("A selected Mandarin Mosaic sentence failed source validation.")
            segmented = self.segmenter(sentence.chinese)
            if not segmented or "".join(segmented.split()) != "".join(sentence.chinese.split()):
                raise UserError("Mandarin Mosaic segmentation failed to preserve the sentence.")
            sentences.append(
                {
                    "UniqueIdentifier": str(uuid4()),
                    "Mandarin": sentence.chinese,
                    "Deleted": False,
                    "SegmentedMandarin": segmented,
                    "English": sentence.english,
                    "ImageUrl": "",
                    "PackId": pack_id,
                    "EnglishReviewed": False,
                    "LastUpdated": None,
                    "ClozePosition": None,
                }
            )
        # Commit every UUID and exact payload BEFORE the first network request.
        with self.storage.db:
            self.storage.db.execute(
                "INSERT INTO mosaic_packs VALUES (?,?,?,?)",
                (source_id, pack_id, json.dumps(pack, ensure_ascii=False), "pending"),
            )
            self.storage.db.executemany(
                "INSERT INTO mosaic_sentences VALUES (?,?,?,?)",
                [
                    (s["UniqueIdentifier"], source_id, json.dumps(s, ensure_ascii=False), "pending")
                    for s in sentences
                ],
            )
        return source_id

    async def create_study_pack(self, path: Path) -> UploadResult:
        async with self._lock:
            source_id = self._prepare(path)
            row = self.storage.db.execute(
                "SELECT * FROM mosaic_packs WHERE source_id=?", (source_id,)
            ).fetchone()
            pack = json.loads(row["payload"])
            if row["status"] != "created":
                with self.storage.db:
                    self.storage.db.execute(
                        "UPDATE mosaic_packs SET status='unconfirmed' WHERE source_id=?",
                        (source_id,),
                    )
                await self.client.create_pack(pack)
                with self.storage.db:
                    self.storage.db.execute(
                        "UPDATE mosaic_packs SET status='created' WHERE source_id=?", (source_id,)
                    )
            pending = self.storage.db.execute(
                "SELECT * FROM mosaic_sentences WHERE source_id=? AND status!='uploaded' ORDER BY rowid",
                (source_id,),
            ).fetchall()
            error = None
            if pending:
                with self.storage.db:
                    self.storage.db.execute(
                        "UPDATE mosaic_sentences SET status='unconfirmed' WHERE source_id=? AND status!='uploaded'",
                        (source_id,),
                    )
                try:
                    result = await self.client.upload_sentences(
                        [json.loads(r["payload"]) for r in pending]
                    )
                    with self.storage.db:
                        for ids, status in (
                            (result.successful, "uploaded"),
                            (result.rejected, "rejected"),
                        ):
                            self.storage.db.executemany(
                                "UPDATE mosaic_sentences SET status=? WHERE uuid=?",
                                [(status, identifier) for identifier in ids],
                            )
                except MosaicError as exc:
                    error = str(exc)  # Client errors contain fixed, local text only.
            counts = dict(
                self.storage.db.execute(
                    "SELECT status,count(*) FROM mosaic_sentences WHERE source_id=? GROUP BY status",
                    (source_id,),
                ).fetchall()
            )
            return UploadResult(
                row["uuid"],
                pack["Name"],
                sum(counts.values()),
                counts.get("uploaded", 0),
                counts.get("rejected", 0),
                counts.get("unconfirmed", 0) + counts.get("pending", 0),
                error,
            )
