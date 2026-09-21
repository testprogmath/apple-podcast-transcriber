"""YouTube subtitles plus durable, immutable source audio. Never invokes ASR."""

import asyncio
import hashlib
import json
import os
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile

from .config import Config
from .models import UserError
from .mp3 import prepare_mp3, source_url
from .reader.media import MAX_AUDIO_BYTES, asset_path
from .storage import atomic_json
from .subtitle_import import import_srt
from .subtitles import download_subtitles
from .transcription.audio import inspect_audio


def save_audio(source: Path, root: Path, quota_mb: int) -> str:
    """Content-addressed file published atomically; never alter an existing recording."""
    directory = root / "media"
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise UserError("Reader audio directory must not be a symlink.")
    pending = None
    try:
        with NamedTemporaryFile(dir=directory, prefix=".pending-", delete=False) as target:
            pending = Path(target.name)
            digest, size = hashlib.sha256(), 0
            with source.open("rb") as audio:
                while block := audio.read(1024 * 1024):
                    size += len(block)
                    if size > MAX_AUDIO_BYTES:
                        raise UserError("Reader audio is too large.")
                    digest.update(block)
                    target.write(block)
            target.flush()
            os.fsync(target.fileno())
        if size == 0:
            raise UserError("Reader audio is empty.")
        identifier = digest.hexdigest()
        if asset_path(root, identifier):
            return identifier
        used = sum(p.stat().st_size for p in directory.glob("*.mp3") if p.is_file())
        if used + size > quota_mb * 1_000_000:
            raise UserError("Reader audio storage is full. Materials will use system TTS.")
        pending.replace(directory / f"{identifier}.mp3")
        return identifier
    finally:
        if pending:
            pending.unlink(missing_ok=True)


async def prepare_youtube(
    url: str,
    language: str,
    chat_id: int,
    config: Config,
    progress: Callable[[str], Awaitable[None]],
) -> tuple[Path, str]:
    kind, url = source_url(url)
    if kind != "youtube":
        raise UserError("Use /youtube with one YouTube video URL.")
    await progress("Downloading existing subtitles… No speech recognition.")
    async with download_subtitles(url, language, config.data_dir) as captions:
        source = import_srt(
            config.data_dir,
            captions.srt.read_bytes(),
            captions.srt.name,
            chat_id,
            language.split("-")[0],
            source_url=url,
        )
    timing_path = source / "audio-timing.json"
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    if asset_path(config.data_dir, timing.get("audio_asset")):
        return source, "Saved original audio reused."
    try:
        async with prepare_mp3(url, config, progress, language=language.split("-")[0]) as audio:
            duration = await inspect_audio(audio.path)
            if max(cue["end"] for cue in timing["segments"]) > duration + 0.15:
                raise UserError(
                    "Subtitles extend beyond the recording. Original audio was not linked."
                )
            # Local copy/hash is finite but can be sizeable. Wait for it on cancellation,
            # so the temporary MP3 cannot disappear underneath the worker thread.
            copying = asyncio.create_task(
                asyncio.to_thread(
                    save_audio, audio.path, config.data_dir, config.reader_audio_storage_mb
                )
            )
            try:
                identifier = await asyncio.shield(copying)
            except asyncio.CancelledError:
                with suppress(Exception):
                    await copying
                raise
            timing.update(audio_asset=identifier, duration=duration)
            metadata_path = source / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.update(
                title=audio.title,
                podcast=audio.performer,
                duration=duration,
                requested_audio_language=language.split("-")[0],
            )
            atomic_json(metadata_path, metadata)
            atomic_json(timing_path, timing)
    except (UserError, OSError, TimeoutError) as exc:
        detail = str(exc) if isinstance(exc, UserError) else "Audio could not be saved."
        return source, f"Subtitles saved; Reader will use system TTS. {detail}"
    return source, "Original audio saved for Reader."
