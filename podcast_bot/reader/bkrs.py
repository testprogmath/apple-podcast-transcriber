"""Read-only 大БКРС lookup: Chinese headword to a Russian gloss.

Optional and never redistributed with this project. Point READER_DICTIONARY_RU at a
database built by tools/build_bkrs_dictionary.py from your own copy of the export.
Absent configuration simply means no Russian gloss, and the Reader falls back as before.
"""

import logging
import os
import re
import sqlite3
from pathlib import Path

from .dictionary import DictionaryEntry

log = logging.getLogger(__name__)
TAG = re.compile(r"\[/?[a-z!*][a-z0-9]*\]")
EXAMPLES = re.compile(r"\[\*\].*?\[/\*\]|\[ex\].*?\[/ex\]", re.S)
SPACES = re.compile(r"[ \t]+")
MAX_SENSES = 8
MAX_SENSE_LENGTH = 160


def russian_path() -> Path | None:
    configured = os.getenv("READER_DICTIONARY_RU", "").strip()
    return Path(configured) if configured else None


def plain_senses(definition: str, limit: int = MAX_SENSES) -> tuple[str, ...]:
    """One display line per DSL block, markup and examples removed.

    Each [m1]/[m2] block is a numbered sense or a reading header, so keeping the block
    boundaries is what stops a long entry collapsing into one unreadable run of text.
    """
    text = EXAMPLES.sub("", definition).replace("[/m]", "\n")
    senses = []
    for line in TAG.sub("", text).split("\n"):
        line = SPACES.sub(" ", line).strip().strip(";,")
        if line and line not in senses:
            senses.append(line[:MAX_SENSE_LENGTH])
    return tuple(senses[:limit])


class RussianDictionary:
    """Exact headword lookup. Same shape as the CC-CEDICT dictionary so both compose."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else russian_path()
        self._connection: sqlite3.Connection | None = None
        if self.path is None:
            return
        if not self.path.is_file():
            log.warning("stage=russian-dictionary-absent")
            return
        try:
            self._connection = sqlite3.connect(
                f"file:{self.path}?mode=ro", uri=True, check_same_thread=False
            )
            self._connection.row_factory = sqlite3.Row
            count = self._connection.execute("SELECT count(*) FROM entries").fetchone()[0]
            log.info("stage=russian-dictionary entries=%s", count)
        except sqlite3.Error:
            self._connection = None
            log.error("stage=russian-dictionary-unavailable")

    @property
    def available(self) -> bool:
        return self._connection is not None

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def lookup(self, glyph: str) -> DictionaryEntry | None:
        return self.lookup_many([glyph]).get(glyph)

    def lookup_many(self, glyphs) -> dict[str, DictionaryEntry]:
        wanted = [g for g in dict.fromkeys(glyphs) if g]
        if not wanted or self._connection is None:
            return {}
        found: dict[str, list[sqlite3.Row]] = {}
        for chunk in (wanted[i : i + 400] for i in range(0, len(wanted), 400)):
            placeholders = ",".join("?" * len(chunk))
            rows = self._connection.execute(
                "SELECT headword,pinyin,definition FROM entries"
                f" WHERE headword IN ({placeholders}) ORDER BY id",
                chunk,
            ).fetchall()
            for row in rows:
                found.setdefault(row["headword"], []).append(row)
        return {glyph: _entry(glyph, rows) for glyph, rows in found.items()}


def _entry(glyph: str, rows: list[sqlite3.Row]) -> DictionaryEntry:
    primary = rows[0]
    definitions = []
    for row in rows:
        for sense in plain_senses(row["definition"]):
            if sense not in definitions:
                definitions.append(sense)
    return DictionaryEntry(
        simplified=glyph,
        traditional=glyph,
        pinyin=primary["pinyin"],
        definitions=tuple(definitions[:MAX_SENSES]),
        alternatives=(),
    )
