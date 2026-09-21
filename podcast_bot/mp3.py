"""On-demand audio-only downloads. No transcription or study API calls."""

import asyncio
import json
import re
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit

from .config import Config
from .models import UserError
from .net import http_client
from .resolver import resolve
from .resolver.apple import parse_url
from .storage import safe_name
from .transcription.audio import check_duration, download, inspect_audio, run

MAX_MP3_BYTES = 49_000_000  # Headroom below Telegram's 50 MB sendAudio limit.


def source_url(value: str) -> tuple[str, str]:
    """Accept a single episode/video, never playlists or arbitrary downloader URLs."""
    try:
        p = urlsplit(value)
        if p.scheme != "https" or p.username or p.password or p.port not in (None, 443):
            raise ValueError
        if p.hostname == "podcasts.apple.com":
            return "podcast", parse_url(value).url
        host = p.hostname
        if host == "youtu.be":
            identifier = p.path.removeprefix("/")
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            if p.path == "/watch":
                identifier = parse_qs(p.query).get("v", [""])[0]
            elif re.fullmatch(r"/(shorts|live|embed)/[^/]+", p.path):
                identifier = p.path.rsplit("/", 1)[-1]
            else:
                raise ValueError
        else:
            raise ValueError
        if not re.fullmatch(r"[a-zA-Z0-9_-]{11}", identifier):
            raise ValueError
        return "youtube", f"https://www.youtube.com/watch?v={identifier}"
    except (ValueError, UserError):
        raise UserError(
            "Send /mp3 followed by one Apple Podcasts episode or YouTube video URL."
        ) from None


async def youtube_source(url: str, max_minutes: float) -> tuple[str, str, str]:
    try:
        output, _ = await run(
            sys.executable,
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--no-cache-dir",
            "--no-playlist",
            "--skip-download",
            "--dump-single-json",
            "--no-warnings",
            "--socket-timeout",
            "20",
            "--retries",
            "2",
            "--js-runtimes",
            "node",
            "--no-remote-components",
            "--use-extractors",
            "youtube",
            "-f",
            "bestaudio[protocol=https]",
            "--",
            url,
            timeout=120,
        )
        info = json.loads(output)
        if not isinstance(info, dict):
            raise ValueError("Invalid video metadata")
        if info.get("_type", "video") != "video" or info.get("live_status") in {
            "is_live",
            "is_upcoming",
            "post_live",
        }:
            raise UserError("Live streams and playlists are not supported. Send a finished video.")
        check_duration(float(info.get("duration") or 0), max_minutes)
        if info.get("vcodec") != "none" or info.get("protocol") != "https":
            raise UserError("No downloadable audio-only stream is available for this video.")
        return (
            info["url"],
            str(info.get("title") or "YouTube audio"),
            str(info.get("uploader") or "YouTube"),
        )
    except (ValueError, TypeError, KeyError, OSError, TimeoutError):
        raise UserError(
            "YouTube audio is unavailable. Try another public, finished video."
        ) from None
    except UserError as exc:
        if str(exc).startswith("Audio validation"):
            raise UserError(
                "YouTube download failed. The video may be unavailable or the host may be blocking downloads."
            ) from None
        raise


@dataclass(frozen=True)
class MP3:
    path: Path
    title: str
    performer: str
    duration: int


@asynccontextmanager
async def prepare_mp3(
    url: str,
    config: Config,
    progress: Callable[[str], Awaitable[None]],
) -> AsyncIterator[MP3]:
    kind, url = source_url(url)
    root = config.data_dir / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="mp3-", dir=root) as temporary:
        directory = Path(temporary)
        async with asyncio.timeout(2400), http_client() as client:
            await progress("Finding audio…")
            if kind == "youtube":
                audio_url, title, performer = await youtube_source(url, config.max_minutes)
            else:
                episode = await resolve(url, client)
                audio_url, title, performer = episode.audio_url, episode.title, episode.podcast
                if episode.duration is not None:
                    check_duration(episode.duration, config.max_minutes)
            await progress("Downloading audio…")
            original = await download(
                client, audio_url, directory, config.max_download_mb * 1_000_000
            )
            duration = await inspect_audio(original)
            check_duration(duration, config.max_minutes)
            # Use 128 kbps normally; lower for long speech recordings to fit one Telegram file.
            bitrate = next(
                (rate for rate in (128, 96, 64, 48, 32) if duration * rate * 1000 / 8 < 47_000_000),
                None,
            )
            if bitrate is None:
                raise UserError("This recording is too long to fit in one Telegram MP3 file.")
            target = directory / ("mp3-" + safe_name(title, 100) + ".mp3")
            await progress("Converting to MP3…")
            await run(
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(original),
                "-map",
                "0:a:0",
                "-vn",
                "-sn",
                "-dn",
                "-map_metadata",
                "-1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                f"{bitrate}k",
                "-ar",
                "44100",
                "-ac",
                "2",
                "-metadata",
                f"title={title[:200]}",
                "-metadata",
                f"artist={performer[:200]}",
                "-fs",
                str(MAX_MP3_BYTES),
                str(target),
                timeout=1800,
            )
            if not target.is_file() or not 0 < target.stat().st_size < MAX_MP3_BYTES - 8192:
                raise UserError("The MP3 exceeds Telegram's file limit.")
            converted_duration = await inspect_audio(target)
            if abs(converted_duration - duration) > 1:
                raise UserError("MP3 conversion was incomplete. Please try again.")
            await progress("Sending MP3…")
            yield MP3(target, title[:200], performer[:200], round(duration))
