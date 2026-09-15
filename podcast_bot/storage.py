import hashlib
import json
import re
import sqlite3
import unicodedata
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .models import Job, Segment, Transcript
from .resolver.apple import parse_url


def now() -> str:
    return datetime.now(UTC).isoformat()


def safe_name(value: str, max_bytes: int = 120) -> str:
    value = unicodedata.normalize("NFC", value)
    value = "".join(c for c in value if not unicodedata.category(c).startswith("C"))
    value = re.sub(r'[<>:"/\\|?*]', "_", value).strip(" .")
    value = value.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore").strip(" .")
    if not value or value.upper().split(".")[0] in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *[f"COM{i}" for i in range(10)],
        *[f"LPT{i}" for i in range(10)],
    }:
        return "episode"
    return value


def request_key(url: str, model: str, language: str | None, hints: str) -> str:
    link = parse_url(url)
    raw = json.dumps([link.podcast_id, link.episode_id, model, language, hints, "pipeline-v1"])
    return hashlib.sha256(raw.encode()).hexdigest()


def atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class Storage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "bot.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY, url TEXT NOT NULL, language TEXT, model TEXT NOT NULL,
          hints TEXT NOT NULL, force INTEGER NOT NULL, chat_id INTEGER NOT NULL,
          message_id INTEGER NOT NULL, state TEXT NOT NULL, cache_key TEXT NOT NULL,
          output_path TEXT, created TEXT NOT NULL, updated TEXT NOT NULL, error TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS active_job ON jobs(cache_key)
          WHERE state IN ('queued','running');
        CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, path TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS languages (podcast_id TEXT PRIMARY KEY, language TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chunks (
          job_id INTEGER, fingerprint TEXT, result TEXT NOT NULL,
          PRIMARY KEY(job_id,fingerprint));
        CREATE TABLE IF NOT EXISTS usage (
          id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, episode TEXT NOT NULL,
          duration REAL NOT NULL, model TEXT NOT NULL, timestamp TEXT NOT NULL,
          status TEXT NOT NULL, estimated_cost REAL);
        """)
        # Additive migration preserves all existing transcription cache/jobs.
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(jobs)")}
        for name, declaration in (
            ("kind", "TEXT NOT NULL DEFAULT 'transcribe'"),
            ("source_path", "TEXT"),
            ("study_settings", "TEXT"),
        ):
            if name not in columns:
                self.db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
        usage_columns = {r[1] for r in self.db.execute("PRAGMA table_info(usage)")}
        for name in ("input_tokens", "output_tokens"):
            if name not in usage_columns:
                self.db.execute(f"ALTER TABLE usage ADD COLUMN {name} INTEGER")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS hanly_collections (
          episode_id TEXT PRIMARY KEY, uuid TEXT UNIQUE NOT NULL, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS hanly_manual_collections (
          key TEXT PRIMARY KEY, uuid TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
          status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS mosaic_packs (
          source_id TEXT PRIMARY KEY, uuid TEXT UNIQUE NOT NULL, payload TEXT NOT NULL,
          status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS mosaic_sentences (
          uuid TEXT PRIMARY KEY, source_id TEXT NOT NULL, payload TEXT NOT NULL,
          status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS recent_material (chat_id INTEGER PRIMARY KEY, source_path TEXT,
          pack_path TEXT, pack_source TEXT);
        CREATE TABLE IF NOT EXISTS study_runs (job_id INTEGER PRIMARY KEY, key TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS study_cache (key TEXT PRIMARY KEY, path TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS study_steps (key TEXT NOT NULL, step TEXT NOT NULL,
          result TEXT NOT NULL, PRIMARY KEY(key,step));
        CREATE TABLE IF NOT EXISTS vocabulary_state (
          glyph TEXT PRIMARY KEY, state TEXT NOT NULL CHECK(state IN ('learning','known')),
          updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS hanly_glyph_notes (
          glyph TEXT PRIMARY KEY, story TEXT NOT NULL, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reader_sentence_translations (
          document_id TEXT NOT NULL, sentence_id INTEGER NOT NULL,
          source_text TEXT NOT NULL, translation TEXT NOT NULL,
          source TEXT NOT NULL CHECK(source='generated'), created TEXT NOT NULL,
          updated TEXT NOT NULL, PRIMARY KEY(document_id,sentence_id));
        CREATE TABLE IF NOT EXISTS reader_translations_by_language (
          document_id TEXT NOT NULL, sentence_id INTEGER NOT NULL,
          language TEXT NOT NULL CHECK(language IN ('ru','en')),
          source_text TEXT NOT NULL, translation TEXT NOT NULL,
          source TEXT NOT NULL CHECK(source='generated'), created TEXT NOT NULL,
          updated TEXT NOT NULL, PRIMARY KEY(document_id,sentence_id,language));
        INSERT OR IGNORE INTO reader_translations_by_language
          SELECT document_id,sentence_id,'ru',source_text,translation,source,created,updated
          FROM reader_sentence_translations;
        CREATE TABLE IF NOT EXISTS reader_documents (
          id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, title TEXT NOT NULL,
          source_type TEXT NOT NULL, source_reference TEXT NOT NULL, raw_text TEXT NOT NULL,
          hanly_key TEXT NOT NULL, mosaic_key TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS study_usage (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL,
          key TEXT NOT NULL, step TEXT NOT NULL, model TEXT NOT NULL, input_chars INTEGER NOT NULL,
          input_tokens INTEGER, cached_input_tokens INTEGER, output_tokens INTEGER,
          timestamp TEXT NOT NULL, status TEXT NOT NULL, estimated_cost REAL);
        """)
        self.db.commit()

    def vocabulary_state(self, glyph: str) -> str | None:
        return self.vocabulary_states([glyph]).get(glyph)

    def vocabulary_states(self, glyphs) -> dict[str, str]:
        """One indexed lookup per 500 unique glyphs, independent of repetitions/documents."""
        unique = list(dict.fromkeys(glyphs))
        result = {}
        for start in range(0, len(unique), 500):
            batch = unique[start : start + 500]
            placeholders = ",".join("?" for _ in batch)
            rows = self.db.execute(
                f"SELECT glyph,state FROM vocabulary_state WHERE glyph IN ({placeholders})", batch
            )
            result.update((row["glyph"], row["state"]) for row in rows)
        return result

    def save_vocabulary_state(self, glyph: str, state: str) -> None:
        self.save_vocabulary_states([glyph], state)

    def save_vocabulary_states(self, glyphs, state: str) -> None:
        if state not in {"learning", "known"}:
            raise ValueError("Persist only learning or known vocabulary states")
        glyphs = list(dict.fromkeys(glyphs))
        if any(not isinstance(g, str) or not g.strip() for g in glyphs):
            raise ValueError("Vocabulary glyph must be nonempty text")
        with self.db:
            self.db.executemany(
                "INSERT INTO vocabulary_state(glyph,state,updated) VALUES (?,?,?) "
                "ON CONFLICT(glyph) DO UPDATE SET state=excluded.state,updated=excluded.updated",
                [(glyph, state, now()) for glyph in glyphs],
            )

    def delete_vocabulary_state(self, glyph: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM vocabulary_state WHERE glyph=?", (glyph,))

    def reader_translation(
        self, document_id: str, sentence_id: int, source_text: str, language: str = "ru"
    ) -> str | None:
        row = self.db.execute(
            "SELECT translation FROM reader_translations_by_language "
            "WHERE document_id=? AND sentence_id=? AND source_text=? AND language=?",
            (document_id, sentence_id, source_text, language),
        ).fetchone()
        return row["translation"] if row else None

    def save_reader_translation(
        self,
        document_id: str,
        sentence_id: int,
        source_text: str,
        translation: str,
        language: str = "ru",
    ) -> None:
        timestamp = now()
        with self.db:
            self.db.execute(
                "INSERT INTO reader_translations_by_language VALUES (?,?,?,?,?,'generated',?,?) "
                "ON CONFLICT(document_id,sentence_id,language) DO UPDATE SET "
                "source_text=excluded.source_text,translation=excluded.translation,"
                "source=excluded.source,updated=excluded.updated",
                (
                    document_id,
                    sentence_id,
                    language,
                    source_text,
                    translation,
                    timestamp,
                    timestamp,
                ),
            )

    def close(self) -> None:
        self.db.close()

    def recover(self) -> None:
        with self.db:
            self.db.execute("UPDATE jobs SET state='queued' WHERE state='running'")
            self.db.execute("UPDATE usage SET status='interrupted-unknown' WHERE status='started'")
            self.db.execute(
                "UPDATE study_usage SET status='interrupted-unknown' WHERE status='started'"
            )

    def job(self, row) -> Job:
        return Job(**{k: row[k] for k in Job.__dataclass_fields__})

    def enqueue(
        self,
        url: str,
        language: str | None,
        model: str,
        hints: str,
        force: bool,
        chat_id: int,
        message_id: int,
        study_settings: str | None = None,
    ) -> tuple[int, int, bool]:
        key = request_key(url, model, language, hints)
        with self.db:
            row = self.db.execute(
                "SELECT id FROM jobs WHERE cache_key=? AND state IN ('queued','running')", (key,)
            ).fetchone()
            if row:
                identifier, duplicate = row[0], True
            else:
                identifier = self.db.execute(
                    """INSERT INTO jobs
                    (url,language,model,hints,force,chat_id,message_id,state,cache_key,created,updated)
                    VALUES (?,?,?,?,?,?,?,'queued',?,?,?)""",
                    (
                        parse_url(url).url,
                        language,
                        model,
                        hints,
                        force,
                        chat_id,
                        message_id,
                        key,
                        now(),
                        now(),
                    ),
                ).lastrowid
                if study_settings:
                    self.db.execute(
                        "UPDATE jobs SET study_settings=? WHERE id=?", (study_settings, identifier)
                    )
                duplicate = False
        position = self.db.execute(
            "SELECT count(*) FROM jobs WHERE state IN ('queued','running') AND id<=?", (identifier,)
        ).fetchone()[0]
        return identifier, position, duplicate

    def claim(self) -> Job | None:
        with self.db:
            # Serialize claims with deployment's drain check, including its marker write.
            self.db.execute("BEGIN IMMEDIATE")
            if (self.root / "deploy-drain").exists():
                return None
            if self.db.execute("SELECT 1 FROM jobs WHERE state='running'").fetchone():
                return None
            row = self.db.execute(
                "SELECT * FROM jobs WHERE state='queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            self.db.execute(
                "UPDATE jobs SET state='running',updated=? WHERE id=?", (now(), row["id"])
            )
        return self.job(self.db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def finish(self, identifier: int, state: str, error: str | None = None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE jobs SET state=?,error=?,updated=? WHERE id=?",
                (state, error, now(), identifier),
            )

    def retry(self, chat_id: int, message_id: int) -> int | None:
        with self.db:
            row = self.db.execute(
                "SELECT * FROM jobs WHERE chat_id=? AND state='failed' ORDER BY updated DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
            if (
                not row
                or self.db.execute(
                    "SELECT 1 FROM jobs WHERE cache_key=? AND state IN ('running','queued')",
                    (row["cache_key"],),
                ).fetchone()
            ):
                return None
            self.db.execute(
                "UPDATE jobs SET state='queued',message_id=?,updated=?,error=NULL WHERE id=?",
                (message_id, now(), row["id"]),
            )
            # Explicit /retry acknowledges that an interrupted request may already have been billed.
            self.db.execute(
                "UPDATE usage SET status='retry-authorized-unknown' WHERE job_id=? AND status='interrupted-unknown'",
                (row["id"],),
            )
            self.db.execute(
                "UPDATE study_usage SET status='retry-authorized-unknown' WHERE (job_id=? OR key IN (SELECT key FROM study_runs WHERE job_id=?)) AND status='interrupted-unknown'",
                (row["id"], row["id"]),
            )
        return row["id"]

    def cached(self, key: str) -> Path | None:
        row = self.db.execute("SELECT path FROM cache WHERE key=?", (key,)).fetchone()
        if (
            row
            and (Path(row[0]) / "transcript.txt").is_file()
            and (Path(row[0]) / "metadata.json").is_file()
        ):
            return Path(row[0])
        return None

    def job_output(self, identifier: int) -> Path | None:
        row = self.db.execute("SELECT output_path FROM jobs WHERE id=?", (identifier,)).fetchone()
        if row and row[0] and (Path(row[0]) / "transcript.txt").is_file():
            return Path(row[0])
        return None

    def save_output(self, job: Job, path: Path) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO cache VALUES (?,?)", (job.cache_key, str(path)))
            self.db.execute("UPDATE jobs SET output_path=? WHERE id=?", (str(path), job.id))

    def remember_language(self, podcast_id: str, language: str | None) -> None:
        if language:
            with self.db:
                self.db.execute(
                    "INSERT OR REPLACE INTO languages VALUES (?,?)", (podcast_id, language)
                )

    def previous_language(self, podcast_id: str) -> str | None:
        row = self.db.execute(
            "SELECT language FROM languages WHERE podcast_id=?", (podcast_id,)
        ).fetchone()
        return row[0] if row else None

    def chunk(self, job_id: int, fingerprint: str) -> Transcript | None:
        row = self.db.execute(
            "SELECT result FROM chunks WHERE job_id=? AND fingerprint=?", (job_id, fingerprint)
        ).fetchone()
        if not row:
            return None
        value = json.loads(row[0])
        return Transcript(
            value["text"],
            [Segment(**s) for s in value["segments"]],
            value["language"],
            value.get("usage", {}),
        )

    def save_chunk(self, job_id: int, fingerprint: str, result: Transcript, usage_id: int) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO chunks VALUES (?,?,?)",
                (job_id, fingerprint, json.dumps(asdict(result), ensure_ascii=False)),
            )
            self.db.execute(
                "UPDATE usage SET status='success',input_tokens=?,output_tokens=? WHERE id=?",
                (result.usage.get("input_tokens"), result.usage.get("output_tokens"), usage_id),
            )

    def start_usage(self, job: Job, episode: str, duration: float, rate: float | None) -> int:
        with self.db:
            return self.db.execute(
                "INSERT INTO usage(job_id,episode,duration,model,timestamp,status,estimated_cost) VALUES (?,?,?,?,?,'started',?)",
                (
                    job.id,
                    episode,
                    duration,
                    job.model,
                    now(),
                    duration / 60 * rate if rate is not None else None,
                ),
            ).lastrowid

    def failed_usage(self, identifier: int) -> None:
        with self.db:
            self.db.execute(
                "UPDATE usage SET status='failure-possibly-billed' WHERE id=?", (identifier,)
            )

    def has_uncertain_usage(self, identifier: int) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM usage WHERE job_id=? AND status='interrupted-unknown'", (identifier,)
            ).fetchone()
            is not None
        )

    def status(self) -> str:
        rows = self.db.execute(
            "SELECT id,state,url FROM jobs WHERE state IN ('running','queued') ORDER BY id LIMIT 15"
        ).fetchall()
        return "\n".join(f"#{r['id']} {r['state']}: {r['url']}" for r in rows) or "No active jobs."

    def preference(self, key: str, default: str) -> str:
        row = self.db.execute("SELECT value FROM preferences WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_preference(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO preferences VALUES (?,?)", (key, value))

    def remember_source(self, chat_id: int, source: Path) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO recent_material(chat_id,source_path) VALUES (?,?) ON CONFLICT(chat_id) DO UPDATE SET source_path=excluded.source_path",
                (chat_id, str(source)),
            )

    def recent_source(self, chat_id: int) -> Path | None:
        row = self.db.execute(
            "SELECT source_path FROM recent_material WHERE chat_id=?", (chat_id,)
        ).fetchone()
        if not row or not row[0]:
            # Upgrade: discover canonical transcripts generated before study support existed.
            row = self.db.execute(
                "SELECT output_path FROM jobs WHERE chat_id=? AND output_path IS NOT NULL ORDER BY updated DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
        path = Path(row[0]) if row and row[0] else None
        return path if path and (path / "transcript.txt").is_file() else None

    def remember_pack(self, chat_id: int, source: Path, pack: Path) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO recent_material(chat_id,source_path,pack_path,pack_source) VALUES (?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET source_path=excluded.source_path,pack_path=excluded.pack_path,pack_source=excluded.pack_source",
                (chat_id, str(source), str(pack), str(source)),
            )

    def recent_pack(self, chat_id: int) -> Path | None:
        row = self.db.execute(
            "SELECT pack_path FROM recent_material WHERE chat_id=?", (chat_id,)
        ).fetchone()
        return Path(row[0]) if row and row[0] else None

    def enqueue_study(
        self, source: Path, settings: str, chat_id: int, message_id: int, regenerate: bool = False
    ) -> tuple[int, int, bool]:
        from .study.settings import StudySettings, study_key

        metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
        key = "study:" + study_key(
            (source / "transcript.txt").read_bytes(), StudySettings.from_json(settings)
        )
        with self.db:
            row = self.db.execute(
                "SELECT id FROM jobs WHERE cache_key=? AND state IN ('queued','running')", (key,)
            ).fetchone()
            if row:
                identifier, duplicate = row[0], True
            else:
                identifier = self.db.execute(
                    """INSERT INTO jobs
                  (url,language,model,hints,force,chat_id,message_id,state,cache_key,created,updated,kind,source_path,study_settings)
                  VALUES (?,?,?,'',0,?,?,'queued',?,?,?,'study',?,?)""",
                    (
                        metadata["apple_url"],
                        metadata.get("language"),
                        metadata["model"],
                        chat_id,
                        message_id,
                        key,
                        now(),
                        now(),
                        str(source),
                        settings,
                    ),
                ).lastrowid
                self.db.execute("UPDATE jobs SET force=? WHERE id=?", (int(regenerate), identifier))
                duplicate = False
        position = self.db.execute(
            "SELECT count(*) FROM jobs WHERE state IN ('queued','running') AND id<=?", (identifier,)
        ).fetchone()[0]
        return identifier, position, duplicate

    def save_reader_document(self, document) -> None:
        with self.db:
            self.db.execute(
                """INSERT INTO reader_documents
                (id,chat_id,title,source_type,source_reference,raw_text,hanly_key,mosaic_key,created)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                  source_reference=excluded.source_reference, raw_text=excluded.raw_text""",
                (
                    document.id,
                    document.chat_id,
                    document.title,
                    document.source_type,
                    document.source_reference,
                    document.raw_text,
                    document.hanly_key,
                    document.mosaic_key,
                    document.created,
                ),
            )

    def reader_document(self, identifier: str):
        from .reader.documents import ReaderDocument

        row = self.db.execute("SELECT * FROM reader_documents WHERE id=?", (identifier,)).fetchone()
        return (
            ReaderDocument(**{k: row[k] for k in ReaderDocument.__dataclass_fields__})
            if row
            else None
        )

    def recent_reader_document(self, chat_id: int):
        row = self.db.execute(
            "SELECT id FROM reader_documents WHERE chat_id=? ORDER BY created DESC, rowid DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
        return self.reader_document(row[0]) if row else None

    def manual_collection(self, key: str):
        return self.db.execute(
            "SELECT uuid, name, status FROM hanly_manual_collections WHERE key=?", (key,)
        ).fetchone()

    def reserve_manual_collection(self, key: str, uuid: str, name: str):
        """Identity and display name are committed before any Hanly network write."""
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO hanly_manual_collections VALUES (?,?,?,?)",
                (key, uuid, name, "pending"),
            )
            self.db.execute(
                "UPDATE hanly_manual_collections SET status='unconfirmed' WHERE key=?", (key,)
            )
        return self.manual_collection(key)

    def confirm_manual_collection(self, key: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE hanly_manual_collections SET status='verified' WHERE key=?", (key,)
            )

    def hanly_note(self, glyph: str) -> str | None:
        """The note this integration last wrote for a glyph, or None if it never wrote one."""
        row = self.db.execute(
            "SELECT story FROM hanly_glyph_notes WHERE glyph=?", (glyph,)
        ).fetchone()
        return row[0] if row else None

    def save_hanly_note(self, glyph: str, story: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO hanly_glyph_notes VALUES (?,?,?)", (glyph, story, now())
            )
