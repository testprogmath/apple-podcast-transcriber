import asyncio
import hashlib
import json
import logging
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path

import httpx

from .config import Config
from .models import Job, UserError
from .resolver import resolve
from .storage import Storage, atomic_json, safe_name
from .transcription import audio
from .transcription.openai import Transcriber, combine, to_srt

log = logging.getLogger(__name__)
Status = Callable[[str], Awaitable[None]]


def clock_text(seconds: float) -> str:
    minutes, seconds = divmod(round(seconds), 60)
    return f"{minutes:02}:{seconds:02}"


def fingerprint(path: Path, offset: float, duration: float) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            hasher.update(block)
    hasher.update(f"{offset:.6f}:{duration:.6f}".encode())
    return hasher.hexdigest()


class Pipeline:
    def __init__(
        self, config: Config, storage: Storage, client: httpx.AsyncClient, transcriber: Transcriber
    ):
        self.config, self.storage = config, storage
        self.client, self.transcriber = client, transcriber

    async def process(self, job: Job, status: Status) -> Path:
        # A completed forced run must also survive a Telegram delivery failure.
        existing = self.storage.job_output(job.id)
        if not existing and not job.force:
            existing = self.storage.cached(job.cache_key)
        if existing:
            await status("Already transcribed.")
            return existing
        if self.storage.has_uncertain_usage(job.id):
            raise UserError(
                "The process stopped during an API request, which may have been billed. Use /retry to resume explicitly; completed chunks will be reused."
            )
        await status("Resolving Apple Podcasts episode…")
        episode = await resolve(job.url, self.client)
        if episode.duration is not None:
            audio.check_duration(episode.duration, self.config.max_minutes)
        language = (
            job.language or episode.language or self.storage.previous_language(episode.podcast_id)
        )
        header = f"🎙 {episode.podcast[:200]}\n{episode.title[:500]}"
        await status(header + "\nFound the episode.\nDownloading audio…")
        work = self.storage.root / "tmp"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"job-{job.id}-", dir=work) as temporary:
            directory = Path(temporary)
            original = await audio.download(
                self.client, episode.audio_url, directory, self.config.max_download_mb * 1_000_000
            )
            duration = await audio.inspect_audio(original)
            audio.check_duration(duration, self.config.max_minutes)
            await status(header + "\nValidating audio…")
            await audio.validate_audio(original)
            chunk_seconds = (
                min(self.config.chunk_seconds, 180)
                if job.model.startswith("gpt-4o-")
                else self.config.chunk_seconds
            )
            chunks = await audio.prepare(original, duration, directory, chunk_seconds)
            prompt = f"Episode title (context only): {episode.title[:180]}\nTranscribe faithfully in the original language. Do not translate or add unspoken words. "
            if language == "zh":
                prompt += "普通话播客。保留简体中文和自然的中文标点。"
            if episode.podcast_id == "1490732024" and language == "zh":
                prompt += "主持人是大鹏，内容涉及汉语语法、词汇、表达和文化。"
            prompt += f"\n{job.hints}"
            started = time.monotonic()
            parts = []
            for index, chunk in enumerate(chunks):
                await status(
                    header
                    + f"\nTranscribing… {index + 1}/{len(chunks)}\nLanguage: {language or 'automatic'}\nDuration: {clock_text(duration)}"
                )
                digest = await asyncio.to_thread(
                    fingerprint, chunk.path, chunk.offset, chunk.duration
                )
                result = self.storage.chunk(job.id, digest)
                if result is None:
                    usage_id = self.storage.start_usage(
                        job, episode.title, chunk.duration, self.config.cost_per_minute
                    )
                    try:
                        result = await self.transcriber.transcribe(
                            chunk, job.model, language, prompt
                        )
                        self.storage.save_chunk(job.id, digest, result, usage_id)
                    except asyncio.CancelledError:
                        # Remains started until recover() marks the uncertain request.
                        raise
                    except Exception:
                        self.storage.failed_usage(usage_id)
                        raise
                parts.append((chunk.offset, result))
            transcript = combine(parts)
            elapsed = time.monotonic() - started
            output = (
                self.storage.root
                / "transcripts"
                / safe_name(episode.podcast)
                / f"{episode.episode_id}-{job.cache_key[:12]}"
                / f"run-{job.id}"
            )
            output.mkdir(parents=True, exist_ok=True)
            (output / "transcript.txt.tmp").write_text(transcript.text, encoding="utf-8")
            (output / "transcript.txt.tmp").replace(output / "transcript.txt")
            atomic_json(
                output / "audio-timing.json",
                {
                    "version": 1,
                    "text": transcript.text,
                    "audio_url": episode.audio_url,
                    "duration": duration,
                    "words": [asdict(w) for w in transcript.words],
                    "segments": [asdict(s) for s in transcript.segments],
                },
            )
            if transcript.segments:
                (output / "transcript.srt").write_text(to_srt(transcript), encoding="utf-8")
            atomic_json(
                output / "metadata.json",
                {
                    **asdict(episode),
                    "language": transcript.language or language,
                    "model": job.model,
                    "duration": duration,
                    "transcription_seconds": elapsed,
                    "chunks": len(chunks),
                    "job_id": job.id,
                    "filename": safe_name(episode.podcast, 60)
                    + " - "
                    + safe_name(episode.title, 100),
                },
            )
            self.storage.save_output(job, output)
            self.storage.remember_language(episode.podcast_id, transcript.language or language)
        # TemporaryDirectory removes original and chunks on success, error, or cancellation.
        return output


def cleanup_abandoned(storage: Storage) -> None:
    """Call only while holding the single-process lock."""
    root = storage.root / "tmp"
    if root.exists():
        for path in (*root.glob("job-*"), *root.glob("mp3-*"), *root.glob("subs-*")):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)


def source_links(metadata: dict) -> str:
    """Where the episode came from, so the audio is one tap away beside its transcript."""
    lines = []
    for label, key in (("🔊 Audio", "audio_url"), ("🎧 Apple Podcasts", "apple_url")):
        value = str(metadata.get(key) or "").strip()
        if value.startswith("https://"):
            lines.append(f"{label}: {value}")
    return ("\n\n" + "\n".join(lines)) if lines else ""


def completion(path: Path) -> str:
    metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("study_complete"):
        settings = metadata["study_settings"]
        if settings["target_language"] != "zh":
            return (
                f"🎙 {metadata.get('title', 'Episode')[:500]}\n✓ Transcript\n"
                f"✓ {metadata['vocabulary_count']} useful words & expressions"
                + source_links(metadata)
            )
        return (
            f"🎙 {metadata.get('podcast', 'Podcast')[:200]}\n{metadata.get('title', 'Episode')[:500]}\n"
            "✓ Transcript\n"
            + ("✓ Pinyin\n" if settings["target_language"] == "zh" else "")
            + f"✓ Translation ({settings['native_language']})\n"
            f"✓ {metadata['vocabulary_count']} useful words & expressions\n"
            f"✓ {metadata['pattern_count']} grammar patterns\n"
            f"✓ {metadata.get('mosaic_sentence_count', 0)} Mosaic sentences\n"
            f"Level: {settings['learner_level']}" + source_links(metadata)
        )
    return (
        f"Done.\nLanguage: {metadata.get('language') or 'automatic'}"
        f"\nDuration: {clock_text(metadata['duration'])}"
        f"\nTranscription: {clock_text(metadata['transcription_seconds'])}" + source_links(metadata)
    )
