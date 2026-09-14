"""Lexical enrichment for the Reader popup: pinyin and meaning with explicit precedence."""

from dataclasses import dataclass

from .dictionary import Dictionary, DictionaryEntry
from .pinyin import pinyin_for

CONTEXTUAL = "contextual"
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
) -> dict[str, Lexeme]:
    """Resolve every distinct glyph once.

    Pinyin: study pronunciation, then an exact CC-CEDICT entry, then the local fallback.
    Meaning: study meaning, then a CC-CEDICT definition, then nothing. A contextual
    meaning is never replaced by a generic dictionary definition.
    """
    wanted = list(dict.fromkeys(glyphs))
    entries = dictionary.lookup_many(wanted) if dictionary is not None else {}
    result = {}
    for glyph in wanted:
        entry = entries.get(glyph)
        pinyin = pronunciations.get(glyph, "").strip()
        if not pinyin and entry is not None:
            pinyin = entry.pinyin
        if not pinyin:
            pinyin = pinyin_for(glyph)
        meaning = meanings.get(glyph, "").strip()
        source = CONTEXTUAL if meaning else NONE
        if not meaning and entry is not None:
            meaning = _definition(entry)
            source = CEDICT if meaning else NONE
        result[glyph] = Lexeme(glyph, pinyin, meaning, source)
    return result
