"""Sentence boundaries for Reader interaction. The source text is never rewritten."""

import re
from bisect import bisect_right
from dataclasses import dataclass

RECOVERY_VERSION = 1
RECOVERED_ID_BASE = 1_000_000_000
HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
TARGET_FRAGMENT = 60
LONG_HAN_RUN = 120

TERMINATORS = "。！？；…!?;‼⁇⁈⁉"
CLOSERS = "”’」』〉》）)]｝}\"'›»"


@dataclass(frozen=True)
class Sentence:
    id: int
    start: int
    end: int
    text: str
    boundary_source: str = "source"
    source_start: float | None = None
    source_end: float | None = None


def _terminator(text: str, index: int) -> bool:
    if text[index] in TERMINATORS:
        return True
    # An ASCII full stop ends a sentence only at a word boundary, never inside 3.14.
    return text[index] == "." and (index + 1 == len(text) or text[index + 1].isspace())


def _blank_line(text: str, index: int) -> bool:
    for position in range(index + 1, len(text)):
        if not text[position].isspace():
            return False
        if text[position] == "\n":
            return True
    return False


def source_sentences(text: str) -> list[Sentence]:
    """Split into selectable units. Everything between two sentences is whitespace."""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    last = 0
    index, length = 0, len(text)
    while index < length:
        character = text[index]
        if character.isspace():
            if start is not None and character == "\n" and _blank_line(text, index):
                spans.append((start, last + 1))
                start = None
            index += 1
            continue
        if start is None:
            start = index
        last = index
        if _terminator(text, index):
            end = index + 1
            while end < length and (text[end] in CLOSERS or _terminator(text, end)):
                end += 1
            spans.append((start, end))
            start = None
            index = end
            continue
        index += 1
    if start is not None:
        spans.append((start, last + 1))
    return [Sentence(i, s, e, text[s:e]) for i, (s, e) in enumerate(spans)]


def paragraphs(text: str, sentences: list[Sentence]) -> list[list[int]]:
    """Group sentence IDs into reading paragraphs using blank lines in the source."""
    groups: list[list[int]] = []
    previous_end = 0
    for sentence in sentences:
        gap = text[previous_end : sentence.start]
        if not groups or gap.count("\n") > 1:
            groups.append([])
        groups[-1].append(sentence.id)
        previous_end = sentence.end
    return groups


def needs_recovery(sentence: Sentence) -> bool:
    """A long Chinese unit without sentence boundaries, not an ASR accuracy estimate."""
    return len(HAN.findall(sentence.text)) >= LONG_HAN_RUN


def sentence_quality(text: str) -> dict:
    affected = [s for s in source_sentences(text) if needs_recovery(s)]
    return {
        "version": RECOVERY_VERSION,
        "status": "sparse_sentence_boundaries" if affected else "ok",
        "affected_units": len(affected),
        "max_unit_characters": max((len(s.text) for s in affected), default=0),
    }


def _reading_spans(sentence: Sentence):
    """Bounded reading units, never claimed to be grammatical sentences.

    Prefer existing whitespace/comma boundaries. For continuous Han text use local
    lexical boundaries. Never invent punctuation, change words or split Latin names.
    """
    from .tokens import tokenize

    text = sentence.text
    preferred = sorted(m.end() for m in re.finditer(r"\s+|[，、：]|(?<![0-9])[, :](?![0-9])", text))
    boundaries = set(preferred)
    boundaries.update(t.end for t in tokenize(text))
    boundaries.add(len(text))
    boundaries = sorted(boundaries)
    start = 0
    while start < len(text):
        while start < len(text) and text[start].isspace():
            start += 1
        if start == len(text):
            break
        target = start + TARGET_FRAGMENT
        preferred_index = bisect_right(preferred, target) - 1
        boundary_index = bisect_right(boundaries, target) - 1
        if len(text) - start <= TARGET_FRAGMENT:
            end = len(text)
        elif preferred_index >= 0 and preferred[preferred_index] >= start + 25:
            end = preferred[preferred_index]
        elif boundary_index >= 0 and boundaries[boundary_index] > start:
            end = boundaries[boundary_index]
        else:
            end = boundaries[bisect_right(boundaries, start)]
        trimmed = end
        while trimmed > start and text[trimmed - 1].isspace():
            trimmed -= 1
        if trimmed > start:
            yield sentence.start + start, sentence.start + trimmed
        start = end


def parse_sentences(text: str) -> list[Sentence]:
    result = []
    for sentence in source_sentences(text):
        if not needs_recovery(sentence):
            result.append(sentence)
            continue
        for start, end in _reading_spans(sentence):
            # Preserve IDs of all unaffected sentences. Old whole-block selections
            # fail validation instead of silently targeting the first new fragment.
            result.append(
                Sentence(RECOVERED_ID_BASE + start, start, end, text[start:end], "approximate")
            )
    return result
