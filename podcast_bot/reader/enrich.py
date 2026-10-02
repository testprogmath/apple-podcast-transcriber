"""Lexical enrichment for the Reader popup: pinyin and meanings with explicit precedence."""

from dataclasses import dataclass, field

from .dictionary import Dictionary
from .pinyin import pinyin_for
from .tokens import BOTANICAL, compound_parts

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

    composed: bool = False

    @property
    def meaning_source(self) -> str:
        if self.contextual:
            return CONTEXTUAL
        if self.composed:
            return "compositional"
        return "dictionary" if self.russian or self.english else "missing"

    @property
    def english_source(self) -> str:
        return "compositional" if self.composed else CEDICT

    @property
    def native_source(self) -> str:
        if self.contextual:
            return CONTEXTUAL
        return ("compositional" if self.composed else BKRS) if self.russian else ""

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
    lexical = dictionary.lexical_entries() if dictionary is not None else {}
    parts = {g: compound_parts(g, lexical) for g in wanted}
    components = list(dict.fromkeys(p for pair in parts.values() if pair for p in pair))
    entries = dictionary.lookup_many(wanted + components) if dictionary is not None else {}
    glosses = russian.lookup_many(wanted + components) if russian is not None else {}
    result = {}
    for glyph in wanted:
        entry, gloss = entries.get(glyph), glosses.get(glyph)
        pinyin = pronunciations.get(glyph, "").strip()
        if not pinyin and entry is not None:
            pinyin = entry.pinyin
        ru = gloss.definitions[:MAX_SENSES] if gloss else ()
        en = entry.definitions[:MAX_SENSES] if entry else ()
        composed = False
        if not entry and not gloss and parts[glyph]:
            base, suffix = parts[glyph]
            # Explicit component glosses, not an invented full-expression definition.
            botanical = next(
                s
                for s in lexical[base]
                if BOTANICAL.search(s) and not s.startswith(("see ", "variant ", "surname"))
            )
            en = (f"{base}: {botanical} + {suffix}: flower",)
            if base in glosses and suffix in glosses:
                ru = (
                    f"{base}: {glosses[base].definitions[0]} + {suffix}: {glosses[suffix].definitions[0]}",
                )
            pinyin = pinyin or pinyin_for(glyph)
            composed = True
        result[glyph] = Lexeme(
            glyph=glyph,
            pinyin=pinyin or pinyin_for(glyph),
            contextual=meanings.get(glyph, "").strip(),
            russian=ru,
            english=en,
            composed=composed,
        )
    return result
