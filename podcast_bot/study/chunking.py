import logging
import re
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass

from ..models import UserError
from .models import ChunkMaterial
from .translations import han_ratio, untranslated, wrong_language

log = logging.getLogger(__name__)

# A tone digit belongs to a lowercase pinyin syllable (ni3, lü4). Acronyms the
# episode itself says — HSK1, MP3 — end in an uppercase letter and are not pinyin.
NUMBERED_PINYIN = re.compile(r"[a-zü][1-5](?:\b|$)")
SENTENCE_END = re.compile(r'[。！？!?；;.][”’」』»"\']*\s*')
SOFT_BREAK = re.compile(r"[,，、:]\s*|\s+")


@dataclass(frozen=True)
class SourceBlock:
    id: int
    text: str


def normalized(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text))


def characters(text: str) -> str:
    """Letters and digits only: the model may punctuate a transcript that has none."""
    return "".join(c for c in unicodedata.normalize("NFC", text) if c.isalnum())


def divergence(expected: str, received: str) -> tuple[int, str, str]:
    at = next(
        (i for i, (a, b) in enumerate(zip(expected, received, strict=False)) if a != b),
        min(len(expected), len(received)),
    )
    return at, expected[max(0, at - 20) : at + 20], received[max(0, at - 20) : at + 20]


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
            # A block that stops mid-sentence invites the model to finish the sentence
            # from the next block, which fails source validation. Commas and spaces are
            # a fallback only when no sentence ends in a usefully large part of the window.
            window = remaining[:limit]
            ends = [m.end() for m in SENTENCE_END.finditer(window) if m.end() >= limit // 3]
            soft = [m.end() for m in SOFT_BREAK.finditer(window)]
            cut = (ends or soft or [limit])[-1]
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


def report(field: str, native: str, value: str) -> None:
    """One diagnostic line so a rejected paid run still tells us what came back."""
    log.warning(
        "study-language field=%s native=%s han_ratio=%.2f excerpt=%r",
        field,
        native,
        han_ratio(value),
        (value or "")[:60],
    )


def validate_chunk(
    material: ChunkMaterial, blocks: list[SourceBlock], target: str, native: str = ""
) -> None:
    # Model-assigned IDs are advisory. Bind complete passages to the immutable
    # source, retaining each passage's own translation and reading lines.
    remaining = defaultdict(deque)
    for passage in material.passages:
        remaining[characters(passage.source)].append(passage)
    aligned = []
    for block in blocks:
        key = characters(block.text)
        if not remaining[key]:
            break
        # The canonical transcript owns spacing and NFC form; the model's copy does not.
        passage = remaining[key].popleft()
        aligned.append(passage.model_copy(update={"block_id": block.id, "source": block.text}))
    if len(aligned) != len(blocks) or any(remaining.values()):
        if len(aligned) < len(blocks) and len(aligned) < len(material.passages):
            at, expected, received = divergence(
                characters(blocks[len(aligned)].text),
                characters(material.passages[len(aligned)].source),
            )
            log.warning(
                "study-coverage-divergence block=%d first_difference=%d "
                "source_excerpt=%r passage_excerpt=%r",
                blocks[len(aligned)].id,
                at,
                expected,
                received,
            )
        log.warning(
            "study-coverage expected_blocks=%d received_passages=%d matched_blocks=%d "
            "unmatched_block=%r unmatched_passages=%r",
            len(blocks),
            len(material.passages),
            len(aligned),
            blocks[len(aligned)].text[:80] if len(aligned) < len(blocks) else "",
            [p.source[:80] for queue in remaining.values() for p in queue][:3],
        )
        raise UserError(
            "Study output did not preserve complete transcript paragraphs "
            f"({len(aligned)}/{len(blocks)} matched). Canonical transcript preserved; use /retry."
        )
    material.passages = aligned
    for passage, block in zip(material.passages, blocks, strict=True):
        source = characters(block.text)
        joined = characters("".join(x.source for x in passage.lines))
        if joined != source:
            at, source_excerpt, line_excerpt = divergence(source, joined)
            log.warning(
                "study-lines block=%d lines=%d source_chars=%d line_chars=%d "
                "first_difference=%d source_excerpt=%r line_excerpt=%r",
                block.id,
                len(passage.lines),
                len(source),
                len(joined),
                at,
                source_excerpt,
                line_excerpt,
            )
            raise UserError(
                "Study output changed the source text. Canonical transcript preserved; use /retry."
            )
        if target == "zh" and any(
            not line.pinyin.strip()
            for line in passage.lines
            if re.search(r"[\u3400-\u9fff]", line.source)
        ):
            raise UserError("The study model omitted required pinyin. Use /retry.")
        numbered = next(
            (line.pinyin for line in passage.lines if NUMBERED_PINYIN.search(line.pinyin)), ""
        )
        if target == "zh" and numbered:
            log.warning("study-pinyin numbered excerpt=%r", numbered[:80])
            raise UserError(
                "The study model returned numbered rather than tone-mark pinyin. Use /retry."
            )
    for passage in material.passages:
        if untranslated(passage.source, passage.translation, native):
            report("passage-translation", native, passage.translation)
            raise UserError(
                "The study model returned the source text instead of a "
                f"{native or 'native'}-language translation. Canonical transcript preserved; use /retry."
            )
    for item in [
        *material.vocabulary,
        *material.patterns,
        *material.pragmatics,
        *material.cultural_references,
    ]:
        if untranslated(item.example, item.example_translation, native):
            report("example-translation", native, item.example_translation)
            raise UserError(
                "A study example was not translated into the configured native language. Use /retry."
            )
    for item in material.vocabulary:
        if wrong_language(item.meaning, native):
            report("vocabulary-meaning", native, item.meaning)
            raise UserError(
                "A vocabulary meaning was not written in the configured native language. Use /retry."
            )
    validate_mosaic(material, blocks, target)
    source = characters("".join(b.text for b in blocks))
    for item in [
        *material.vocabulary,
        *material.patterns,
        *material.pragmatics,
        *material.cultural_references,
    ]:
        if characters(item.example) not in source:
            raise UserError(
                "A study example was not found in the canonical transcript. Use /retry."
            )
    for item in material.vocabulary:
        if characters(item.term) not in source:
            raise UserError(
                "A vocabulary item was not found in the canonical transcript. Use /retry."
            )
    for issue in material.possible_asr_errors:
        if characters(issue.original) not in source:
            raise UserError("An ASR issue did not cite the canonical transcript. Use /retry.")


def validate_mosaic(material: ChunkMaterial, blocks: list[SourceBlock], target: str) -> None:
    for sentence in material.mosaic_sentences:
        if target != "zh" or not any(sentence.chinese in block.text for block in blocks):
            raise UserError(
                "A Mandarin Mosaic sentence is not verbatim in the transcript. Use /retry."
            )
        if sentence.source_start is not None or sentence.source_end is not None:
            raise UserError("The study model invented Mosaic timestamps. Use /retry.")
