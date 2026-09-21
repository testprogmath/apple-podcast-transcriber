"""Reader HTTP API. Pure request dispatch: no sockets, no credentials in any response."""

import json
import logging
import re
from pathlib import Path

from ..hanly.client import merge_glyphs
from ..hanly.notes import build_hanly_note
from ..models import UserError
from .audio import document_audio
from .auth import telegram_user_id
from .bkrs import RussianDictionary
from .dictionary import Dictionary
from .documents import ReaderDocument
from .enrich import enrich
from .media import asset_path, audio_response, grant_cookie, valid_grant
from .tokens import HAN, tokenize
from .translations import SentenceTranslations

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
IDENTIFIER = re.compile(r"[0-9a-f]{32}")
DESCRIPTION = {
    "subtitles": "Sentences from the uploaded subtitles.",
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
        "media-src 'self' https:; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'"
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
    def __init__(
        self,
        config,
        storage,
        services,
        dev_mode: bool = False,
        dictionary=None,
        russian=None,
        translations=None,
    ):
        self.config, self.storage, self.services = config, storage, services
        self.translations = translations or SentenceTranslations(storage)
        self.dev_mode = dev_mode
        self.dictionary = Dictionary() if dictionary is None else dictionary
        self.russian = RussianDictionary() if russian is None else russian

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
        if len(parts) == 4 and parts[:2] == ["api", "reader"] and parts[3] == "audio":
            if method not in {"GET", "HEAD"}:
                raise ApiError(405, "Use GET or HEAD for audio.")
            identifier = parts[2]
            if not IDENTIFIER.fullmatch(identifier):
                raise ApiError(404, "Unknown Reader document.")
            if headers.get("x-telegram-init-data"):
                document = self.authorize(headers, identifier)
            else:
                if not valid_grant(
                    headers.get("cookie", ""),
                    self.config.token,
                    identifier,
                    self.config.allowed_user_id,
                ):
                    raise ApiError(401, "Reopen Reader to access audio.")
                document = self.storage.reader_document(identifier)
                if document is None or document.chat_id != self.config.allowed_user_id:
                    raise ApiError(404, "Unknown Reader document.")
            source, _, _ = document_audio(document, document.sentences(), self.storage.root)
            local = asset_path(self.storage.root, source.get("asset_id")) if source else None
            if local is None:
                raise ApiError(404, "Audio unavailable.")
            return audio_response(local, method, headers)
        if (
            parts[:2] == ["api", "reader"]
            and len(parts) == 6
            and parts[3] == "sentences"
            and parts[5] == "translation"
        ):
            document = self.authorize(headers, parts[2])
            if method != "POST":
                raise ApiError(405, "Use POST for sentence translation.")
            data = self.payload(body) if body else {}
            language = data.get("language", "ru")
            if (
                set(data) - {"language"}
                or not isinstance(language, str)
                or language not in {"ru", "en"}
            ):
                raise ApiError(400, "Translation accepts only language: ru or en.")
            if not re.fullmatch(r"0|[1-9][0-9]{0,5}", parts[4]):
                raise ApiError(400, "Invalid sentence ID.")
            sentence = next((s for s in document.sentences() if s.id == int(parts[4])), None)
            if sentence is None:
                raise ApiError(404, "Unknown Reader sentence.")
            return json_response(200, await self.translations.resolve(document, sentence, language))
        if parts[:2] == ["api", "reader"] and len(parts) in (3, 4):
            document = self.authorize(headers, parts[2])
            if method == "GET" and len(parts) == 3:
                return self.read(document)
            if method == "POST" and parts[3:] == ["vocabulary-state"]:
                return self.vocabulary(document, self.payload(body))
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
        audio, ranges, audio_status = document_audio(document, sentences, self.storage.root)
        local_asset = audio.pop("asset_id", None) if audio else None
        tokens = document.tokens(sentences)
        lexemes = enrich(
            (token.text for items in tokens for token in items if token.word),
            document.glyph_meanings(),
            document.glyph_pronunciations(),
            self.dictionary,
            self.russian,
        )
        # Keyed by glyph rather than repeated per token: a transcript repeats each word
        # about three times, and this is what makes carrying both languages affordable.
        states = self.storage.vocabulary_states(lexemes)
        glossary = {}
        for glyph, lexeme in lexemes.items():
            entry = {"p": lexeme.pinyin, "vocabulary_state": states.get(glyph, "unknown")}
            if lexeme.native():
                entry["ru"] = list(lexeme.native())
                entry["rs"] = lexeme.native_source
            if lexeme.english:
                entry["en"] = list(lexeme.english)
            glossary[glyph] = entry

        def describe(token):
            return {"t": token.text, "w": True} if token.word else {"t": token.text, "w": False}

        result = json_response(
            200,
            {
                "id": document.id,
                "title": document.title,
                "source_type": document.source_type,
                "audio": audio,
                "audio_status": audio_status,
                "hanly_available": self.services.hanly is not None,
                "hanly_error": self.services.hanly_error or "",
                "mosaic_available": self.services.mosaic is not None,
                "mosaic_error": self.services.mosaic_error or "",
                "native_language": document.native_language() or "ru",
                "glossary": glossary,
                "paragraphs": document.paragraphs(sentences),
                "sentences": [
                    {
                        "id": sentence.id,
                        "text": sentence.text,
                        "audio": ranges.get(sentence.id),
                        "tokens": [describe(t) for t in items],
                    }
                    for sentence, items in zip(sentences, tokens, strict=True)
                ],
            },
        )

        if local_asset:
            result[1]["Set-Cookie"] = grant_cookie(self.config.token, document.id, document.chat_id)
            result[1]["Cache-Control"] = "private, no-store"
        return result

    def vocabulary(self, document: ReaderDocument, data: dict):
        if set(data) != {"glyph", "state"}:
            raise ApiError(400, "Send only glyph and state.")
        glyph, state = data["glyph"], data["state"]
        if not isinstance(state, str) or state not in {"known", "unknown"}:
            raise ApiError(400, "Choose known or unknown.")
        if (
            not isinstance(glyph, str)
            or not 1 <= len(glyph) <= MAX_GLYPH_LENGTH
            or glyph != glyph.strip()
            or not HAN.search(glyph)
        ):
            raise ApiError(400, "Invalid vocabulary item.")
        # Use the same source-owned tokenization as the popup, never browser-supplied text.
        tokens = document.tokens(document.sentences())
        if not any(token.word and token.text == glyph for group in tokens for token in group):
            raise ApiError(400, "That item is not a Reader word in this document.")
        if state == "unknown":
            self.storage.delete_vocabulary_state(glyph)
        else:
            self.storage.save_vocabulary_state(glyph, state)
        # Basket membership is local; the frontend derives effective state from this value.
        return json_response(200, {"glyph": glyph, "vocabulary_state": state})

    def items(self, document: ReaderDocument, data: dict):
        """Resolve client selections into (glyph, canonical sentence) pairs, first occurrence wins."""
        values = data.get("items")
        if not isinstance(values, list) or not values or len(values) > MAX_GLYPHS:
            raise ApiError(400, "Send between 1 and 200 vocabulary items.")
        sentences = {s.id: s for s in document.sentences()}
        known = document.known_chunks()
        words: dict[int, set[str]] = {}
        chosen, seen = [], set()
        for value in values:
            if not isinstance(value, dict) or set(value) != {"glyph", "sentence_id"}:
                raise ApiError(400, "A selected vocabulary item is invalid.")
            glyph, identifier = value["glyph"], value["sentence_id"]
            if not isinstance(glyph, str) or not 1 <= len(glyph.strip()) <= MAX_GLYPH_LENGTH:
                raise ApiError(400, "A selected vocabulary item is invalid.")
            glyph = glyph.strip()
            if not HAN.search(glyph):
                raise ApiError(400, "A selected vocabulary item is invalid.")
            if type(identifier) is not int or identifier not in sentences:
                raise ApiError(400, "A selected sentence is not part of this Reader document.")
            sentence = sentences[identifier]
            if identifier not in words:
                words[identifier] = {
                    token.text for token in tokenize(sentence.text, known) if token.word
                }
            # Only lexical items the Reader itself offered in that sentence are uploadable.
            if glyph not in words[identifier]:
                raise ApiError(400, "That item is not a Reader word in the selected sentence.")
            if glyph not in seen:
                seen.add(glyph)
                chosen.append((glyph, sentence))
        return chosen

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
        chosen = self.items(document, data)
        glyphs = merge_glyphs([], [glyph for glyph, _ in chosen])
        try:
            result = await service.merge_collection(
                document.hanly_key, document.title, document.title[:1000], glyphs
            )
        except UserError as exc:
            raise ApiError(502, str(exc)) from None
        log.info(
            "reader=%s collection=%s glyphs=%s", document.id, result.collection_id, len(glyphs)
        )
        self.storage.save_vocabulary_states(glyphs, "learning")
        # Enrichment is secondary: it never turns a stored glyph into a failed upload.
        outcomes = await service.write_notes(self.stories(document, chosen), document.id)
        return json_response(
            200,
            {
                "collection_id": result.collection_id,
                "name": result.name,
                "uploaded": result.requested,
                "total": result.total,
                "vocabulary_states": dict.fromkeys(glyphs, "learning"),
                "notes": [
                    {"glyph": o.glyph, "action": o.action, "error": o.error or ""} for o in outcomes
                ],
            },
        )

    def stories(self, document: ReaderDocument, chosen) -> list[tuple[str, str]]:
        """Notes use the same meaning the popup showed, so a card never says less than the Reader."""
        lexemes = enrich(
            [glyph for glyph, _ in chosen],
            document.glyph_meanings(),
            document.glyph_pronunciations(),
            self.dictionary,
            self.russian,
        )
        translations = document.sentence_translations()
        notes = []
        for glyph, sentence in chosen:
            lexeme = lexemes[glyph]
            meaning = lexeme.native() or lexeme.english
            story = build_hanly_note(
                "\n".join(meaning) or None,
                sentence.text,
                translations.get(sentence.text.strip()),
            )
            if story:
                notes.append((glyph, story))
        return notes

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
