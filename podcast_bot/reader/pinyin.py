"""Tone-mark pinyin for Reader lexical items. Deterministic and local: never a model call."""

from functools import lru_cache

from .tokens import HAN


@lru_cache(maxsize=8192)
def pinyin_for(word: str) -> str:
    """Space-separated tone-mark syllables, or an empty string for anything without Han."""
    if not HAN.search(word):
        return ""
    from pypinyin import Style, lazy_pinyin

    # The whole lexical item is passed at once so phrase context resolves 银行 as yín háng.
    return " ".join(syllable for syllable in lazy_pinyin(word, style=Style.TONE, errors="ignore"))
