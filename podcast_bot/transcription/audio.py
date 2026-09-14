import asyncio
import json
import math
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..models import Chunk, UserError
from ..net import stream

UPLOAD_BYTES = 24_000_000  # Headroom under documented 25 MB upload limit.
SUPPORTED = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}


def check_ffmpeg() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise UserError("ffmpeg and ffprobe are required. On macOS run: brew install ffmpeg")


async def run(*args: str, timeout: float = 900) -> tuple[str, str]:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    if process.returncode:
        raise UserError("Audio validation or conversion failed. The download may be corrupted.")
    return stdout.decode(errors="replace"), stderr.decode(errors="replace")


def check_duration(duration: float, max_minutes: float) -> None:
    if not math.isfinite(duration) or duration <= 0:
        raise UserError("Couldn't determine a valid audio duration; transcription was not started.")
    if duration > max_minutes * 60:
        raise UserError(
            f"This episode is {duration / 60:.1f} minutes long and exceeds your configured {max_minutes:g}-minute limit."
        )


async def download(client: httpx.AsyncClient, url: str, directory: Path, max_bytes: int) -> Path:
    suffix = Path(urlsplit(url).path).suffix.lower()
    target = directory / ("original" + (suffix if suffix in SUPPORTED else ".audio"))
    try:
        async with asyncio.timeout(1800):
            async with stream(client, url) as response:
                mime = response.headers.get("content-type", "").split(";")[0].lower()
                if mime and not (
                    mime.startswith("audio/")
                    or mime
                    in {
                        "application/octet-stream",
                        "binary/octet-stream",
                        "video/mp4",
                        "video/webm",
                    }
                ):
                    raise UserError("Audio download failed: the host returned non-audio content.")
                length = response.headers.get("content-length")
                if length and int(length) > max_bytes:
                    raise UserError("Audio file exceeds MAX_DOWNLOAD_MB.")
                size = 0
                with target.open("wb") as output:
                    async for block in response.aiter_bytes(64 * 1024):
                        size += len(block)
                        if size > max_bytes:
                            raise UserError("Audio file exceeds MAX_DOWNLOAD_MB.")
                        output.write(block)
                if size == 0:
                    raise UserError("Audio download failed: empty file.")
                if length and not response.headers.get("content-encoding") and size != int(length):
                    raise UserError("Audio download failed: truncated file.")
        return target
    except (httpx.HTTPError, TimeoutError, ValueError):
        raise UserError("Audio download failed. Try again later.") from None


async def inspect_audio(path: Path) -> float:
    stdout, _ = await run(
        "ffprobe",
        "-v",
        "error",
        "-protocol_whitelist",
        "file,pipe",
        "-show_entries",
        "format=duration:stream=codec_type",
        "-of",
        "json",
        str(path),
    )
    try:
        info = json.loads(stdout)
        if not any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
            raise ValueError
        duration = float(info["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError
        return duration
    except (ValueError, KeyError):
        raise UserError(
            "The download contains no valid audio with a measurable duration."
        ) from None


async def validate_audio(path: Path) -> None:
    # Fully decode before charging, to detect corruption beyond the initial header.
    await run(
        "ffmpeg",
        "-v",
        "error",
        "-xerror",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-f",
        "null",
        "-",
    )


async def silence_boundary(path: Path, start: float, target: float) -> float:
    window = min(20.0, target - start)
    origin = target - window
    _, stderr = await run(
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-protocol_whitelist",
        "file,pipe",
        "-ss",
        str(origin),
        "-i",
        str(path),
        "-t",
        str(window),
        "-af",
        "silencedetect=noise=-35dB:d=0.35",
        "-f",
        "null",
        "-",
    )
    starts = [float(s) for s in re.findall(r"silence_start: ([\d.]+)", stderr)]
    ends = [float(s) for s in re.findall(r"silence_end: ([\d.]+)", stderr)]
    # A trailing silence may lack an end marker; preserve truncation of unmatched pairs.
    boundaries = [origin + (a + b) / 2 for a, b in zip(starts, ends, strict=False) if b >= a]
    return boundaries[-1] if boundaries else target


async def prepare(path: Path, duration: float, directory: Path, seconds: int) -> list[Chunk]:
    if path.stat().st_size < UPLOAD_BYTES and duration <= seconds and path.suffix in SUPPORTED:
        return [Chunk(path, 0, duration)]
    chunks: list[Chunk] = []
    start = 0.0
    while start < duration - 0.01:
        end = min(start + seconds, duration)
        if end < duration:
            end = await silence_boundary(path, start, end)
        output = directory / f"chunk-{len(chunks):04d}.mp3"
        # Preserve compressed source audio when its container supports safe stream-copy.
        copy = path.suffix == ".mp3"
        if copy:
            await run(
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-protocol_whitelist",
                "file,pipe",
                "-ss",
                str(start),
                "-i",
                str(path),
                "-t",
                str(end - start),
                "-map",
                "0:a:0",
                "-c:a",
                "copy",
                str(output),
            )
        if not copy or output.stat().st_size >= UPLOAD_BYTES:
            await run(
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(path),
                "-ss",
                str(start),
                "-t",
                str(end - start),
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                "24000",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "64k",
                str(output),
            )
        if output.stat().st_size == 0 or output.stat().st_size >= UPLOAD_BYTES:
            raise UserError("Couldn't prepare audio within the upload limit.")
        chunks.append(Chunk(output, start, end - start))
        start = end
    return chunks
