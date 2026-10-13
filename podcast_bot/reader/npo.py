"""Public NPO acquisition, separate from conservative subtitle normalization."""

import hashlib
import html
import json
import logging
import re
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from ..models import UserError
from ..storage import now
from .documents import MAX_CHARACTERS, ReaderDocument, clean_title
from .sentences import Sentence

PRODUCT = re.compile(r"(?<![\w])VPWON_[0-9]{1,20}(?![\w])")
# Exact descriptions only: parenthetical dialogue is not a sound label.
NON_SPEECH = {
    "explosie",
    "muziek",
    "applaus",
    "deurbel",
    "gelach",
    "stilte",
    "het Wilhelmus speelt",
    "schoten",
    "taptoe-signaal",
}
ELLIPSIS_GAP = 2.0  # Observed enumeration gaps: 1.68 and 2.00 seconds.


def lyrics_text(text):
    # TT888 uses * before lyric lines. Require a detached marker or a capitalized
    # multiword line, not arbitrary emphasis, multiplication or wildcard syntax.
    leading = bool(re.match(r"^\*(?:\s+(?=[^\W\d_])|(?=[A-ZÀ-Ý][^\n]*\s+[^\W\d_]))", text))
    trailing = text.endswith(" *") or (leading and text.endswith("*"))
    original = text
    if leading:
        text = text[1:].lstrip()
    if trailing:
        text = text[:-1].rstrip()
    if "*" in text and (leading or trailing):
        return original, False
    return text, leading or trailing


def fail(code):
    logging.getLogger(__name__).warning("stage=npo-import code=%s", code)
    raise UserError("NPO import: " + code.replace("_", " ") + ".")


def valid_url(url):
    try:
        p = urlsplit(url)
        return (
            p.scheme == "https"
            and p.hostname in {"npo.nl", "www.npo.nl"}
            and p.port in {None, 443}
            and not p.username
            and not p.password
            and not p.query
            and not p.fragment
            and bool(re.fullmatch(r"/start/afspelen/[A-Za-z0-9_-]+/?", p.path))
        )
    except ValueError:
        return False


class PageMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("property") == "og:title":
            self.title = clean_title(attrs.get("content", ""))


@dataclass
class RawCue:
    start: float
    end: float
    raw_text: str
    text: str
    settings: str
    classes: list[str]
    classification: str
    identifier: str = ""
    music_marker: bool = False


def seconds(value):
    m = re.fullmatch(r"(?:(\d{2,}):)?(\d{2}):(\d{2})\.(\d{3})", value)
    if not m or int(m[2]) > 59 or int(m[3]) > 59:
        fail("invalid_vtt")
    return int(m[1] or 0) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000


class NpoSubtitleNormalizer:
    def parse(self, raw):
        blocks = re.split(
            r"\n[ \t]*\n", raw.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
        )
        if not blocks or not re.fullmatch(r"WEBVTT(?:[ \t].*)?(?:\n[^\n]*)*", blocks[0]):
            fail("invalid_vtt")
        cues = []
        for block in blocks[1:]:
            lines = block.splitlines()
            if lines[0].split()[0] in {"NOTE", "STYLE", "REGION"}:
                continue
            identifier = ""
            if "-->" not in lines[0]:
                identifier = lines.pop(0)
            if not lines:
                fail("invalid_vtt")
            m = re.fullmatch(r"(\S+) --> (\S+)(?:[ \t]+(.*))?", lines.pop(0))
            if not m or not lines:
                fail("invalid_vtt")
            start, end = seconds(m[1]), seconds(m[2])
            if end <= start or (cues and start < cues[-1].start):
                fail("invalid_vtt")
            text = "\n".join(lines)
            visible = " ".join(html.unescape(re.sub(r"<[^>]*>", "", text)).split())
            kind = "speech"
            if not cues and visible == "888" and start <= 2:
                kind = "tt888"
            elif visible.lower() in {f"({word.lower()})" for word in NON_SPEECH} or visible in {
                "",
                "*",
                "♪",
                "♫",
            }:
                kind = "non_speech"
            visible, music_marker = lyrics_text(visible) if kind == "speech" else (visible, False)
            cues.append(
                RawCue(
                    start,
                    end,
                    text,
                    visible,
                    m[3] or "",
                    sorted(set(re.findall(r"<c\.([^ >]+)", text))),
                    kind,
                    identifier,
                    music_marker,
                )
            )
        if not cues:
            fail("empty_subtitle_track")
        return cues

    def reconstruct(self, cues):
        groups = []
        pending = ""
        first = last = None
        previous_classes = None
        previous_music = False

        def flush():
            nonlocal pending
            if pending:
                groups.append((pending, first, last))
                pending = ""

        for cue in cues:
            if cue.classification != "speech":
                flush()
                continue
            text = cue.text
            speaker = bool(re.match(r"(?:[-–]|[A-ZÀ-Ý][\w ]{0,30}:)", text))
            # Larger gaps require an explicit ellipsis plus enumeration evidence:
            # another ellipsis item, or a final "en ..." item. Ordinary continuation
            # remains capped at one second.
            enumeration = pending.endswith(("...", "…")) and (
                text.endswith(("...", "…")) or text.startswith("en ")
            )
            gap_limit = ELLIPSIS_GAP if enumeration else 1.0
            continuation = (
                pending
                and cue.start >= last
                and cue.start - last <= gap_limit
                and not speaker
                and not cue.music_marker
                and not previous_music
                and cue.classes == previous_classes
                and not text.startswith(("*", "♪", "♫"))
                and text.lstrip(".… ")[:1].islower()
                and (pending.endswith(("...", "…")) or not re.search(r"[.!?][\"'’”)]*$", pending))
                and len(pending) + len(text) <= 3000
            )
            if continuation:
                separator = (
                    ", "
                    if enumeration and cue.start - last > 1 and not text.startswith("en ")
                    else " "
                )
                pending = (
                    re.sub(r"(?:\.{3}|…)$", "", pending).rstrip()
                    + separator
                    + re.sub(r"^(?:\.{3}|…)\s*", "", text)
                )
            else:
                flush()
                pending, first = text, cue.start
            last = cue.end
            previous_classes = cue.classes
            previous_music = cue.music_marker
            if re.search(r"(?<!\.)[.!?][\"'’”)]*$", pending):
                flush()
        flush()
        sentences, pieces = [], []
        offset = 0
        for text, start, end in groups:
            # Preserve semantic ellipses inside each conservative group.
            spans = re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý])", text)
            for part in spans:
                pieces.append(part)
                sentences.append(
                    Sentence(len(sentences), offset, offset + len(part), part, start, end)
                )
                offset += len(part) + 2
        if not sentences:
            fail("empty_subtitle_track")
        return "\n\n".join(pieces), sentences


