import logging
import re
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass

from ..models import UserError
from .models import ChunkMaterial

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceBlock:
    id: int
    text: str


def normalized(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text))


def split_blocks(text: str, limit: int = 1200) -> list[SourceBlock]:
    """Prefer paragraph/sentence boundaries. Never truncate or lose source characters."""
    if not text.strip():
        raise UserError("The canonical transcript is empty.")
    if len(text) > 300_000:
        raise UserError("The transcript exceeds the 300,000-character study safety limit.")
    blocks: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        remaining = paragraph.strip()
        while len(remaining) > limit:
            candidates = list(
                re.finditer(r'[。！？!?；;.!][”’」』»"\']*\s*|[,，、:]\s*|\s+', remaining[:limit])
            )
            cut = candidates[-1].end() if candidates else limit
            blocks.append(remaining[:cut])
            remaining = remaining[cut:]
        if remaining.strip():
            blocks.append(remaining)
    return [SourceBlock(i, value) for i, value in enumerate(blocks)]


def batches(blocks: list[SourceBlock], limit: int) -> list[list[SourceBlock]]:
    result, current, count = [], [], 0
    for block in blocks:
        if current and count + len(block.text) > limit:
            result.append(current)
            current, count = [], 0
        current.append(block)
        count += len(block.text)
    if current:
        result.append(current)
    return result


def validate_chunk(material: ChunkMaterial, blocks: list[SourceBlock], target: str) -> None:
    # Model-assigned IDs are advisory. Bind complete passages to the immutable
    # source, retaining each passage's own translation and reading lines.
    remaining = defaultdict(deque)
    for passage in material.passages:
        remaining[passage.source].append(passage)
    aligned = []
    for block in blocks:
        if not remaining[block.text]:
            break
        aligned.append(remaining[block.text].popleft().model_copy(update={"block_id": block.id}))
    if len(aligned) != len(blocks) or any(remaining.values()):
        log.warning(
            "study-coverage expected_blocks=%d received_passages=%d matched_blocks=%d",
            len(blocks),
            len(material.passages),
            len(aligned),
        )
        raise UserError(
            "Study output did not preserve complete transcript paragraphs "
            f"({len(aligned)}/{len(blocks)} matched). Canonical transcript preserved; use /retry."
        )
    material.passages = aligned
    for passage, block in zip(material.passages, blocks, strict=True):
        if passage.source != block.text or normalized(
            "".join(x.source for x in passage.lines)
        ) != normalized(block.text):
            raise UserError(
                "Study output changed the source text. Canonical transcript preserved; use /retry."
            )
        if target == "zh" and any(
            not line.pinyin.strip()
            for line in passage.lines
            if re.search(r"[\u3400-\u9fff]", line.source)
        ):
            raise UserError("The study model omitted required pinyin. Use /retry.")
        if target == "zh" and any(
            re.search(r"[a-zA-ZüÜ][1-5](?:\b|$)", line.pinyin) for line in passage.lines
        ):
            raise UserError(
                "The study model returned numbered rather than tone-mark pinyin. Use /retry."
            )
    validate_mosaic(material, blocks, target)
    source = normalized("".join(b.text for b in blocks))
    for item in [
        *material.vocabulary,
        *material.patterns,
        *material.pragmatics,
        *material.cultural_references,
    ]:
        if normalized(item.example) not in source:
            raise UserError(
                "A study example was not found in the canonical transcript. Use /retry."
            )
    for item in material.vocabulary:
        if normalized(item.term) not in source:
            raise UserError(
                "A vocabulary item was not found in the canonical transcript. Use /retry."
            )
    for issue in material.possible_asr_errors:
        if normalized(issue.original) not in source:
            raise UserError("An ASR issue did not cite the canonical transcript. Use /retry.")


def validate_mosaic(material: ChunkMaterial, blocks: list[SourceBlock], target: str) -> None:
    for sentence in material.mosaic_sentences:
        if target != "zh" or not any(sentence.chinese in block.text for block in blocks):
            raise UserError(
                "A Mandarin Mosaic sentence is not verbatim in the transcript. Use /retry."
            )
        if sentence.source_start is not None or sentence.source_end is not None:
            raise UserError("The study model invented Mosaic timestamps. Use /retry.")
