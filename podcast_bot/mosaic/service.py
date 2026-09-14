"""Persistent upload snapshots. Each source gets one pack, even after study regeneration."""

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ..identity import mosaic_identity, pack_name
from ..models import UserError
from ..storage import Storage
from ..study.models import StudyMaterial
from ..study.service import pack_valid
from .client import MandarinMosaicClient, MosaicError
from .segmentation import segment_mandarin

IMAGE_URL = "https://images.pexels.com/photos/7657398/pexels-photo-7657398.jpeg"
DESCRIPTION = "Authentic sentences from the podcast transcript."


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

    def stage(
        self,
        source_id: str,
        name: str,
        description: str,
        sentences: list[tuple[str, str]],
        source_reference: str = "",
    ) -> int:
        """Commit every UUID and exact payload BEFORE the first network request.

        Returns the number of newly staged sentences; already staged Mandarin text is
        never staged twice, so a retry cannot duplicate sentences inside the pack.
        """
        row = self.storage.db.execute(
            "SELECT payload FROM mosaic_packs WHERE source_id=?", (source_id,)
        ).fetchone()
        created = datetime.now(UTC).isoformat()
        if row:
            pack = json.loads(row["payload"])
        else:
            pack = {
                "Name": name,
                "Description": description,
                "ImageUrl": IMAGE_URL,
                "Created": created,
                "UniqueIdentifier": str(uuid4()),
                "LastPropertiesChange": created,
                "Deleted": False,
                "ImportedFrom": source_reference or source_id,
                "MakePublic": None,
            }
        known = {
            json.loads(r["payload"])["Mandarin"]
            for r in self.storage.db.execute(
                "SELECT payload FROM mosaic_sentences WHERE source_id=?", (source_id,)
            )
        }
        staged = []
        for chinese, english in sentences:
            if chinese in known:
                continue
            known.add(chinese)
            segmented = self.segmenter(chinese)
            if not segmented or "".join(segmented.split()) != "".join(chinese.split()):
                raise UserError("Mandarin Mosaic segmentation failed to preserve the sentence.")
            staged.append(
                {
                    "UniqueIdentifier": str(uuid4()),
                    "Mandarin": chinese,
                    "Deleted": False,
                    "SegmentedMandarin": segmented,
                    "English": english,
                    "ImageUrl": "",
                    "PackId": pack["UniqueIdentifier"],
                    "EnglishReviewed": False,
                    "LastUpdated": None,
                    "ClozePosition": None,
                }
            )
        with self.storage.db:
            if not row:
                self.storage.db.execute(
                    "INSERT INTO mosaic_packs VALUES (?,?,?,?)",
                    (
                        source_id,
                        pack["UniqueIdentifier"],
                        json.dumps(pack, ensure_ascii=False),
                        "pending",
                    ),
                )
            self.storage.db.executemany(
                "INSERT INTO mosaic_sentences VALUES (?,?,?,?)",
                [
                    (s["UniqueIdentifier"], source_id, json.dumps(s, ensure_ascii=False), "pending")
                    for s in staged
                ],
            )
        return len(staged)

    def _prepare(self, path: Path) -> str:
        if not pack_valid(path):
            raise UserError("No complete study pack yet. Send a link or use /regenerate.")
        metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        if metadata["study_settings"]["target_language"] != "zh":
            raise UserError("Mandarin Mosaic upload requires a Chinese study pack.")
        source_id = mosaic_identity(metadata, path)
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
        for sentence in material.mosaic_sentences:
            if (
                not sentence.chinese.strip()
                or sentence.chinese not in text
                or not sentence.english.strip()
            ):
                raise UserError("A selected Mandarin Mosaic sentence failed source validation.")
        self.stage(
            source_id,
            pack_name(metadata),
            DESCRIPTION,
            [(s.chinese, s.english) for s in material.mosaic_sentences],
        )
        return source_id

    def sentence_status(self, source_id: str) -> dict[str, str]:
        return {
            json.loads(row["payload"])["Mandarin"]: row["status"]
            for row in self.storage.db.execute(
                "SELECT payload,status FROM mosaic_sentences WHERE source_id=?", (source_id,)
            )
        }

    async def _upload(self, source_id: str) -> UploadResult:
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

    async def upload_sentences_for(
        self,
        source_id: str,
        name: str,
        description: str,
        sentences: list[tuple[str, str]],
        source_reference: str = "",
    ) -> UploadResult:
        """Reader entry point: stage the selected sentences, then upload the pending ones."""
        async with self._lock:
            self.stage(source_id, name, description, sentences, source_reference)
            return await self._upload(source_id)

    async def create_study_pack(self, path: Path) -> UploadResult:
        async with self._lock:
            return await self._upload(self._prepare(path))