class NpoSourceAdapter:
    def __init__(self, client=None):
        self.client = client

    async def fetch(self, client, url, stage):
        for _ in range(6):
            try:
                async with client.stream("GET", url, follow_redirects=False) as response:
                    if response.is_redirect:
                        target = urljoin(url, response.headers.get("location", ""))
                        if stage != "page" or not valid_url(target):
                            fail(f"{stage}_redirect_rejected")
                        url = target
                        continue
                    if stage == "subtitle" and response.status_code == 404:
                        fail("subtitle_not_found")
                    response.raise_for_status()
                    expected = "text/html" if stage == "page" else "text/vtt"
                    if response.headers.get("content-type", "").split(";")[0].lower() != expected:
                        fail(f"unexpected_{stage}_content_type")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 2 * 1024 * 1024:
                            fail(f"{stage}_too_large")
                    return raw.decode("utf-8-sig"), url
            except (httpx.HTTPError, UnicodeError):
                fail("npo_page_fetch_failed" if stage == "page" else "subtitle_fetch_failed")
        fail(f"{stage}_redirect_rejected")

    async def resolve_product_id(self, client, url):
        if not valid_url(url):
            fail("unsupported_npo_url")
        page, final = await self.fetch(client, url, "page")
        identifiers = set(PRODUCT.findall(page))
        if not identifiers:
            fail("product_id_not_found")
        if len(identifiers) != 1:
            fail("ambiguous_product_id")
        metadata = PageMetadata()
        metadata.feed(page)
        return identifiers.pop(), metadata.title, final

    def discover_subtitle(self, product):
        if not PRODUCT.fullmatch(product):
            fail("product_id_not_found")
        return f"https://cdn.npoplayer.nl/subtitles/nl/{product}.vtt"

    async def fetch_subtitles(self, client, product):
        url = self.discover_subtitle(product)
        raw, _ = await self.fetch(client, url, "subtitle")
        return raw, url

    async def import_document(self, owner, url):
        if self.client is None:
            async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
                return await NpoSourceAdapter(client).import_document(owner, url)
        product, title, final = await self.resolve_product_id(self.client, url)
        raw, subtitle_url = await self.fetch_subtitles(self.client, product)
        normalizer = NpoSubtitleNormalizer()
        cues = normalizer.parse(raw)
        text, sentences = normalizer.reconstruct(cues)
        if len(text) > MAX_CHARACTERS:
            fail("subtitle_too_large")
        identifier = hashlib.sha256(f"reader-v1:npo:{owner}:{product}".encode()).hexdigest()[:32]
        metadata = {
            "language": "nl",
            "product_id": product,
            "original_url": url,
            "resolved_url": final,
            "subtitle_url": subtitle_url,
            "raw_vtt": raw,
            "normalizer_version": 2,
            "cues": [asdict(c) for c in cues],
            "sentences": [asdict(s) for s in sentences],
        }
        return ReaderDocument(
            identifier,
            owner,
            title or product,
            "npo",
            product,
            text,
            "reader:" + identifier,
            "reader:" + identifier,
            now(),
            json.dumps(metadata, ensure_ascii=False),
        )
