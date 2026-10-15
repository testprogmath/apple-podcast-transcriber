"""Local Dutch lexical evidence. Never generates morphology or calls a provider."""

import logging
import os
import sqlite3
import unicodedata
from pathlib import Path
from urllib.parse import quote

from .lexical import normalized

SCHEMA_VERSION = "1"
BUILDER_VERSION = "1"
SNAPSHOT = {
    "source": "English Wiktionary via Kaikki/Wiktextract (raw)",
    "source_url": "https://kaikki.org/dictionary/raw-wiktextract-data.jsonl.gz",
    "dump_date": "2026-09-02",
    "extraction_date": "2026-10-03",
    "compressed_sha256": "3dac8a09e57827bef493e2e6b552fe917bf3b1fc55dee05c4e4a3702aff5dbdb",
    "subset_sha256": "e63cb76e7cac43b8fa03d886bb0cda388f5ec8354e9f9c4854c2f46a7b4d9ff8",
    "wiktextract_revision": "1a05e46f9efbccda6a2b2f8e21b30a9c0c46513a",
    "wikitextprocessor_revision": "e3d6d4edb77618f4d6680edc66e3f774bea59820",
    "attribution": "Wiktionary contributors; extracted by Wiktextract, provided by Kaikki",
    "license": "CC BY-SA 4.0",
    "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
}
GENDER_ARTICLE = {"neuter": "het", "masculine": "de", "feminine": "de", "common-gender": "de"}
REQUIRED = {
    "metadata": "key,value",
    "entries": "id,lemma,key,pos,source_title",
    "senses": "id,entry_id,ordinal",
    "glosses": "sense_id,ordinal,text",
    "labels": "id,value",
    "sense_labels": "sense_id,label_id",
    "surfaces": "id,text,key",
    "features": "id,value",
    "forms": "entry_id,surface_id,feature_id",
    "relations": "entry_id,target_key,kind,feature_id",
}
log = logging.getLogger(__name__)


def reference_sense(sense):
    return bool(sense.get("form_of") or sense.get("alt_of") or "form-of" in sense.get("tags", []))


def article_evidence(pos, tags):
    return sorted({GENDER_ARTICLE[t] for t in tags if t in GENDER_ARTICLE}) if pos == "noun" else []


