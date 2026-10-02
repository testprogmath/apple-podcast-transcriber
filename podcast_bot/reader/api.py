"""Reader HTTP API. Pure request dispatch: no sockets, no credentials in any response."""

import base64
import binascii
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
from .documents import MAX_FILE_BYTES, ReaderDocument, clean_title, from_file, from_text
from .enrich import enrich
from .media import asset_path, audio_response, grant_cookie, valid_grant
from .tokens import HAN
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
    "pasted_text": "Sentences selected in the Reader.",
    "file": "Sentences selected in the Reader.",
}
MAX_GLYPHS = 200
MAX_GLYPH_LENGTH = 40
MAX_SENTENCES = 200
SPAN_FIELDS = {"sentence_id", "start", "end", "text"}
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


def describe(token) -> dict:
    result = {"t": token.text, "w": token.word, "start": token.start, "end": token.end}
    if token.override:
        result["override"] = token.override
    return result


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
        if parts == ["api", "reader", "documents"]:
            owner = self.authenticate(headers)
            if method == "GET":
                return json_response(
                    200, {"documents": self.storage.reader_library(owner), "limit": 100}
                )
            if method == "POST":
                data = self.payload(body)
                source = data.get("source_type")
                if source == "pasted_text":
                    if (
                        set(data) - {"source_type", "text", "title"}
                        or not isinstance(data.get("text"), str)
                        or not isinstance(data.get("title", ""), str)
                    ):
                        raise ApiError(400, "Send text and an optional title.")
                    document = from_text(owner, data["text"], data.get("title"))
                elif source == "file":
                    if (
                        set(data) != {"source_type", "filename", "content"}
                        or not isinstance(data["filename"], str)
                        or not isinstance(data["content"], str)
                    ):
                        raise ApiError(400, "Send a filename and base64 UTF-8 file content.")
                    if len(data["content"]) > ((MAX_FILE_BYTES + 2) // 3) * 4:
                        raise ApiError(413, "Reader files must be at most 512 KiB.")
                    try:
                        raw = base64.b64decode(data["content"], validate=True)
                    except (ValueError, binascii.Error):
                        raise ApiError(400, "Invalid file content.") from None
                    if len(raw) > MAX_FILE_BYTES:
                        raise ApiError(413, "Reader files must be at most 512 KiB.")
                    document = from_file(owner, raw, data["filename"])
                else:
                    raise ApiError(400, "Choose pasted_text or file.")
                self.storage.save_reader_document(document)
                return json_response(200, {"id": document.id})
            raise ApiError(405, "Use GET or POST for the library.")
        if len(parts) == 4 and parts[:3] == ["api", "reader", "documents"]:
            document = self.authorize(headers, parts[3])
            if method != "PATCH":
                raise ApiError(405, "Use PATCH to rename a document.")
            data = self.payload(body)
            if set(data) != {"title"} or not isinstance(data["title"], str):
                raise ApiError(400, "Send only a title.")
            title = clean_title(data["title"])
            if not title:
                raise ApiError(400, "Enter a non-empty title.")
            self.storage.rename_reader_document(document.id, document.chat_id, title)
            return json_response(200, {"id": document.id, "title": title})
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
        if parts[:2] == ["api", "reader"] and len(parts) == 5 and parts[3] == "lexical-overrides":
            document = self.authorize(headers, parts[2])
            if method != "DELETE":
                raise ApiError(405, "Use DELETE to remove a custom expression.")
            if not re.fullmatch(r"[1-9][0-9]{0,17}", parts[4]):
                raise ApiError(404, "Unknown custom expression.")
            return self.remove_override(document, int(parts[4]))
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
            if method == "POST" and parts[3:] == ["lexical-overrides"]:
                return self.add_override(document, self.payload(body))
            if method == "POST" and parts[3:] == ["selection"]:
                return self.selection(document, self.payload(body))
            raise ApiError(405, "Unsupported Reader request.")
        raise ApiError(404, "Not found.")

    def static(self, name: str):
        target = STATIC / name
        if name not in {p.name for p in STATIC.iterdir()} or target.suffix not in TYPES:
            raise ApiError(404, "Not found.")
        return 200, {"Content-Type": TYPES[target.suffix], **SECURITY_HEADERS}, target.read_bytes()

    def authenticate(self, headers: dict) -> int:
        init_data = headers.get("x-telegram-init-data", "")
        user = telegram_user_id(init_data, self.config.token)
        if user is None and self.dev_mode and not init_data:
            user = self.config.allowed_user_id
        if user != self.config.allowed_user_id:
            raise ApiError(401, "Open this Reader from your Telegram bot.")
        return user

    def authorize(self, headers: dict, identifier: str) -> ReaderDocument:
        user = self.authenticate(headers)
        if not IDENTIFIER.fullmatch(identifier):
            raise ApiError(404, "Unknown Reader document.")
        document = self.storage.reader_document(identifier)
        if document is None or document.chat_id != user:
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
        self.storage.touch_reader_document(document.id, document.chat_id)
        sentences = document.sentences()
        audio, ranges, audio_status = document_audio(document, sentences, self.storage.root)
        local_asset = audio.pop("asset_id", None) if audio else None
        tokens = self.tokens(document, sentences)
        glossary = self.glossary(
            document, (token.text for items in tokens for token in items if token.word)
        )
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

    def tokens(self, document: ReaderDocument, sentences):
        overrides = self.storage.reader_lexical_overrides(document.id)
        return document.tokens(sentences, self.dictionary, overrides)

    def glossary(self, document: ReaderDocument, glyphs) -> dict:
        lexemes = enrich(
            glyphs,
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
            entry = {
                "meaning_source": lexeme.meaning_source,
                "p": lexeme.pinyin,
                "vocabulary_state": states.get(glyph, "unknown"),
            }
            if lexeme.native():
                entry["ru"] = list(lexeme.native())
                entry["rs"] = lexeme.native_source
            if lexeme.english:
                entry["en"] = list(lexeme.english)
                entry["es"] = lexeme.english_source
            glossary[glyph] = entry
        return glossary

    def span(self, document: ReaderDocument, data: dict, minimum: int = 1):
        """A browser selection as an exact slice of one canonical sentence, or a refusal.

        Offsets count Unicode code points, the convention of every Reader token span.
        """
        if set(data) != SPAN_FIELDS or not all(
            type(data[key]) is int for key in ("sentence_id", "start", "end")
        ):
            raise ApiError(400, "Invalid selection.")
        if not isinstance(data["text"], str):
            raise ApiError(400, "Invalid selection.")
        sentence = next((s for s in document.sentences() if s.id == data["sentence_id"]), None)
        start, end, text = data["start"], data["end"], data["text"]
        if sentence is None or not 0 <= start < end <= len(sentence.text):
            raise ApiError(409, "This document has changed. Reopen Reader.")
        if sentence.text[start:end] != text:
            raise ApiError(409, "This document has changed. Reopen Reader.")
        if not all(HAN.fullmatch(character) for character in text):
            raise ApiError(400, "Select Chinese text within one sentence.")
        if len(text) < minimum:
            raise ApiError(400, "Select at least two characters to make an expression.")
        if len(text) > MAX_GLYPH_LENGTH:
            raise ApiError(400, f"Select at most {MAX_GLYPH_LENGTH} characters.")
        return sentence, start, end, text

    def sentence_update(self, document: ReaderDocument, sentence_id: int, override=None):
        sentences = document.sentences()
        sentence = next(s for s in sentences if s.id == sentence_id)
        overrides = self.storage.reader_lexical_overrides(document.id)
        tokens = document.tokens([sentence], self.dictionary, overrides)[0]
        payload = {
            "sentence": {"id": sentence.id, "tokens": [describe(t) for t in tokens]},
            "glossary": self.glossary(document, (t.text for t in tokens if t.word)),
        }
        if override is not None:
            payload["override"] = override
        return json_response(200, payload)

    def add_override(self, document: ReaderDocument, data: dict):
        sentence, start, end, text = self.span(document, data, minimum=2)
        existing = self.storage.reader_lexical_overrides(document.id).get(sentence.id, [])
        active = [o for o in existing if sentence.text[o.start : o.end] == o.text]
        same = next((o for o in active if (o.start, o.end) == (start, end)), None)
        if same is None:
            if any(o.start < end and start < o.end for o in active):
                raise ApiError(409, "That overlaps an existing custom expression.")
            stale = [o.id for o in existing if o not in active and o.start < end and start < o.end]
            identifier = self.storage.save_reader_lexical_override(
                document.id, sentence.id, start, end, text, stale
            )
        else:
            identifier = same.id
        override = {"id": identifier, "sentence_id": sentence.id, "start": start, "end": end}
        return self.sentence_update(document, sentence.id, override)

    def remove_override(self, document: ReaderDocument, identifier: int):
        sentence_id = self.storage.delete_reader_lexical_override(document.id, identifier)
        if sentence_id is None:
            raise ApiError(404, "Unknown custom expression.")
        if not any(s.id == sentence_id for s in document.sentences()):
            return json_response(200, {})
        return self.sentence_update(document, sentence_id)

    def selection(self, document: ReaderDocument, data: dict):
        """Reading aids for a selected span, without persisting anything."""
        sentence, start, end, text = self.span(document, data)
        return json_response(
            200,
            {
                "glyph": text,
                "sentence_id": sentence.id,
                "start": start,
                "end": end,
                "entry": self.glossary(document, [text])[text],
            },
        )

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
        tokens = self.tokens(document, document.sentences())
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
        words: dict[int, set[str]] = {}
        chosen, seen = [], set()
        for value in values:
            if isinstance(value, dict) and set(value) == {"glyph", "sentence_id", "start", "end"}:
                # A selected span: exactly the canonical source slice, never browser text.
                try:
                    _, _, _, glyph = self.span(
                        document,
                        {
                            "sentence_id": value["sentence_id"],
                            "start": value["start"],
                            "end": value["end"],
                            "text": value["glyph"],
                        },
                    )
                except ApiError:
                    raise ApiError(
                        400, "A selected expression is not part of the selected sentence."
                    ) from None
                if glyph not in seen:
                    seen.add(glyph)
                    chosen.append((glyph, sentences[value["sentence_id"]]))
                continue
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
                    token.text for token in self.tokens(document, [sentence])[0] if token.word
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
