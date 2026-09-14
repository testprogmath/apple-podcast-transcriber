"""ReaderDocument: anything that can be opened in the Reader.

A podcast document keeps a deterministic identity, so reopening an episode never creates
a second document, a second Hanly collection, or a second Mandarin Mosaic pack.
"""

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..identity import collection_name, hanly_identity
from ..models import UserError
from ..storage import now
from .sentences import Sentence, paragraphs, parse_sentences
from .tokens import HAN, Token, tokenize

MAX_CHARACTERS = 200_000


@dataclass(frozen=True)
class ReaderDocument:
    id: str
    chat_id: int
    title: str
    source_type: str
    source_reference: str
    raw_text: str
    hanly_key: str
    mosaic_key: str
    created: str

    def sentences(self) -> list[Sentence]:
        return parse_sentences(self.raw_text)

    def paragraphs(self, sentences: list[Sentence]) -> list[list[int]]:
        return paragraphs(self.raw_text, sentences)

    def known_chunks(self) -> frozenset[str]:
        """Reuse study-pipeline vocabulary and patterns; never call a model when opening."""
        if not self.source_reference:
            return frozenset()
        try:
            material = json.loads(
                (Path(self.source_reference) / "study.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return frozenset()
        terms = [item.get("term", "") for item in material.get("vocabulary", [])]
        terms += [item.get("pattern", "") for item in material.get("patterns", [])]
        return frozenset(term for term in terms if isinstance(term, str) and term)

    def known_translations(self) -> dict[str, str]:
        """Reuse translations the study pipeline already produced; never translate on demand."""
        if not self.source_reference:
            return {}
        try:
            material = json.loads(
                (Path(self.source_reference) / "study.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return {}
        return {
            item["chinese"]: item["english"]
            for item in material.get("mosaic_sentences", [])
            if isinstance(item.get("chinese"), str) and isinstance(item.get("english"), str)
        }

    def tokens(self, sentences: list[Sentence]) -> list[list[Token]]:
        known = self.known_chunks()
        return [tokenize(sentence.text, known) for sentence in sentences]


def short_title(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    first = re.sub(r"\s+", " ", first)[:60].strip()
    return first or "Text " + datetime.now(UTC).strftime("%Y-%m-%d %H:%M")


def check_text(text: str) -> str:
    if not text.strip():
        raise UserError("Reader needs some text. Send Chinese text or a .txt/.md file.")
    if len(text) > MAX_CHARACTERS:
        raise UserError(f"Reader accepts at most {MAX_CHARACTERS:,} characters.")
    if not HAN.search(text):
        raise UserError("Reader expects Chinese text.")
    return text


def from_text(chat_id: int, text: str, title: str | None = None, source_type: str = "text"):
    text = check_text(text)
    identifier = secrets.token_hex(16)
    return ReaderDocument(
        id=identifier,
        chat_id=chat_id,
        title=title or short_title(text),
        source_type=source_type,
        source_reference="",
        raw_text=text,
        hanly_key="reader:" + identifier,
        mosaic_key="reader:" + identifier,
        created=now(),
    )


def from_transcript(chat_id: int, path: Path):
    """Open a stored transcript or study pack. Never retranscribes anything."""
    try:
        text = (path / "transcript.txt").read_text(encoding="utf-8")
        metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise UserError("That transcript is unavailable. Send a link or use /regenerate.") from None
    hanly_key = hanly_identity(metadata)
    identifier = hashlib.sha256(("reader-v1:" + hanly_key).encode()).hexdigest()[:32]
    return ReaderDocument(
        id=identifier,
        chat_id=chat_id,
        title=collection_name(metadata),
        source_type="podcast",
        source_reference=str(path),
        raw_text=check_text(text),
        hanly_key=hanly_key,
        mosaic_key="reader:" + identifier,
        created=now(),
    )
