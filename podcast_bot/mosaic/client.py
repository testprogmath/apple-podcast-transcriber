"""Only the three documented API endpoints; no remote response bodies in errors."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx

from ..models import UserError
from .config import MosaicConfig

REFRESH_URL = "https://mandarinmosaic.azurewebsites.net/api/auth/refresh"
PACK_URL = "https://mandarinvocabularybuilder.azurewebsites.net/api/addpacks"
SENTENCE_URL = "https://mandarinvocabularybuilder.azurewebsites.net/api/sentences"


class MosaicError(UserError):
    pass


@dataclass(frozen=True)
class BatchResult:
    successful: frozenset[str]
    rejected: frozenset[str]
    unconfirmed: frozenset[str]


def response_ids(value: object) -> set[str]:
    if not isinstance(value, list):
        raise MosaicError(
            "Mandarin Mosaic returned an invalid update response. Use /mosaic to retry."
        )
    result = set()
    for item in value:
        try:
            result.add(str(UUID(item["UniqueIdentifier"])))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise MosaicError(
                "Mandarin Mosaic returned an invalid update response. Use /mosaic to retry."
            ) from None
    return result


def validate_updates(data: dict, submitted: set[str]) -> BatchResult:
    successful = response_ids(data.get("SuccessfulUpdates"))
    rejected = response_ids(data.get("UnsuccessfulUpdates")) & submitted
    # A rejection wins if the server lists an ID in both arrays.
    successful = (successful & submitted) - rejected
    return BatchResult(
        frozenset(successful), frozenset(rejected), frozenset(submitted - successful - rejected)
    )


class MandarinMosaicClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        config: MosaicConfig,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.http = http
        self._config = config
        self._rotation_pending = False
        self._refresh_token = config.refresh_token
        self._refresh_token_id = config.refresh_token_id
        self._jwt: str | None = None
        self._expiry = datetime.min.replace(tzinfo=UTC)
        self._generation = 0
        self._refresh_lock = asyncio.Lock()
        self._clock = clock

    async def _post(self, url: str, payload: dict, jwt: str | None = None) -> httpx.Response:
        try:
            return await self.http.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {jwt}"} if jwt else {},
                follow_redirects=False,
                timeout=30,
            )
        except httpx.HTTPError:
            raise MosaicError(
                "Mandarin Mosaic connection failed; delivery is unconfirmed. Use /mosaic to retry with the same IDs."
            ) from None

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        if not 200 <= response.status_code < 300:
            raise MosaicError(
                "Mandarin Mosaic request failed. Use /mosaic to retry with the same IDs."
            )
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError
            return data
        except ValueError:
            raise MosaicError(
                "Mandarin Mosaic returned an invalid response. Use /mosaic to retry."
            ) from None

    async def ensure_jwt(self) -> str:
        await self.refresh_session()
        return self._jwt

    async def refresh_session(
        self, force: bool = False, observed_generation: int | None = None
    ) -> None:
        async with self._refresh_lock:
            if self._rotation_pending:
                self._save_rotation()
            if (
                force
                and observed_generation is not None
                and observed_generation != self._generation
            ):
                return  # Another request already replaced the rejected session.
            if not force and self._jwt and self._clock() + timedelta(seconds=30) < self._expiry:
                return
            response = await self._post(
                REFRESH_URL,
                {"RefreshTokenId": self._refresh_token_id, "RefreshToken": self._refresh_token},
            )
            if response.status_code == 401:
                raise MosaicError(
                    "Mandarin Mosaic stored refresh credentials are no longer valid. Update server configuration."
                )
            data = self._json(response)
            try:
                expiry = datetime.fromisoformat(data["JwtExpiry"].replace("Z", "+00:00"))
                jwt, token, identifier = data["Jwt"], data["RefreshToken"], data["RefreshTokenId"]
                if (
                    data.get("Success") is not True
                    or not isinstance(jwt, str)
                    or not jwt
                    or not isinstance(token, str)
                    or not token
                    or type(identifier) is not int
                    or identifier < 0
                    or expiry.tzinfo is None
                    or expiry <= self._clock()
                ):
                    raise ValueError
            except (KeyError, ValueError, TypeError, AttributeError):
                raise MosaicError(
                    "Mandarin Mosaic refresh failed or returned an invalid session. Update server configuration."
                ) from None
            self._jwt, self._expiry = jwt, expiry
            self._refresh_token, self._refresh_token_id = token, identifier
            self._generation += 1
            self._rotation_pending = True
            self._save_rotation()

    def _save_rotation(self) -> None:
        try:
            self._config.save_rotation(self._refresh_token, self._refresh_token_id)
        except UserError as exc:
            raise MosaicError(str(exc)) from None
        self._rotation_pending = False

    async def _authenticated(self, url: str, payload: dict) -> dict:
        jwt = await self.ensure_jwt()
        generation = self._generation
        response = await self._post(url, payload, jwt)
        if response.status_code == 401:
            await self.refresh_session(force=True, observed_generation=generation)
            response = await self._post(url, payload, self._jwt)
            if response.status_code == 401:
                raise MosaicError(
                    "Mandarin Mosaic authentication failed after refreshing. Update server configuration."
                )
        return self._json(response)

    async def create_pack(self, pack: dict) -> None:
        result = validate_updates(
            await self._authenticated(PACK_URL, {"Packs": [pack]}), {pack["UniqueIdentifier"]}
        )
        if pack["UniqueIdentifier"] not in result.successful:
            raise MosaicError(
                "Mandarin Mosaic did not confirm pack creation. No sentences uploaded. Use /mosaic to retry."
            )

    async def upload_sentences(self, sentences: list[dict]) -> BatchResult:
        return validate_updates(
            await self._authenticated(SENTENCE_URL, {"Sentences": sentences}),
            {s["UniqueIdentifier"] for s in sentences},
        )
