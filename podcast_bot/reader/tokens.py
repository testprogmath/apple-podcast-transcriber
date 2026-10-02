"""Deterministic bounded candidate selection; source text and sentence offsets are immutable."""

import re
from dataclasses import dataclass

from ..models import UserError

HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
RUN = re.compile(r"[㐀-䶿一-鿿豈-﫿]+|[^㐀-䶿一-鿿豈-﫿]+")
MAX_CHUNK = 12
LEXICAL_SEGMENTATION_VERSION = 2
BOTANICAL = re.compile(
    r"^(?:(?:Chinese|tree|common|herbaceous|white|red|yellow|water) )*"
    r"(?:peony|rose|orchid|chrysanthemum|azalea|jasmine|hibiscus|camellia|magnolia|lotus|lily|tulip|daffodil)(?:$|[ (,;])",
    re.I,
)


@dataclass(frozen=True)
class Token:
    text: str
    word: bool
    start: int = 0
    end: int = 0


def compound_parts(word: str, entries: dict) -> tuple[str, str] | None:
    """Narrow botanical noun + 花, not arbitrary word + 花. No synthetic dictionary entries."""
    base = word[:-1]
    if not (word.endswith("花") and 2 <= len(base) <= 4 and "flower" in entries.get("花", ())):
        return None
    senses = entries.get(base, ())
    # Ignore cross references/surnames: botanical evidence must be a literal gloss.
    if any(
        BOTANICAL.search(s) and not s.startswith(("see ", "variant ", "surname")) for s in senses
    ):
        return base, "花"
    return None


def _cut(text: str) -> list[str]:
    import jieba

    return [part for part in jieba.cut(text, cut_all=False) if part]


def _select(text: str, entries: dict) -> list[str]:
    baseline = {}
    at = 0
    for part in _cut(text):
        baseline[at] = part
        at += len(part)
    # Dynamic programming compares complete paths, not greedy longest matches.
    scores = [0] * (len(text) + 1)
    choices = [1] * len(text)
    for start in range(len(text) - 1, -1, -1):
        candidates = {1: 1}
        fallback = baseline.get(start)
        if fallback:
            candidates[len(fallback)] = 2 * len(fallback) - 1
        for size in range(2, min(MAX_CHUNK, len(text) - start) + 1):
            word = text[start : start + size]
            if word in entries:
                score = 3 * size - 1
                # Weak long entries must not swallow two independent lexical words.
                if word != fallback and any(
                    word[:i] in entries and word[i:] in entries for i in range(2, size - 1)
                ):
                    score -= 3
                candidates[size] = max(candidates.get(size, 0), score)
            if compound_parts(word, entries):
                candidates[size] = max(candidates.get(size, 0), 3 * size + 1)
        size = max(sorted(candidates), key=lambda n: (candidates[n] + scores[start + n], -n))
        choices[start] = size
        scores[start] = candidates[size] + scores[start + size]
    result = []
    at = 0
    while at < len(text):
        size = choices[at]
        result.append(text[at : at + size])
        at += size
    return result


def tokenize(text: str, known: frozenset[str] = frozenset(), dictionary=None) -> list[Token]:
    """Study chunks retain priority; no candidate crosses a non-Han boundary."""
    entries = dictionary.lexical_entries() if dictionary is not None else {}
    known = frozenset(
        t for t in known if 2 <= len(t) <= MAX_CHUNK and all(HAN.fullmatch(c) for c in t)
    )
    tokens = []
    for run in RUN.finditer(text):
        value = run.group()
        if not HAN.match(value):
            tokens.append(Token(value, False, run.start(), run.end()))
            continue
        # Preserve the established leftmost/longest explicit study claims.
        pieces = []
        cursor = 0
        pending = 0
        while cursor < len(value):
            term = next(
                (
                    value[cursor : cursor + n]
                    for n in range(min(MAX_CHUNK, len(value) - cursor), 1, -1)
                    if value[cursor : cursor + n] in known
                ),
                None,
            )
            # Reject an unsupported interior suffix claim (丹花) that cuts a
            # dictionary-backed botanical noun. Aligned/lexical study claims win.
            if (
                term
                and term not in entries
                and any(
                    start < cursor < end and cursor + len(term) <= end
                    for start in range(max(0, cursor - 4), cursor)
                    for end in range(cursor + 1, min(len(value), start + 5) + 1)
                    if compound_parts(value[start:end], entries)
                )
            ):
                term = None
            if term:
                pieces.extend(_select(value[pending:cursor], entries))
                pieces.append(term)
                cursor += len(term)
                pending = cursor
            else:
                cursor += 1
        pieces.extend(_select(value[pending:], entries))
        offset = run.start()
        for piece in pieces:
            tokens.append(Token(piece, True, offset, offset + len(piece)))
            offset += len(piece)
    if "".join(t.text for t in tokens) != text:
        raise UserError("Reader segmentation failed to preserve the sentence.")
    return tokens
