"""Sentence boundaries for Reader interaction. The source text is never rewritten."""

from dataclasses import dataclass

TERMINATORS = "。！？；…!?;‼⁇⁈⁉"
CLOSERS = "”’」』〉》）)]｝}\"'›»"


@dataclass(frozen=True)
class Sentence:
    id: int
    start: int
    end: int
    text: str


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


def parse_sentences(text: str) -> list[Sentence]:
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
