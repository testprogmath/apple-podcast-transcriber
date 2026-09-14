"""Reader HTTP API. Pure request dispatch: no sockets, no credentials in any response."""

import json
import logging
import re
from pathlib import Path

from ..hanly.client import merge_glyphs
from ..models import UserError
from .auth import telegram_user_id
from .documents import ReaderDocument
from .tokens import HAN

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
IDENTIFIER = re.compile(r"[0-9a-f]{32}")
DESCRIPTION = {
    "podcast": "Authentic sentences from the podcast transcript.",
    "text": "Sentences selected in the Reader.",
    "file": "Sentences selected in the Reader.",
}
MAX_GLYPHS = 200
MAX_GLYPH_LENGTH = 40
MAX_SENTENCES = 200
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'"
    ),
}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def json_response(status: int, payload: dict):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return status, {"Content-Type": "application/json; charset=utf-8", **SECURITY_HEADERS}, body


class ReaderApi:
    def __init__(self, config, storage, services, dev_mode: bool = False):
        self.config, self.storage, self.services = config, storage, services
        self.dev_mode = dev_mode

    async def dispatch(self, method: str, path: str, headers: dict, body: bytes):
        try:
            return await self.route(method, path, headers, body)
        except ApiError as exc:
            return json_response(exc.status, {"error": exc.message})
        except UserError as exc:
            return json_response(400, {"error": str(exc)})
        except Exception as exc:
            log.error("stage=reader-api exception_type=%s", type(exc).__name__)
            return json_response(500, {"error": "Reader request failed."})

    async def route(self, method: str, path: str, headers: dict, body: bytes):
        if method == "GET" and path in ("/", "/reader", "/reader/"):
            return self.static("index.html")
        if method == "GET" and path.startswith("/reader/"):
            return self.static(path[len("/reader/") :])
        parts = path.strip("/").split("/")
        if parts[:2] == ["api", "reader"] and len(parts) in (3, 4):
            document = self.authorize(headers, parts[2])
            if method == "GET" and len(parts) == 3:
                return self.read(document)
            if method == "POST" and parts[3:] == ["hanly"]:
                return await self.hanly(document, self.payload(body))
            if method == "POST" and parts[3:] == ["mandarin-mosaic"]:
                return await self.mosaic(document, self.payload(body))
            raise ApiError(405, "Unsupported Reader request.")
        raise ApiError(404, "Not found.")

    def static(self, name: str):
        target = STATIC / name
        if name not in {p.name for p in STATIC.iterdir()} or target.suffix not in TYPES:
            raise ApiError(404, "Not found.")
        return 200, {"Content-Type": TYPES[target.suffix], **SECURITY_HEADERS}, target.read_bytes()

    def authorize(self, headers: dict, identifier: str) -> ReaderDocument:
        init_data = headers.get("x-telegram-init-data", "")
        user = telegram_user_id(init_data, self.config.token)
        if user is None and self.dev_mode and not init_data:
            user = self.config.allowed_user_id
        if user != self.config.allowed_user_id:
            raise ApiError(401, "Open this Reader from your Telegram bot.")
        if not IDENTIFIER.fullmatch(identifier):
            raise ApiError(404, "Unknown Reader document.")
        document = self.storage.reader_document(identifier)
        if document is None or document.chat_id != self.config.allowed_user_id:
            raise ApiError(404, "Unknown Reader document.")
        return document

    @staticmethod
    def payload(body: bytes) -> dict:
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "Malformed Reader request.") from None
        if not isinstance(data, dict):
            raise ApiError(400, "Malformed Reader request.")
        return data

    def read(self, document: ReaderDocument):
        sentences = document.sentences()
        tokens = document.tokens(sentences)
        return json_response(
            200,
            {
                "id": document.id,
                "title": document.title,
                "source_type": document.source_type,
                "hanly_available": self.services.hanly is not None,
                "hanly_error": self.services.hanly_error or "",
                "mosaic_available": self.services.mosaic is not None,
                "mosaic_error": self.services.mosaic_error or "",
                "paragraphs": document.paragraphs(sentences),
                "sentences": [
                    {
                        "id": sentence.id,
                        "text": sentence.text,
                        "tokens": [{"t": t.text, "w": t.word} for t in items],
                    }
                    for sentence, items in zip(sentences, tokens, strict=True)
                ],
            },
        )

    def glyphs(self, document: ReaderDocument, data: dict) -> list[str]:
        values = data.get("glyphs")
        if not isinstance(values, list) or not values or len(values) > MAX_GLYPHS:
            raise ApiError(400, "Send between 1 and 200 vocabulary items.")
        for value in values:
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= MAX_GLYPH_LENGTH:
                raise ApiError(400, "A selected vocabulary item is invalid.")
            if not HAN.search(value) or value.strip() not in document.raw_text:
                raise ApiError(400, "A selected item is absent from this Reader document.")
        return merge_glyphs([], values)

    def selected(self, document: ReaderDocument, data: dict):
        values = data.get("sentence_ids")
        if not isinstance(values, list) or not values or len(values) > MAX_SENTENCES:
            raise ApiError(400, "Send between 1 and 200 sentence IDs.")
        sentences = {s.id: s for s in document.sentences()}
        chosen = []
        for value in values:
            if type(value) is not int or value not in sentences:
                raise ApiError(400, "A selected sentence is not part of this Reader document.")
            sentence = sentences[value]
            if not HAN.search(sentence.text):
                raise ApiError(400, "Mandarin Mosaic accepts Chinese sentences only.")
            if sentence not in chosen:
                chosen.append(sentence)
        return chosen

    async def hanly(self, document: ReaderDocument, data: dict):
        service = self.services.hanly
        if service is None:
            raise ApiError(
                503, self.services.hanly_error or "Hanly upload is not configured on the server."
            )
        glyphs = self.glyphs(document, data)
        try:
            result = await service.merge_collection(
                document.hanly_key, document.title, document.title[:1000], glyphs
            )
        except UserError as exc:
            raise ApiError(502, str(exc)) from None
        return json_response(
            200,
            {
                "collection_id": result.collection_id,
                "name": result.name,
                "uploaded": result.requested,
                "total": result.total,
            },
        )

    async def mosaic(self, document: ReaderDocument, data: dict):
        service = self.services.mosaic
        if service is None:
            raise ApiError(
                503,
                self.services.mosaic_error
                or "Mandarin Mosaic upload is not configured on the server.",
            )
        chosen = self.selected(document, data)
        translations = document.known_translations()
        try:
            result = await service.upload_sentences_for(
                document.mosaic_key,
                document.title,
                DESCRIPTION[document.source_type],
                [(s.text, translations.get(s.text, "")) for s in chosen],
                source_reference=document.source_reference or document.mosaic_key,
            )
        except UserError as exc:
            raise ApiError(502, str(exc)) from None
        statuses = service.sentence_status(document.mosaic_key)
        failed = [s.id for s in chosen if statuses.get(s.text) != "uploaded"]
        return json_response(
            200,
            {
                "pack_id": result.pack_id,
                "name": result.name,
                "uploaded": len(chosen) - len(failed),
                "failed_sentence_ids": failed,
                "rejected": result.rejected,
                "unconfirmed": result.unconfirmed,
                "error": result.error or "",
            },
        )
