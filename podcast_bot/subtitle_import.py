"""Durable subtitle sources for the existing Reader and study-only queue."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from .models import UserError
from .reader.documents import MAX_CHARACTERS
from .storage import atomic_json
from .subtitles import parse_srt


def import_srt(root: Path, raw: bytes, filename: str, chat_id: int, language: str) -> Path:
    if not raw or len(raw) > 2_000_000:
        raise UserError("Upload a non-empty SRT file smaller than 2 MB.")
    try:
        srt = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UserError("Subtitles must be a UTF-8 encoded .srt file.") from None
    cues = parse_srt(srt)
    text = "\n".join(cue.text for cue in cues) + "\n"
    if len(text) > MAX_CHARACTERS:
        raise UserError(f"Subtitles exceed the {MAX_CHARACTERS:,}-character Reader limit.")
    identifier = hashlib.sha256(
        json.dumps([chat_id, language, srt], ensure_ascii=False).encode()
    ).hexdigest()
    directory = root / "transcripts" / "subtitles"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / identifier
    if (target / "metadata.json").is_file():
        return target
    with TemporaryDirectory(prefix=".import-", dir=directory) as temporary:
        staging = Path(temporary) / "source"
        staging.mkdir()
        (staging / "transcript.txt").write_text(text, encoding="utf-8")
        (staging / "transcript.srt").write_bytes(raw)
        atomic_json(
            staging / "metadata.json",
            {
                "source_type": "subtitles",
                "subtitle_id": identifier,
                "podcast": "Subtitles",
                "title": Path(filename).name[:200],
                "language": language,
                "model": "imported-subtitles",
                "duration": max(cue.end for cue in cues),
                "transcription_seconds": 0,
                "filename": "subtitles",
                "source_url": "",
            },
        )
        # Timing stays available for later explicit association with matching audio.
        # Never infer a media URL from a filename or upload arbitrary file references.
        atomic_json(
            staging / "audio-timing.json",
            {
                "version": 1,
                "text": text,
                "duration": max(cue.end for cue in cues),
                "audio_url": None,
                "words": [],
                "segments": [asdict(cue) for cue in cues],
            },
        )
        staging.rename(target)
    return target