class DutchDictionary:
    def __init__(self, path=None, *, snapshot=None):
        self.db = None
        self.reason = "missing"
        self.metadata = {}
        path = Path(
            path
            or os.getenv("READER_DUTCH_DICTIONARY")
            or Path(__file__).with_name("dutch.sqlite3")
        )
        if not path.is_file():
            log.warning("stage=dutch-dictionary-unavailable reason=missing")
            return
        connection = None
        try:
            connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            for table, columns in REQUIRED.items():
                connection.execute(f"SELECT {columns} FROM {table} LIMIT 0")
            indexes = {
                r[0]
                for r in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
            }
            if not {"entries_key", "senses_entry", "surfaces_key", "forms_surface"} <= indexes:
                raise ValueError("missing lookup indexes")
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
            expected = {
                **(SNAPSHOT if snapshot is None else snapshot),
                "schema_version": SCHEMA_VERSION,
                "builder_version": BUILDER_VERSION,
                "language": "nl",
            }
            if any(metadata.get(k) != v for k, v in expected.items()):
                raise ValueError("incompatible metadata")
            kind = metadata.get("input_kind")
            if kind not in {"subset", "compressed"} or metadata.get("input_sha256") != metadata.get(
                f"{kind}_sha256"
            ):
                raise ValueError("invalid source input metadata")
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("integrity")
            self.db, self.metadata, self.reason = connection, metadata, ""
        except (sqlite3.Error, ValueError):
            if connection:
                connection.close()
            self.reason = "invalid or incompatible artifact"
            log.warning("stage=dutch-dictionary-unavailable reason=invalid-or-incompatible")

    @property
    def available(self):
        return self.db is not None

    def close(self):
        if self.db:
            self.db.close()
            self.db = None

    def lookup(self, surface, *, form_aware=True):
        result = {
            "surface": surface,
            "normalized_surface": normalized(surface),
            "available": self.available,
            "candidates": [],
            "is_ambiguous": False,
        }
        if not self.available:
            result["reason"] = self.reason
            return result
        try:
            result["candidates"] = self._lookup(surface, form_aware)
            result["is_ambiguous"] = len(result["candidates"]) > 1 or any(
                len({a for sense in c["senses"] for a in sense["articles"]}) > 1
                for c in result["candidates"]
            )
            return result
        except sqlite3.Error:
            self.close()
            self.reason = "artifact read failed"
            log.warning("stage=dutch-dictionary-unavailable reason=read-failed")
            return {**result, "available": False, "reason": self.reason}

    def _lookup(self, surface, form_aware):
        spelling = " ".join(unicodedata.normalize("NFC", surface).split())
        key = normalized(surface)
        exact = list(self.db.execute("SELECT * FROM entries WHERE key=? ORDER BY id", (key,)))
        pending = [(r, 0, {"kind": "direct", "surface": surface, "features": []}) for r in exact]
        if form_aware:
            pending += [
                (
                    r,
                    0,
                    {
                        "kind": "form",
                        "surface": surface,
                        "features": r["features"].split("\x1f") if r["features"] else [],
                    },
                )
                for r in self.db.execute(
                    "SELECT e.*,t.value AS features FROM surfaces s JOIN forms f ON f.surface_id=s.id "
                    "JOIN entries e ON e.id=f.entry_id JOIN features t ON t.id=f.feature_id "
                    "WHERE s.key=? ORDER BY e.id,t.value",
                    (key,),
                )
            ]
        found, visited = {}, set()
        while pending:
            row, depth, relation = pending.pop(0)
            identifier = row["id"]
            candidate = found.get(identifier)
            if candidate is None:
                senses = self._senses(identifier, row["pos"])
                if senses:
                    candidate = found[identifier] = {
                        "id": identifier,
                        "lemma": row["lemma"],
                        "pos": row["pos"],
                        "senses": senses,
                        "source_url": "https://en.wiktionary.org/wiki/"
                        + quote(row["source_title"] or row["lemma"], safe=""),
                        "forms": [],
                        "plural": [],
                        "diminutive": [],
                        "relationships": [],
                    }
                    for f in self.db.execute(
                        "SELECT s.text,t.value FROM forms f JOIN surfaces s ON s.id=f.surface_id "
                        "JOIN features t ON t.id=f.feature_id WHERE f.entry_id=? ORDER BY s.text,t.value",
                        (identifier,),
                    ):
                        tags = set(f["value"].split("\x1f"))
                        field = None
                        if row["pos"] == "noun":
                            if "plural" in tags and "diminutive" not in tags:
                                field = "plural"
                            elif "diminutive" in tags and "plural" not in tags:
                                field = "diminutive"
                        if field:
                            candidate["forms"].append(
                                {"surface": f["text"], "features": sorted(tags)}
                            )
                            if f["text"] not in candidate[field]:
                                candidate[field].append(f["text"])
            if candidate and relation not in candidate["relationships"]:
                candidate["relationships"].append(relation)
            if identifier in visited or depth >= 8 or not form_aware:
                continue
            visited.add(identifier)
            for ref in self.db.execute(
                "SELECT r.*,f.value FROM relations r JOIN features f ON f.id=r.feature_id "
                "WHERE r.entry_id=? ORDER BY r.target_key,r.kind,f.value",
                (identifier,),
            ):
                for other in self.db.execute(
                    "SELECT * FROM entries WHERE key=? AND pos=? ORDER BY id",
                    (ref["target_key"], row["pos"]),
                ):
                    pending.append(
                        (
                            other,
                            depth + 1,
                            {
                                "kind": ref["kind"],
                                "surface": surface,
                                "via": row["lemma"],
                                "features": ref["value"].split("\x1f") if ref["value"] else [],
                            },
                        )
                    )
        # Ordering is presentation evidence only; never a contextual best_candidate.
        return sorted(
            found.values(),
            key=lambda c: (
                not (
                    c["lemma"] == spelling
                    and any(r["kind"] == "direct" for r in c["relationships"])
                ),
                c["pos"] == "name" and c["lemma"] != spelling,
                not any(r["kind"] == "direct" for r in c["relationships"]),
                c["lemma"] != c["lemma"].lower(),
                normalized(c["lemma"]),
                c["pos"],
                c["id"],
            ),
        )

    def _senses(self, identifier, pos):
        result = []
        for row in self.db.execute(
            "SELECT id FROM senses WHERE entry_id=? ORDER BY ordinal", (identifier,)
        ):
            tags = [
                r[0]
                for r in self.db.execute(
                    "SELECT l.value FROM sense_labels s JOIN labels l ON l.id=s.label_id "
                    "WHERE s.sense_id=? ORDER BY l.value",
                    (row[0],),
                )
            ]
            glosses = [
                r[0]
                for r in self.db.execute(
                    "SELECT text FROM glosses WHERE sense_id=? ORDER BY ordinal", (row[0],)
                )
            ]
            supported = article_evidence(pos, tags)
            result.append(
                {
                    "glosses": glosses,
                    "labels": tags,
                    "gender_tags": [t for t in tags if t in GENDER_ARTICLE]
                    if pos == "noun"
                    else [],
                    "articles": supported,
                    "article": supported[0] if len(supported) == 1 else None,
                }
            )
        return result
