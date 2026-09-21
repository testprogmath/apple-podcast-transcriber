"""Private local audio: scoped browser grants and bounded byte-range responses."""

import hashlib
import hmac
import re
import time
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from pathlib import Path

GRANT_SECONDS = 12 * 60 * 60
COOKIE = "reader_audio"
MAX_AUDIO_BYTES = 49_000_000


def asset_path(root: Path, identifier: str) -> Path | None:
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{64}", identifier):
        return None
    directory = root / "media"
    path = directory / f"{identifier}.mp3"
    try:
        if directory.is_symlink() or path.is_symlink() or not path.is_file():
            return None
        if not 0 < path.stat().st_size <= MAX_AUDIO_BYTES:
            return None
        return path
    except OSError:
        return None


def signature(secret: str, document_id: str, owner: int, expiry: str) -> str:
    message = f"reader-audio-v1:{document_id}:{owner}:{expiry}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def grant_cookie(secret: str, document_id: str, owner: int) -> str:
    expiry = str(int(time.time()) + GRANT_SECONDS)
    token = expiry + "." + signature(secret, document_id, owner, expiry)
    return f"{COOKIE}={token}; Path=/api/reader/{document_id}/audio; Max-Age={GRANT_SECONDS}; Secure; HttpOnly; SameSite=Strict"


def valid_grant(header: str, secret: str, document_id: str, owner: int) -> bool:
    try:
        cookie = SimpleCookie()
        cookie.load(header)
        token = cookie[COOKIE].value
        expiry, digest = token.split(".")
        if not re.fullmatch(r"\d{1,12}", expiry) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return False
        if not time.time() < int(expiry) <= time.time() + GRANT_SECONDS:
            return False
        return hmac.compare_digest(digest, signature(secret, document_id, owner, expiry))
    except (CookieError, KeyError, ValueError, TypeError):
        return False


@dataclass(frozen=True)
class FileSlice:
    path: Path
    offset: int
    length: int
    head_only: bool = False


def audio_response(path: Path, method: str, headers: dict):
    size = path.stat().st_size
    response_headers = {
        "Content-Type": "audio/mpeg",
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    value = headers.get("range", "") if method == "GET" and not headers.get("if-range") else ""
    start, end, status = 0, size - 1, 200
    if value:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", value) if len(value) <= 128 else None
        try:
            if not match or not any(match.groups()):
                raise ValueError
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
            else:
                if int(last) <= 0:
                    raise ValueError
                start = max(0, size - int(last))
            if start >= size or start > end:
                raise ValueError
        except ValueError:
            return 416, {**response_headers, "Content-Range": f"bytes */{size}"}, b""
        status = 206
        response_headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return status, response_headers, FileSlice(path, start, end - start + 1, method == "HEAD")
