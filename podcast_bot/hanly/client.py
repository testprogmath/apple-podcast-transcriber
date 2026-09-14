import asyncio
import copy
import json
import logging
import math
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

import httpx

from .auth import HanlyAuth, HanlyError, RefreshError

log = logging.getLogger(__name__)
_sensitive_http = ContextVar("hanly_http", default=False)


class _TransportFilter(logging.Filter):
    def filter(self, record):
        return not _sensitive_http.get()


_transport_filter = _TransportFilter()


class SchemaError(HanlyError):
    pass


class FirestoreAuthError(HanlyError):
    pass


class FirestorePermissionError(HanlyError):
    pass


class DocumentMissingError(HanlyError):
    pass


class ConflictError(HanlyError):
    pass


class VerificationError(HanlyError):
    pass


@dataclass
class CollectionsDocument:
    collections: dict
    timestamp: int
    update_time: str


@dataclass(frozen=True)
class GlyphNote:
    story: str
    update_time: str


DATABASE = "projects/hanzo-282fc/databases/(default)"
DOCUMENTS = f"https://firestore.googleapis.com/v1/{DATABASE}/documents"
COMMIT_URL = DOCUMENTS + ":commit"


def valid_document_id(value: str) -> bool:
    return (
        bool(value)
        and "/" not in value
        and value not in {".", ".."}
        and not re.fullmatch(r"__.*__", value)
        and len(value.encode("utf-8")) <= 1500
    )


def conflicted(response) -> bool:
    if response.status_code in (409, 412):
        return True
    # Firestore can express FAILED_PRECONDITION as HTTP 400.
    if response.status_code == 400:
        try:
            return response.json().get("error", {}).get("status") == "FAILED_PRECONDITION"
        except (ValueError, AttributeError):
            return False
    return False


