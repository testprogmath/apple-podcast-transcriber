"""Conservative alignment of canonical sentences to persisted provider timestamps."""

import ipaddress
import json
import math
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit


def normalized(text):
    return "".join(c for c in unicodedata.normalize("NFC", text) if c.isalnum())


def safe_source(url):
    """Only persisted public HTTPS enclosures; never accept browser-supplied URLs."""
    if not isinstance(url, str) or len(url) > 4096:
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if (
            parts.scheme != "https"
            or parts.username
            or parts.password
            or parts.port not in (None, 443)
            or "." not in host
            or host.endswith((".local", ".localhost", ".internal"))
        ):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        return url
    except ValueError:
        return None


def align(sentences, units, duration):
    """Align whole normalized streams in order; never search repeated text or split a unit."""
    if not units or not math.isfinite(duration) or duration <= 0:
        return {}
    text, starts, ends, valid = "", {}, {}, []
    previous = 0.0
    for unit in units:
        start, end = float(unit["start"]), float(unit["end"])
        value = normalized(unit["text"])
        if (
            not value
            or not math.isfinite(start)
            or not math.isfinite(end)
            or not 0 <= previous <= start < end <= duration
        ):
            return {}
        starts[len(text)] = len(valid)
        text += value
        ends[len(text)] = len(valid)
        valid.append((start, end))
        previous = end
    values = [normalized(s.text) for s in sentences]
    if "".join(values) != text:
        return {}
    result, offset = {}, 0
    for sentence, value in zip(sentences, values, strict=True):
        first, last = starts.get(offset), ends.get(offset + len(value))
        offset += len(value)
        if not value or first is None or last is None:
            continue  # Shared segment/word: no defensible intra-unit timestamp.
        start, end = valid[first][0], valid[last][1]
        padded_start = max(0, start - 0.12, valid[first - 1][1] if first else 0)
        padded_end = min(
            duration, end + 0.2, valid[last + 1][0] if last + 1 < len(valid) else duration
        )
        result[sentence.id] = {
            "source": "podcast",
            "start_ms": round(padded_start * 1000),
            "end_ms": round(padded_end * 1000),
        }
    return result


def document_audio(document, sentences):
    if document.source_type != "podcast" or not document.source_reference:
        return None, {}
    path = Path(document.source_reference) / "audio-timing.json"
    try:
        if path.stat().st_size > 20_000_000:
            return None, {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or data.get("text") != document.raw_text:
            return None, {}
        url = safe_source(data.get("audio_url"))
        if not url:
            return None, {}
        duration = float(data["duration"])
        ranges = align(sentences, data.get("words", []), duration)
        granularity = "word"
        if not ranges:
            ranges = align(sentences, data.get("segments", []), duration)
            granularity = "segment"
        if not ranges:
            return None, {}
        return {"url": url, "duration": duration, "granularity": granularity}, ranges
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return None, {}
