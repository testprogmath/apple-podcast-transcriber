#!/usr/bin/env python3
"""Convert a 大БКРС daily export into a read-only SQLite dictionary.

Standalone on purpose: no third-party packages and no imports from this project, so the
same script can build the shared dictionary used by other applications.

The export is plain UTF-8 text. An entry is a headword at column zero, an optional
indented pinyin line, then indented definition lines carrying ABBYY DSL markup:

    加拿大
     jiānádà
     [m1]Канада[/m]

Definitions are stored with their markup intact so the database stays a faithful
reformatting. `podcast_bot/reader/bkrs.py` is the reference renderer for turning one
into a short display string.

Usage:  build_bkrs_dictionary.py <dabkrs_YYMMDD.gz|.txt> <out.sqlite3>
"""

import gzip
import sqlite3
import sys
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=DELETE;
CREATE TABLE entries (
  id INTEGER PRIMARY KEY,
  headword TEXT NOT NULL,
  pinyin TEXT NOT NULL,
  definition TEXT NOT NULL);
CREATE INDEX entries_headword ON entries(headword);
"""


def read_entries(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    headword, pinyin, definition = None, "", []
    with opener(path, "rt", encoding="utf-8") as stream:
        for raw in stream:
            line = raw.rstrip("\n").rstrip("\r").lstrip("﻿")
            if line.startswith("#"):
                continue
            if not line.strip():
                if headword and definition:
                    yield headword, pinyin, "\n".join(definition)
                headword, pinyin, definition = None, "", []
                continue
            if not line[0].isspace():
                if headword and definition:
                    yield headword, pinyin, "\n".join(definition)
                headword, pinyin, definition = line.strip(), "", []
                continue
            body = line.strip()
            if not body:
                continue
            # The first indented line is pinyin only when it carries no markup.
            if not definition and not pinyin and "[" not in body:
                pinyin = body
            else:
                definition.append(body)
    if headword and definition:
        yield headword, pinyin, "\n".join(definition)


def build(source: Path, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(SCHEMA)
        with connection:
            connection.executemany(
                "INSERT INTO entries(id,headword,pinyin,definition) VALUES (?,?,?,?)",
                (
                    (index, headword, pinyin, definition)
                    for index, (headword, pinyin, definition) in enumerate(read_entries(source))
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
    total = build(source, destination)
    print(f"wrote {total} entries to {destination} ({destination.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