def parse_note(data: object) -> GlyphNote:
    try:
        update_time = data["updateTime"]
        story = data.get("fields", {}).get("story", {}).get("stringValue", "")
        if (
            not isinstance(story, str)
            or not isinstance(update_time, str)
            or datetime.fromisoformat(update_time.replace("Z", "+00:00")).tzinfo is None
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise SchemaError(
            "Hanly personalized note has an unexpected schema; nothing was overwritten."
        ) from None
    return GlyphNote(story, update_time)


def strict_json(value: str):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError
            result[key] = item
        return result

    def invalid(value):
        raise ValueError

    def finite(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError
        return result

    return json.loads(value, object_pairs_hook=unique, parse_constant=invalid, parse_float=finite)


def parse_document(data: object) -> CollectionsDocument:
    try:
        fields = data["fields"]
        encoded = fields["all_user_collections"]["stringValue"]
        timestamp = fields["all_user_collections_timestamp"]["integerValue"]
        update_time = data["updateTime"]
        if (
            not isinstance(encoded, str)
            or not isinstance(timestamp, str)
            or not re.fullmatch(r"[0-9]+", timestamp)
            or int(timestamp) >= 2**63 - 1
            or not isinstance(update_time, str)
            or datetime.fromisoformat(update_time.replace("Z", "+00:00")).tzinfo is None
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise SchemaError(
            "Hanly Firestore document has an unexpected schema; nothing was overwritten."
        ) from None
    try:
        collections = strict_json(encoded)
    except ValueError:
        raise SchemaError(
            "Hanly all_user_collections contains malformed JSON; nothing was overwritten."
        ) from None
    if not isinstance(collections, dict):
        raise SchemaError("Hanly collections must be a JSON object; nothing was overwritten.")
    for identifier, collection in collections.items():
        if (
            not isinstance(collection, dict)
            or collection.get("id") != identifier
            or not isinstance(collection.get("name"), str)
            or not isinstance(collection.get("glyphs"), list)
            or any(not isinstance(g, str) for g in collection["glyphs"])
            or type(collection.get("deleted")) is not bool
        ):
            raise SchemaError("Hanly collection has an unexpected schema; nothing was overwritten.")
    return CollectionsDocument(collections, int(timestamp), update_time)


def merge_glyphs(existing: list[str], incoming: list[str]) -> list[str]:
    # Leave existing cards (including their order/spelling) unchanged.
    result = list(existing)
    seen = {g.strip() for g in existing}
    for glyph in incoming:
        glyph = glyph.strip()
        if glyph and glyph not in seen:
            result.append(glyph)
            seen.add(glyph)
    return result


class HanlyClient:
    def __init__(self, http: httpx.AsyncClient, auth: HanlyAuth, clock=time.time):
        self.http, self.auth, self.clock = http, auth, clock
        self._id_token = None
        self._uid = None
        self._expiry = 0
        self._generation = 0
        self._pending_persist = False
        self._auth_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    async def _request(self, method, url, **kwargs):
        # Firebase puts the API key in the URL. Suppress transport diagnostics in
        # this task's context; our own logs contain operation/status/retry only.
        for name in list(logging.Logger.manager.loggerDict):
            if name == "httpx" or name.startswith("httpcore"):
                logging.getLogger(name).addFilter(_transport_filter)
        marker = _sensitive_http.set(True)
        try:
            return await self.http.request(
                method, url, follow_redirects=False, timeout=30, **kwargs
            )
        except httpx.HTTPError:
            raise HanlyError(
                "Hanly connection failed. Use /hanly to retry without retranscription."
            ) from None
        finally:
            _sensitive_http.reset(marker)

    async def refresh_auth(self, force=False, observed=None):
        async with self._auth_lock:
            if self._pending_persist:
                self.auth.persist()
                self._pending_persist = False
            if force and observed is not None and observed != self._generation:
                return
            if not force and self._id_token and self.clock() + 60 < self._expiry:
                return
            response = await self._request(
                "POST",
                "https://securetoken.googleapis.com/v1/token",
                params={"key": self.auth.api_key},
                data={"grant_type": "refresh_token", "refresh_token": self.auth.refresh_token},
            )
            log.info("operation=refresh http_status=%s", response.status_code)
            if response.status_code != 200:
                raise RefreshError(
                    "Hanly Firebase refresh failed. Replace the configured Firebase session."
                )
            try:
                data = response.json()
                token, uid = data.get("id_token"), data.get("user_id")
                if not isinstance(token, str) or not token or not isinstance(uid, str) or not uid:
                    raise RefreshError("Hanly Firebase refresh returned no valid ID token or UID.")
                seconds = data["expires_in"]
                if (
                    isinstance(seconds, bool)
                    or not re.fullmatch(r"[0-9]+", str(seconds))
                    or int(seconds) <= 0
                ):
                    raise ValueError
                rotated = data.get("refresh_token", self.auth.refresh_token)
                if not isinstance(rotated, str) or not rotated:
                    raise ValueError
                if self._uid is not None and uid != self._uid:
                    raise RefreshError(
                        "Hanly Firebase account changed unexpectedly; restart with the intended session."
                    )
            except (ValueError, TypeError, KeyError, AttributeError):
                raise RefreshError("Hanly Firebase refresh returned an invalid session.") from None
            self._id_token, self._uid = token, uid
            self._expiry = self.clock() + int(seconds)
            self._generation += 1
            if rotated != self.auth.refresh_token:
                self.auth.refresh_token = rotated
                self._pending_persist = True
                self.auth.persist()
                self._pending_persist = False

    def user_document(self, suffix: str) -> str:
        return DOCUMENTS + "/userData/" + quote(self._uid, safe="") + suffix

    async def _authenticated(self, method, url, **kwargs):
        await self.refresh_auth()
        generation = self._generation
        response = await self._request(
            method, url, headers={"Authorization": f"Bearer {self._id_token}"}, **kwargs
        )
        log.info("operation=%s http_status=%s auth_retry=0", method, response.status_code)
        if response.status_code == 401:
            await self.refresh_auth(force=True, observed=generation)
            response = await self._request(
                method, url, headers={"Authorization": f"Bearer {self._id_token}"}, **kwargs
            )
            log.info("operation=%s http_status=%s auth_retry=1", method, response.status_code)
            if response.status_code == 401:
                raise FirestoreAuthError(
                    "Hanly Firestore authentication failed after one refresh and retry."
                )
        if response.status_code == 403:
            raise FirestorePermissionError(
                "Hanly Firestore permission denied. Check the legitimate session and security rules."
            )
        return response

    async def _firestore(self, method, **kwargs):
        await self.refresh_auth()
        response = await self._authenticated(
            method, self.user_document("/collections/data"), **kwargs
        )
        if response.status_code == 404:
            raise DocumentMissingError(
                "Hanly collections document is missing. Open Hanly and initialize collections first."
            )
        return response

    async def get_collections_document(self):
        response = await self._firestore("GET")
        if response.status_code != 200:
            raise HanlyError("Hanly could not read collections. Use /hanly to retry.")
        try:
            data = response.json()
        except ValueError:
            raise SchemaError("Hanly Firestore returned malformed JSON.") from None
        return parse_document(data)

    async def upsert_episode_collection(self, identifier, name, comment, glyphs, episode_id):
        async with self._write_lock:
            for attempt in range(1, 4):
                document = await self.get_collections_document()
                collections = copy.deepcopy(document.collections)
                collection = collections.get(
                    identifier,
                    {
                        "id": identifier,
                        "name": name,
                        "comment": comment,
                        "glyphs": [],
                        "color": None,
                        "deleted": False,
                    },
                )
                collection.update(
                    name=name, glyphs=merge_glyphs(collection["glyphs"], glyphs), deleted=False
                )
                collections[identifier] = collection
                timestamp = max(int(self.clock() * 1000), document.timestamp + 1)
                log.info(
                    "episode=%s collection=%s operation=merge retry=%s",
                    episode_id,
                    identifier,
                    attempt - 1,
                )
                response = await self._firestore(
                    "PATCH",
                    params=[
                        ("updateMask.fieldPaths", "all_user_collections"),
                        ("updateMask.fieldPaths", "all_user_collections_timestamp"),
                        ("currentDocument.updateTime", document.update_time),
                    ],
                    json={
                        "fields": {
                            "all_user_collections": {
                                "stringValue": json.dumps(
                                    collections, ensure_ascii=False, separators=(",", ":")
                                )
                            },
                            "all_user_collections_timestamp": {"integerValue": str(timestamp)},
                        }
                    },
                )
                log.info(
                    "episode=%s collection=%s operation=PATCH http_status=%s retry=%s",
                    episode_id,
                    identifier,
                    response.status_code,
                    attempt - 1,
                )
                if conflicted(response):
                    continue
                if response.status_code != 200:
                    raise HanlyError(
                        "Hanly collection write failed. Use /hanly to retry with the same collection UUID."
                    )
                verified = await self.get_collections_document()
                actual = verified.collections.get(identifier)
                if (
                    not actual
                    or actual["name"] != name
                    or actual["deleted"]
                    or not set(g.strip() for g in glyphs).issubset(
                        {g.strip() for g in actual["glyphs"]}
                    )
                    or verified.timestamp < timestamp
                ):
                    raise VerificationError(
                        "Hanly verification failed after writing. Use /hanly to safely retry."
                    )
                return len(actual["glyphs"])
            raise ConflictError(
                "Hanly collections kept changing; concurrency retry limit reached. Use /hanly again."
            )

    async def read_glyph_note(self, glyph: str) -> GlyphNote | None:
        await self.refresh_auth()
        response = await self._authenticated(
            "GET", self.user_document("/personalizedStories/" + quote(glyph, safe=""))
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise HanlyError("Hanly could not read the personalized note. Retry the upload.")
        try:
            data = response.json()
        except ValueError:
            raise SchemaError("Hanly Firestore returned malformed JSON.") from None
        return parse_note(data)

    async def _commit_note(self, glyph: str, story: str, existing: GlyphNote | None):
        await self.refresh_auth()
        write = {
            "update": {
                "name": f"{DATABASE}/documents/userData/{self._uid}/personalizedStories/{glyph}",
                "fields": {"story": {"stringValue": story}},
            },
            "updateMask": {"fieldPaths": ["story"]},
            "updateTransforms": [{"fieldPath": "timestamp", "setToServerValue": "REQUEST_TIME"}],
            "currentDocument": (
                {"updateTime": existing.update_time} if existing else {"exists": False}
            ),
        }
        return await self._authenticated("POST", COMMIT_URL, json={"writes": [write]})

    async def upsert_glyph_note(self, glyph: str, story: str, previous: str | None) -> str:
        """Write one personalizedStories note, preserving anything this integration did not write.

        Returns created, updated, unchanged, or skipped-user-modified.
        """
        if not valid_document_id(glyph):
            raise SchemaError("Hanly cannot store a note for that vocabulary item.")
        async with self._write_lock:
            for attempt in range(1, 4):
                existing = await self.read_glyph_note(glyph)
                if existing is not None:
                    if existing.story == story:
                        return "unchanged"
                    if previous is None or existing.story != previous:
                        return "skipped-user-modified"
                response = await self._commit_note(glyph, story, existing)
                log.info(
                    "glyph=%s operation=note-commit http_status=%s retry=%s",
                    glyph,
                    response.status_code,
                    attempt - 1,
                )
                if conflicted(response):
                    continue
                if response.status_code != 200:
                    raise HanlyError("Hanly note write failed. Retry the upload for this item.")
                verified = await self.read_glyph_note(glyph)
                if verified is None or verified.story != story:
                    raise VerificationError(
                        "Hanly note verification failed after writing. Retry the upload."
                    )
                return "created" if existing is None else "updated"
            raise ConflictError(
                "Hanly note kept changing; concurrency retry limit reached. Retry the upload."
            )
