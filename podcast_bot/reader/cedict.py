"""CC-CEDICT parsing and the reproducible conversion to the runtime SQLite database.

The database is a faithful format conversion of CC-CEDICT, which is published by MDBG
under CC BY-SA 4.0. Numeric pinyin is stored exactly as the source writes it; tone marks
are produced for display by `tone_marks`, so the stored data stays a plain reformatting.

Build:  python -m podcast_bot.reader.cedict <cedict_ts.u8[.gz]> <out.sqlite3>
"""

import gzip
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=DELETE;
CREATE TABLE entries (
  id INTEGER PRIMARY KEY,
  traditional TEXT NOT NULL,
  simplified TEXT NOT NULL,
  numeric_pinyin TEXT NOT NULL,
  definitions TEXT NOT NULL);
CREATE INDEX entries_simplified ON entries(simplified);
CREATE INDEX entries_traditional ON entries(traditional);
"""

LINE = re.compile(r"^(\S+)\s+(\S+)\s+\[([^\]]*)\]\s+/(.*)/\s*$")
VOWELS = {
    "a": "āáǎàa",
    "e": "ēéěèe",
    "i": "īíǐìi",
    "o": "ōóǒòo",
    "u": "ūúǔùu",
    "ü": "ǖǘǚǜü",
}
SYLLABLE = re.compile(r"^([^\d]*)([1-5])$")


@dataclass(frozen=True)
class RawEntry:
    traditional: str
    simplified: str
    numeric_pinyin: str
    definitions: tuple[str, ...]


def parse_line(line: str) -> RawEntry | None:
    """Parse one CC-CEDICT line. Comments, blanks and malformed lines return None."""
    line = line.rstrip("\n").rstrip("\r")
    if not line or line.startswith("#"):
        return None
    match = LINE.match(line)
    if not match:
        return None
    traditional, simplified, pinyin, definitions = match.groups()
    parts = tuple(part for part in definitions.split("/") if part.strip())
    if not traditional or not simplified or not parts:
        return None
    return RawEntry(traditional, simplified, pinyin.strip(), parts)


def _mark(syllable: str) -> str:
    match = SYLLABLE.match(syllable)
    if not match:
        return syllable
    body, tone = match.group(1), int(match.group(2))
    body = body.replace("u:", "ü").replace("U:", "Ü").replace("v", "ü").replace("V", "Ü")
    if tone == 5:
        return body
    lowered = body.lower()
    index = None
    for candidate in ("a", "e"):
        if candidate in lowered:
            index = lowered.index(candidate)
            break
    if index is None and "ou" in lowered:
        index = lowered.index("ou")
    if index is None:
        positions = [i for i, c in enumerate(lowered) if c in VOWELS]
        if not positions:
            return body
        index = positions[-1]
    vowel = body[index]
    marked = VOWELS[vowel.lower()][tone - 1]
    return body[:index] + (marked.upper() if vowel.isupper() else marked) + body[index + 1 :]


@lru_cache(maxsize=16384)
def tone_marks(numeric_pinyin: str) -> str:
    """Convert CC-CEDICT numeric pinyin to tone marks: yan2 jiu1 -> yán jiū."""
    if not numeric_pinyin:
        return ""
    return " ".join(_mark(part) for part in numeric_pinyin.split() if part)


def read_entries(source: Path):
    opener = gzip.open if source.suffix == ".gz" else open
    with opener(source, "rt", encoding="utf-8") as stream:
        for line in stream:
            entry = parse_line(line)
            if entry is not None:
                yield entry


def source_version(source: Path) -> dict[str, str]:
    """The `#!` metadata block CC-CEDICT uses to identify a snapshot."""
    opener = gzip.open if source.suffix == ".gz" else open
    metadata = {}
    with opener(source, "rt", encoding="utf-8") as stream:
        for line in stream:
            if not line.startswith("#"):
                break
            if line.startswith("#!"):
                key, _, value = line[2:].strip().partition("=")
                metadata[key.strip()] = value.strip()
    return metadata


def build(source: Path, destination: Path) -> int:
    """Write a deterministic SQLite conversion. Rows keep source order, ids are sequential."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(SCHEMA)
        with connection:
            connection.executemany(
                "INSERT INTO entries(id,traditional,simplified,numeric_pinyin,definitions)"
                " VALUES (?,?,?,?,?)",
                (
                    (
                        index,
                        entry.traditional,
                        unicodedata.normalize("NFC", entry.simplified),
                        entry.numeric_pinyin,
                        "\x1f".join(entry.definitions),
                    )
                    for index, entry in enumerate(read_entries(source))
                ),
            )
        total = connection.execute("SELECT count(*) FROM entries").fetchone()[0]
        connection.execute("VACUUM")
    finally:
        connection.close()
    temporary.replace(destination)
    return total


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    source, destination = Path(argv[1]), Path(argv[2])
    metadata = source_version(source)
    total = build(source, destination)
    print(f"CC-CEDICT snapshot: {metadata.get('date', 'unknown')} ({metadata.get('entries', '?')})")
    print(f"wrote {total} entries to {destination} ({destination.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
