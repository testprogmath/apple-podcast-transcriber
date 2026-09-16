"""Ad-hoc Hanly cards: whatever the user typed, stored verbatim as one glyph.

Hanly's own interface only offers words its dictionary knows, while the Firestore
collection accepts any string. `/add_hanly` exists to reach that gap, so the argument is
never segmented, normalised, or checked against a dictionary.
"""

import unicodedata

from ..models import UserError
from ..study.translations import HAN

KEY = "manual:imports"
NAME = "Manual imports"
COMMENT = "Added from Telegram with /add_hanly"
MAX_LENGTH = 200
USAGE = "Send a Chinese word, phrase or sentence:\n/add_hanly 不知不觉"
UNPRINTABLE = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}


def parse_glyph(text: str) -> str:
    fields = text.split(maxsplit=1)
    glyph = fields[1].strip() if len(fields) == 2 else ""
    if not glyph:
        raise UserError(USAGE)
    if len(glyph) > MAX_LENGTH:
        raise UserError(
            f"One Hanly card holds up to {MAX_LENGTH} characters. Send a single word, phrase or sentence."
        )
    if any(unicodedata.category(c) in UNPRINTABLE for c in glyph):
        raise UserError("Send one line of Chinese text, without control characters.")
    if not HAN.search(glyph):
        raise UserError("A Hanly card needs Chinese characters.\n" + USAGE)
    return glyph


def distinct_name(collections: dict, base: str = NAME) -> str:
    """Only the stored UUID grants ownership, so a same-named user collection is left alone."""
    taken = {c["name"] for c in collections.values() if not c["deleted"]}
    if base not in taken:
        return base
    candidate = f"{base} (bot)"
    index = 2
    while candidate in taken:
        candidate = f"{base} (bot {index})"
        index += 1
    return candidate
