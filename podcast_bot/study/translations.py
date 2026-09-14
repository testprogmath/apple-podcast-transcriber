"""Detect study output that was never actually translated into the native language.

The chunk prompt requires meanings and translations in the configured native language, but
nothing verified compliance. A model that answers in Chinese produced packs whose
`example_translation` was byte-identical to the Chinese example, which then reached Hanly
notes as `Перевод：<the same Chinese>`.
"""

import re
import unicodedata

HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
HAN_LIMIT = 0.5


def _normalized(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text or ""))


def han_ratio(text: str) -> float:
    """Share of Han characters among letters, ignoring spacing and punctuation."""
    letters = [
        c
        for c in (text or "")
        if not c.isspace() and not unicodedata.category(c).startswith(("P", "N", "S"))
    ]
    if not letters:
        return 0.0
    return sum(1 for c in letters if HAN.match(c)) / len(letters)


def untranslated(source: str, translation: str, native_language: str) -> bool:
    """True when a translation field repeats its source or stayed in the target language."""
    translation = (translation or "").strip()
    if not translation:
        return True
    if _normalized(translation) == _normalized(source):
        return True
    return native_language != "zh" and han_ratio(translation) > HAN_LIMIT


def wrong_language(text: str, native_language: str) -> bool:
    """True when prose that should be in the native language is predominantly Chinese."""
    text = (text or "").strip()
    if not text:
        return True
    return native_language != "zh" and han_ratio(text) > HAN_LIMIT
