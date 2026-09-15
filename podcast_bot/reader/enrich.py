"""Lexical enrichment for the Reader popup: pinyin and meanings with explicit precedence."""

from dataclasses import dataclass, field

from .dictionary import Dictionary
from .pinyin import pinyin_for

CONTEXTUAL = "contextual"
BKRS = "bkrs"
CEDICT = "cc-cedict"
MAX_SENSES = 8


@dataclass(frozen=True)
class Lexeme:
    """Both language tracks are carried so the Reader can switch without another request."""

    glyph: str
    pinyin: str
    contextual: str = ""
    russian: tuple[str, ...] = field(default=())
    english: tuple[str, ...] = field(default=())

    @property
    def native_source(self) -> str:
        if self.contextual:
            return CONTEXTUAL
        return BKRS if self.russian else ""

    def native(self) -> tuple[str, ...]:
        """The learner's own language: the pack's contextual meaning, else the Russian gloss."""
        return (self.contextual,) if self.contextual else self.russian


def enrich(
    glyphs,
    meanings: dict[str, str],
    pronunciations: dict[str, str],
    dictionary: Dictionary | None = None,
    russian=None,
) -> dict[str, Lexeme]:
    """Resolve every distinct glyph once.

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
        result[glyph] = Lexeme(
            glyph=glyph,
            pinyin=pinyin or pinyin_for(glyph),
            contextual=meanings.get(glyph, "").strip(),
            russian=gloss.definitions[:MAX_SENSES] if gloss else (),
            english=entry.definitions[:MAX_SENSES] if entry else (),
        )
    return result
