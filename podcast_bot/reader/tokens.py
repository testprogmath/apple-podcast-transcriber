"""Reader-friendly lexical segmentation.

Conceptually distinct from Mandarin Mosaic transport segmentation: this decides what a
reader may tap, so punctuation, whitespace and Latin text stay unclickable and known
multi-character chunks win over the generic segmenter.
"""

import re
from dataclasses import dataclass

from ..models import UserError

HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
MAX_CHUNK = 12


@dataclass(frozen=True)
class Token:
    text: str
    word: bool


def _claim(text: str, known: frozenset[str], longest: int) -> list[tuple[int, int]]:
    claims: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        for size in range(min(longest, len(text) - index), 1, -1):
            if text[index : index + size] in known:
                claims.append((index, index + size))
                index += size
                break
        else:
            index += 1
    return claims


def _cut(text: str) -> list[str]:
    import jieba

    return [part for part in jieba.cut(text, cut_all=False) if part]


def tokenize(text: str, known: frozenset[str] = frozenset()) -> list[Token]:
    """Segment one sentence. Concatenated token text always reproduces the input."""
    known = frozenset(
        term for term in known if 2 <= len(term) <= MAX_CHUNK and HAN.search(term) and term in text
    )
    longest = max((len(term) for term in known), default=0)
    pieces: list[str] = []
    position = 0
    for start, end in _claim(text, known, longest):
        if start > position:
            pieces.extend(_cut(text[position:start]))
        pieces.append(text[start:end])
        position = end
    if position < len(text):
        pieces.extend(_cut(text[position:]))
    tokens = [Token(piece, bool(HAN.search(piece))) for piece in pieces]
    if "".join(token.text for token in tokens) != text:
        raise UserError("Reader segmentation failed to preserve the sentence.")
    return tokens
