"""Download existing YouTube captions, without media or speech recognition."""

import html
import json
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit

from .models import UserError
from .mp3 import source_url
from .net import fetch, http_client
from .transcription.audio import run

MAX_SUBTITLE_BYTES = 2_000_000


def request(text: str, default_language: str) -> tuple[str, str]:
    fields = text.split()[1:]
    language = default_language if default_language != "auto" else "zh"
    if len(fields) == 2:
        language, url = fields
    elif len(fields) == 1:
        url = fields[0]
    else:
        raise UserError(
            "Send /subs [language] <YouTube URL>, for example /subs zh https://youtu.be/…"
        )
    if not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z]{2,8})?", language):
        raise UserError("Use a subtitle language code such as zh, zh-Hant, en, de or nl.")
    try:
        kind, url = source_url(url)
        if kind != "youtube":
            raise ValueError
    except (UserError, ValueError):
        raise UserError("/subs needs one YouTube video URL.") from None
    return url, language


def choose_track(info: dict, language: str) -> tuple[str, str, bool]:
    """Prefer published tracks, then existing auto-captions; never auto-translate."""
    for group, automatic in (("subtitles", False), ("automatic_captions", True)):
        tracks = info.get(group) or {}
        if not isinstance(tracks, dict):
            continue
        keys = sorted(tracks, key=lambda key: (key.lower() != language.lower(), key))
        for key in keys:
            if key.lower() != language.lower() and not key.lower().startswith(
                language.lower() + "-"
            ):
                continue
            for track in tracks[key] or []:
                url = track.get("url", "")
                if (
                    track.get("ext") == "srt"
                    and url
                    and not parse_qs(urlsplit(url).query).get("tlang")
                ):
                    return url, key, automatic
    raise UserError(
        f"No downloadable {language} subtitles found. No speech recognition was started. Try /subs with another language code."
    )


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


def parse_srt(srt: str) -> list[Cue]:
    srt = srt.lstrip("\ufeff").replace("\r\n", "\n").strip()
    cues = []
    timestamp = r"(\d{2,4}):([0-5]\d):([0-5]\d),(\d{3})"
    previous = -1.0
    for block in re.split(r"\n\s*\n", srt):
        parts = block.splitlines()
        match = (
            re.fullmatch(timestamp + r" --> " + timestamp + r"(?:[ \t]+.*)?", parts[1])
            if len(parts) >= 3
            else None
        )
        if not match or not parts[0].isdigit():
            raise UserError(
                "Invalid SRT subtitles. Expected numbered cues with start/end timestamps."
            )
        values = [int(v) for v in match.groups()]
        start = values[0] * 3600 + values[1] * 60 + values[2] + values[3] / 1000
        end = values[4] * 3600 + values[5] * 60 + values[6] + values[7] / 1000
        if end <= start or start < previous:
            raise UserError("Invalid SRT timing: cues must be ordered and end after they start.")
        previous = start
        text = html.unescape(re.sub(r"<[^>]*>", "", " ".join(parts[2:]))).strip()
        if text:
            cues.append(Cue(start, end, text))
    if not cues:
        raise UserError("The subtitle track is empty.")
    return cues


def plain_text(srt: str) -> str:
    return "\n".join(cue.text for cue in parse_srt(srt)) + "\n"


@dataclass(frozen=True)
class Subtitles:
    srt: Path
    text: Path
    language: str
    automatic: bool


@asynccontextmanager
async def download_subtitles(url: str, language: str, data_dir: Path) -> AsyncIterator[Subtitles]:
    # Revalidate at the service boundary; the extractor never receives an arbitrary URL.
    url, language = request(f"/subs {language} {url}", "zh")
    try:
        output, _ = await run(
            sys.executable,
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--no-cache-dir",
            "--skip-download",
            "--no-playlist",
            "--dump-single-json",
            "--no-warnings",
            "--ignore-no-formats-error",
            "--socket-timeout",
            "20",
            "--retries",
            "1",
            "--extractor-retries",
            "1",
            "--js-runtimes",
            "node",
            "--no-remote-components",
            "--use-extractors",
            "youtube",
            "--",
            url,
            timeout=120,
        )
        info = json.loads(output)
        if not isinstance(info, dict) or info.get("_type", "video") != "video":
            raise ValueError
    except (UserError, OSError, TimeoutError, ValueError):
        raise UserError(
            "Could not read YouTube subtitles. The video may be unavailable; try again later."
        ) from None
    track_url, selected, automatic = choose_track(info, language)
    try:
        async with http_client() as client:
            raw = await fetch(client, track_url, MAX_SUBTITLE_BYTES)
        srt = raw.decode("utf-8-sig")
    except Exception as exc:
        # Never leak signed caption URLs, provider output or credentials.
        raise UserError("Could not download YouTube subtitles. Please try again later.") from exc
    text = plain_text(srt)
    root = data_dir / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="subs-", dir=root) as temporary:
        # The ID is taken from the validated canonical URL, not remote filenames.
        identifier = parse_qs(urlsplit(url).query)["v"][0]
        path = Path(temporary) / f"{identifier}.{language}.srt"
        path.write_text(srt, encoding="utf-8")
        path.with_suffix(".txt").write_text(text, encoding="utf-8")
        yield Subtitles(path, path.with_suffix(".txt"), selected, automatic)
