import hashlib
import json
import math
import shutil
import tempfile
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path

from ..models import Job, UserError
from ..storage import Storage, atomic_json, safe_name
from .chunking import batches, normalized, split_blocks, validate_chunk
from .client import StudyRequests
from .lexical import (
    FORMAT,
    PROMPT,
    LexicalChunk,
    as_material,
    validate_lexical,
    vocabulary_markdown,
)
from .models import Selection, StudyMaterial, chunk_schema
from .mosaic import select_candidates
from .prompts import CHUNK_PROMPT, RANK_PROMPT
from .render import (
    hanly_csv,
    mosaic_csv,
    pinyin_markdown,
    reader_markdown,
    study_markdown,
    translation_markdown,
)
from .settings import StudySettings, study_key


def deduplicate(items: list, field: str) -> list:
    result, seen = [], set()
    for item in items:
        key = normalized(getattr(item, field)).casefold()
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result


def pack_valid(path: Path) -> bool:
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        required = {"transcript.txt", "metadata.json", "study.json"}
        required |= (
            {"vocabulary.md"}
            if metadata.get("pack_format") == FORMAT
            else {"reader.md", "study.md", "hanly.csv"}
        )
        return (
            isinstance(manifest, dict)
            and required.issubset(manifest)
            and all(
                (path / name).is_file()
                and hashlib.sha256((path / name).read_bytes()).hexdigest() == digest
                for name, digest in manifest.items()
            )
        )
    except (OSError, ValueError, TypeError):
        return False


