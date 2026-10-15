"""Stream a pinned raw Wiktextract gzip/JSONL into a compact Dutch SQLite projection."""

import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path

from podcast_bot.reader.dutch import BUILDER_VERSION, SCHEMA_VERSION, SNAPSHOT, reference_sense
from podcast_bot.reader.lexical import normalized

SCHEMA = """
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE entries(id INTEGER PRIMARY KEY,lemma TEXT NOT NULL,key TEXT NOT NULL,pos TEXT NOT NULL,source_title TEXT);
CREATE TABLE senses(id INTEGER PRIMARY KEY,entry_id INTEGER NOT NULL REFERENCES entries(id),ordinal INTEGER NOT NULL);
CREATE TABLE glosses(sense_id INTEGER NOT NULL REFERENCES senses(id),ordinal INTEGER NOT NULL,text TEXT NOT NULL,PRIMARY KEY(sense_id,ordinal)) WITHOUT ROWID;
CREATE TABLE labels(id INTEGER PRIMARY KEY,value TEXT NOT NULL UNIQUE);
CREATE TABLE sense_labels(sense_id INTEGER NOT NULL REFERENCES senses(id),label_id INTEGER NOT NULL REFERENCES labels(id),PRIMARY KEY(sense_id,label_id)) WITHOUT ROWID;
CREATE TABLE surfaces(id INTEGER PRIMARY KEY,text TEXT NOT NULL UNIQUE,key TEXT NOT NULL);
CREATE TABLE features(id INTEGER PRIMARY KEY,value TEXT NOT NULL UNIQUE);
CREATE TABLE forms(entry_id INTEGER NOT NULL REFERENCES entries(id),surface_id INTEGER NOT NULL REFERENCES surfaces(id),feature_id INTEGER NOT NULL REFERENCES features(id),PRIMARY KEY(entry_id,surface_id,feature_id)) WITHOUT ROWID;
CREATE TABLE relations(entry_id INTEGER NOT NULL REFERENCES entries(id),target_key TEXT NOT NULL,kind TEXT NOT NULL,feature_id INTEGER NOT NULL REFERENCES features(id),PRIMARY KEY(entry_id,target_key,kind,feature_id)) WITHOUT ROWID;
CREATE INDEX entries_key ON entries(key,pos);
CREATE INDEX senses_entry ON senses(entry_id,ordinal);
CREATE INDEX surfaces_key ON surfaces(key);
CREATE INDEX forms_surface ON forms(surface_id,entry_id);
"""
META_FORMS = {"table-tags", "inflection-template", "canonical"}


def build(source, output, *, source_kind="subset", snapshot=None, overwrite=False):
    source, output = Path(source), Path(output)
    snapshot = SNAPSHOT if snapshot is None else snapshot
    if source.resolve() == output.resolve():
        raise ValueError("Source and output must differ")
    if output.exists() and not overwrite:
        raise FileExistsError("Refusing to overwrite dictionary")
    began = time.perf_counter()
    checksum = hashlib.file_digest(source.open("rb"), "sha256").hexdigest()
    if checksum != snapshot[f"{source_kind}_sha256"]:
        raise ValueError("Pinned raw source checksum mismatch")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".dutch-", dir=output.parent)
    os.close(fd)
    db = sqlite3.connect(temporary)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(SCHEMA)
        labels, features = {}, {}

        def intern(table, cache, value):
            if value not in cache:
                cache[value] = db.execute(
                    f"INSERT INTO {table}(value) VALUES (?)", (value,)
                ).lastrowid
            return cache[value]

        counts = {"processed_records": 0, "entries": 0}
        opener = gzip.open if source.suffix == ".gz" else open
        with opener(source, "rt", encoding="utf-8") as stream:
            for line in stream:
                record = json.loads(line)
                counts["processed_records"] += 1
                if record.get("lang_code") != "nl":
                    continue
                word, pos = record["word"], record["pos"]
                if not isinstance(word, str) or not word or not isinstance(pos, str):
                    raise ValueError("Malformed Dutch entry")
                identifier = db.execute(
                    "INSERT INTO entries(lemma,key,pos,source_title) VALUES (?,?,?,?)",
                    (word, normalized(word), pos, record.get("original_title")),
                ).lastrowid
                counts["entries"] += 1
                for ordinal, sense in enumerate(record.get("senses", [])):
                    tags = sorted(set(sense.get("tags", [])))
                    feature = intern("features", features, "\x1f".join(tags))
                    for kind in ("form_of", "alt_of"):
                        for ref in sense.get(kind, []):
                            if ref.get("word"):
                                db.execute(
                                    "INSERT OR IGNORE INTO relations VALUES (?,?,?,?)",
                                    (identifier, normalized(ref["word"]), kind, feature),
                                )
                    if reference_sense(sense):
                        continue
                    glosses = [
                        g for g in sense.get("glosses", []) if isinstance(g, str) and g.strip()
                    ]
                    # Empty non-reference senses remain lexical evidence, as in the source gate.
                    sid = db.execute(
                        "INSERT INTO senses(entry_id,ordinal) VALUES (?,?)", (identifier, ordinal)
                    ).lastrowid
                    db.executemany(
                        "INSERT INTO glosses VALUES (?,?,?)",
                        [(sid, i, g) for i, g in enumerate(glosses)],
                    )
                    db.executemany(
                        "INSERT INTO sense_labels VALUES (?,?)",
                        [(sid, intern("labels", labels, t)) for t in tags],
                    )
                for form in record.get("forms", []):
                    text, tags = form.get("form", ""), sorted(set(form.get("tags", [])))
                    if not text or text in {"-", "?", "—"} or META_FORMS.intersection(tags):
                        continue
                    db.execute(
                        "INSERT OR IGNORE INTO surfaces(text,key) VALUES (?,?)",
                        (text, normalized(text)),
                    )
                    surface_id = db.execute(
                        "SELECT id FROM surfaces WHERE text=?", (text,)
                    ).fetchone()[0]
                    feature = intern("features", features, "\x1f".join(tags))
                    db.execute(
                        "INSERT OR IGNORE INTO forms VALUES (?,?,?)",
                        (identifier, surface_id, feature),
                    )
        if not counts["entries"]:
            raise ValueError("No Dutch records")
        for table in ("senses", "glosses", "surfaces", "forms", "relations"):
            counts[table] = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        metadata = {
            **snapshot,
            "schema_version": SCHEMA_VERSION,
            "builder_version": BUILDER_VERSION,
            "input_sha256": checksum,
            "input_kind": source_kind,
            "language": "nl",
            "transformation": "Dutch-only lexical senses, labels, explicit forms and references; no media, quotations, categories or raw JSON",
            **{f"count_{k}": str(v) for k, v in counts.items()},
        }
        db.executemany("INSERT INTO metadata VALUES (?,?)", sorted(metadata.items()))
        db.commit()
        db.execute("VACUUM")
        if (
            db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or db.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise ValueError("Dictionary integrity check failed")
        db.close()
        os.chmod(temporary, 0o644)
        if overwrite:
            os.replace(temporary, output)
        else:
            # Atomic no-clobber publication, even if another build won the race.
            os.link(temporary, output)
            os.unlink(temporary)
        return {
            **counts,
            "bytes": output.stat().st_size,
            "seconds": time.perf_counter() - began,
            "sha256": hashlib.file_digest(output.open("rb"), "sha256").hexdigest(),
        }
    finally:
        db.close()
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-kind", choices=("subset", "compressed"), default="subset")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build(args.source, args.output, source_kind=args.source_kind, overwrite=args.overwrite)
        )
    )


if __name__ == "__main__":
    main()
