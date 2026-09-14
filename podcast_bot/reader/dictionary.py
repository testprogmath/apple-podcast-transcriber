"""Read-only CC-CEDICT lookup for Reader enrichment.

Enrichment only: a glyph absent from the dictionary stays selectable and uploadable.
Never performs network access. Missing database means every lookup cleanly returns None.
"""

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .cedict import tone_marks

log = logging.getLogger(__name__)
DEFAULT_PATH = Path(__file__).with_name("cedict.sqlite3")
MAX_DEFINITIONS = 6


@dataclass(frozen=True)
class DictionaryEntry:
    simplified: str
    traditional: str
    pinyin: str
    definitions: tuple[str, ...]
    alternatives: tuple[tuple[str, tuple[str, ...]], ...] = field(default=())


def dictionary_path() -> Path:
    return Path(os.getenv("READER_DICTIONARY") or DEFAULT_PATH)


class Dictionary:
    """Exact simplified/traditional lookup against the generated CC-CEDICT database."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else dictionary_path()
        self._connection: sqlite3.Connection | None = None
        if self.path.is_file():
            try:
                self._connection = sqlite3.connect(
                    f"file:{self.path}?mode=ro", uri=True, check_same_thread=False
                )
                self._connection.row_factory = sqlite3.Row
                count = self._connection.execute("SELECT count(*) FROM entries").fetchone()[0]
                log.info("stage=dictionary entries=%s", count)
            except sqlite3.Error:
                self._connection = None
                log.error("stage=dictionary-unavailable path_present=1")
        else:
            log.warning("stage=dictionary-absent")

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
        """One query for the whole document. Duplicate glyphs cost nothing extra."""
        wanted = [g for g in dict.fromkeys(glyphs) if g]
        if not wanted or self._connection is None:
            return {}
        requested = set(wanted)
        found: dict[str, list[sqlite3.Row]] = {}
        for chunk in (wanted[i : i + 400] for i in range(0, len(wanted), 400)):
            placeholders = ",".join("?" * len(chunk))
            rows = self._connection.execute(
                "SELECT id,traditional,simplified,numeric_pinyin,definitions FROM entries"
                f" WHERE simplified IN ({placeholders}) OR traditional IN ({placeholders})"
                " ORDER BY id",
                (*chunk, *chunk),
            ).fetchall()
            for row in rows:
                # An entry whose simplified and traditional forms are identical must be
                # bucketed once, or it would masquerade as its own alternative reading.
                for key in dict.fromkeys((row["simplified"], row["traditional"])):
                    if key in requested:
                        found.setdefault(key, []).append(row)
        return {glyph: _entry(rows) for glyph, rows in found.items()}


def _entry(rows: list[sqlite3.Row]) -> DictionaryEntry:
    """Lowest source id is the primary entry; definitions merge across homographs."""
    primary = rows[0]
    definitions: list[str] = []
    for row in rows:
        for definition in row["definitions"].split("\x1f"):
            if definition and definition not in definitions:
                definitions.append(definition)
    alternatives = tuple(
        (tone_marks(row["numeric_pinyin"]), tuple(row["definitions"].split("\x1f")))
        for row in rows[1:]
    )
    return DictionaryEntry(
        simplified=primary["simplified"],
        traditional=primary["traditional"],
        pinyin=tone_marks(primary["numeric_pinyin"]),
        definitions=tuple(definitions[:MAX_DEFINITIONS]),
        alternatives=alternatives,
    )