class StudyService:
    def __init__(self, storage: Storage, requests: StudyRequests):
        self.storage, self.requests = storage, requests

    def cached(self, source: Path, settings: StudySettings) -> Path | None:
        key = study_key((source / "transcript.txt").read_bytes(), settings)
        row = self.storage.db.execute("SELECT path FROM study_cache WHERE key=?", (key,)).fetchone()
        path = Path(row[0]) if row else self.storage.root / "study" / key
        return path if pack_valid(path) else None

    async def generate(
        self,
        source: Path,
        settings: StudySettings,
        job: Job,
        status: Callable[[str], Awaitable[None]],
        regenerate: bool = False,
    ) -> Path:
        canonical = (source / "transcript.txt").read_bytes()
        base_key = study_key(canonical, settings)
        key = f"{base_key}-run-{job.id}" if regenerate else base_key
        with self.storage.db:
            self.storage.db.execute("INSERT OR REPLACE INTO study_runs VALUES (?,?)", (job.id, key))
        existing = self.storage.root / "study" / key
        cached = (
            existing
            if regenerate and pack_valid(existing)
            else (None if regenerate else self.cached(source, settings))
        )
        if cached:
            await status("Study pack already prepared.")
            return cached
        blocks = split_blocks(canonical.decode("utf-8"))
        groups = batches(blocks, settings.chunk_chars)
        metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
        chunks = []
        for index, group in enumerate(groups):
            await status(
                f"Preparing study materials… {index + 1}/{len(groups)}\nLevel: {settings.learner_level}\nNative language: {settings.native_language}"
            )
            result = await self.requests.call(
                job_id=job.id,
                key=key,
                step=f"chunk-{index}",
                model=settings.model,
                schema=chunk_schema(len(group))
                if settings.target_language == "zh"
                else LexicalChunk,
                instructions=CHUNK_PROMPT if settings.target_language == "zh" else PROMPT,
                payload={
                    "settings": asdict(settings),
                    "episode_title": metadata.get("title", "")[:300],
                    "blocks": [asdict(b) for b in group],
                },
                validate=lambda material, group=group: (
                    validate_chunk(
                        material,
                        group,
                        settings.target_language,
                        settings.native_language,
                    )
                    if settings.target_language == "zh"
                    else validate_lexical(material, group)
                ),
            )
            chunks.append(result if settings.target_language == "zh" else as_material(result))
        material = StudyMaterial(
            passages=[p for c in chunks for p in c.passages],
            vocabulary=deduplicate([x for c in chunks for x in c.vocabulary], "term"),
            patterns=deduplicate([x for c in chunks for x in c.patterns], "pattern"),
            pragmatics=deduplicate([x for c in chunks for x in c.pragmatics], "title"),
            cultural_references=deduplicate(
                [x for c in chunks for x in c.cultural_references], "title"
            ),
            mosaic_sentences=select_candidates(
                [x for c in chunks for x in c.mosaic_sentences], canonical.decode("utf-8")
            ),
            possible_asr_errors=deduplicate(
                [x for c in chunks for x in c.possible_asr_errors], "original"
            ),
        )
        await status(
            "Selecting useful vocabulary, patterns, and cultural notes across the episode…"
        )
        material = await self.rank(material, settings, job, key)
        # A modified source must not be associated with results for its old hash.
        if (source / "transcript.txt").read_bytes() != canonical:
            raise UserError("The canonical transcript changed during processing. Please retry.")
        root = self.storage.root / "study"
        root.mkdir(exist_ok=True)
        destination = root / key
        with tempfile.TemporaryDirectory(prefix=".pack-", dir=root) as temp:
            output = Path(temp)
            (output / "transcript.txt").write_bytes(canonical)
            if (source / "audio-timing.json").is_file():
                shutil.copyfile(source / "audio-timing.json", output / "audio-timing.json")
            if (source / "transcript.srt").is_file():
                shutil.copyfile(source / "transcript.srt", output / "transcript.srt")
            if settings.target_language == "zh":
                (output / "transcript_pinyin.md").write_text(
                    pinyin_markdown(material), encoding="utf-8"
                )
            if settings.target_language == "zh":
                (output / f"translation_{settings.native_language}.md").write_text(
                    translation_markdown(material), encoding="utf-8"
                )
                (output / "reader.md").write_text(
                    reader_markdown(material, settings), encoding="utf-8"
                )
                (output / "mandarin_mosaic.csv").write_text(mosaic_csv(material), encoding="utf-8")
                (output / "study.md").write_text(
                    study_markdown(material, settings), encoding="utf-8"
                )
                (output / "hanly.csv").write_text(hanly_csv(material, settings), encoding="utf-8")
            else:
                (output / "vocabulary.md").write_text(
                    vocabulary_markdown(material), encoding="utf-8"
                )
            atomic_json(output / "study.json", material.model_dump())
            zip_name = safe_name(metadata.get("title", "episode"), 100) + "-study-pack.zip"
            atomic_json(
                output / "metadata.json",
                {
                    **metadata,
                    "study_settings": asdict(settings),
                    "transcript_sha256": hashlib.sha256(canonical).hexdigest(),
                    "canonical_source": str(source),
                    "study_key": key,
                    "vocabulary_count": len(material.vocabulary),
                    "pattern_count": len(material.patterns),
                    "mosaic_sentence_count": len(material.mosaic_sentences),
                    "correction_policy": "suggestions_only",
                    "possible_asr_errors": [x.model_dump() for x in material.possible_asr_errors],
                    "study_complete": True,
                    "pack_format": "mandarin-full" if settings.target_language == "zh" else FORMAT,
                    "zip_filename": zip_name,
                },
            )
            with zipfile.ZipFile(output / zip_name, "w", zipfile.ZIP_DEFLATED) as archive:
                for file in sorted(output.iterdir()):
                    if file.suffix != ".zip":
                        archive.write(file, file.name)
            atomic_json(
                output / "manifest.json",
                {
                    file.name: hashlib.sha256(file.read_bytes()).hexdigest()
                    for file in output.iterdir()
                },
            )
            if destination.exists():
                shutil.rmtree(
                    destination
                )  # Invalid derived pack only, canonical data is elsewhere.
            output.rename(destination)
        with self.storage.db:
            self.storage.db.execute(
                "INSERT OR REPLACE INTO study_cache VALUES (?,?)", (base_key, str(destination))
            )
        return destination

    async def rank(
        self, material: StudyMaterial, settings: StudySettings, job: Job, key: str
    ) -> StudyMaterial:
        candidates = []
        for kind in (
            "vocabulary",
            "patterns",
            "pragmatics",
            "cultural_references",
            "mosaic_sentences",
        ):
            for item in getattr(material, kind):
                candidates.append((kind, item))
        if not candidates:
            return material
        # Rolling tournament bounds every global request, even for very long episodes.
        shortlist: list[int] = []
        maximum = min(50, math.ceil(settings.vocab_target * 1.5))
        mosaic_maximum = min(40, math.ceil(settings.mosaic_target * 1.5))
        for start in range(0, len(candidates), 40):
            ids = shortlist + list(range(start, min(start + 40, len(candidates))))
            payload = {
                "level": settings.learner_level,
                "native_language": settings.native_language,
                "vocab_target": settings.vocab_target,
                "maximum_vocabulary": maximum,
                "mosaic_target": settings.mosaic_target,
                "maximum_mosaic": mosaic_maximum,
                "candidates": [
                    {
                        "id": i,
                        "kind": candidates[i][0],
                        "title": getattr(
                            candidates[i][1],
                            "term",
                            getattr(
                                candidates[i][1],
                                "pattern",
                                getattr(
                                    candidates[i][1],
                                    "title",
                                    getattr(candidates[i][1], "chinese", ""),
                                ),
                            ),
                        )[:150],
                        "meaning": getattr(
                            candidates[i][1],
                            "meaning",
                            getattr(
                                candidates[i][1],
                                "explanation",
                                getattr(candidates[i][1], "reason", ""),
                            ),
                        )[:300],
                        "example": getattr(
                            candidates[i][1], "example", getattr(candidates[i][1], "chinese", "")
                        )[:300],
                    }
                    for i in ids
                ],
            }

            def validate(selection, ids=ids):
                selected = selection.selected_ids
                if len(set(selected)) != len(selected) or any(i not in ids for i in selected):
                    raise UserError(
                        "Global study selection returned invalid candidate IDs. Use /retry."
                    )
                for kind, limit in [
                    ("vocabulary", maximum),
                    ("patterns", 8),
                    ("pragmatics", 6),
                    ("cultural_references", 6),
                    ("mosaic_sentences", mosaic_maximum),
                ]:
                    if sum(candidates[i][0] == kind for i in selected) > limit:
                        raise UserError(
                            "Global study selection exceeded its safety limit. Use /retry."
                        )

            selected = await self.requests.call(
                job_id=job.id,
                key=key,
                step=f"rank-{start}",
                model=settings.model,
                schema=Selection,
                instructions=RANK_PROMPT,
                payload=payload,
                validate=validate,
                max_output=2000,
            )
            shortlist = selected.selected_ids
        return material.model_copy(
            update={
                kind: [candidates[i][1] for i in shortlist if candidates[i][0] == kind]
                for kind in (
                    "vocabulary",
                    "patterns",
                    "pragmatics",
                    "cultural_references",
                    "mosaic_sentences",
                )
            }
        )
