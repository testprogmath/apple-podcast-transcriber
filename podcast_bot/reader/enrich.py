"""Lexical enrichment for the Reader popup: pinyin and meaning with explicit precedence."""

from dataclasses import dataclass

from .dictionary import Dictionary, DictionaryEntry
from .pinyin import pinyin_for

CONTEXTUAL = "contextual"
BKRS = "bkrs"
CEDICT = "cc-cedict"
NONE = "none"
MAX_POPUP_DEFINITIONS = 3


@dataclass(frozen=True)
class Lexeme:
    glyph: str
    pinyin: str
    meaning: str = ""
    meaning_source: str = NONE


def _definition(entry: DictionaryEntry) -> str:
    return "; ".join(entry.definitions[:MAX_POPUP_DEFINITIONS])


def enrich(
    glyphs,
    meanings: dict[str, str],
    pronunciations: dict[str, str],
    dictionary: Dictionary | None = None,
    russian=None,
) -> dict[str, Lexeme]:
    """Resolve every distinct glyph once, with explicit per-field precedence.

    Meaning: the study pack's contextual meaning, then a Russian gloss, then a CC-CEDICT
    definition, then nothing. A contextual meaning is never replaced by a generic one.
    Pinyin: study pronunciation, then CC-CEDICT, then the local fallback. The Russian
    source is skipped for pinyin because it writes syllables unseparated (rènwéi).
    """
    wanted = list(dict.fromkeys(glyphs))
    entries = dictionary.lookup_many(wanted) if dictionary is not None else {}
    glosses = russian.lookup_many(wanted) if russian is not None else {}
    result = {}
    for glyph in wanted:
        entry, gloss = entries.get(glyph), glosses.get(glyph)
        pinyin = pronunciations.get(glyph, "").strip()
        if not pinyin and entry is not None:
            pinyin = entry.pinyin
        if not pinyin:
            pinyin = pinyin_for(glyph)
        meaning = meanings.get(glyph, "").strip()
        source = CONTEXTUAL if meaning else NONE
        for candidate, name in ((gloss, BKRS), (entry, CEDICT)):
            if meaning or candidate is None:
                continue
            meaning = _definition(candidate)
            source = name if meaning else NONE
        result[glyph] = Lexeme(glyph, pinyin, meaning, source)
    return result
