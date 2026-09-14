import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from podcast_bot.bot import BotHandlers
from podcast_bot.mosaic.client import (
    PACK_URL,
    REFRESH_URL,
    SENTENCE_URL,
    MandarinMosaicClient,
    MosaicError,
)
from podcast_bot.mosaic.config import MosaicConfig
from podcast_bot.mosaic.segmentation import segment_mandarin
from podcast_bot.mosaic.service import MosaicUploadService
from podcast_bot.storage import Storage

NOW = datetime(2026, 9, 14, tzinfo=UTC)
SECRET = "secret-refresh-do-not-log"
SECRET_ID = 987654321
JWT = "secret-jwt-do-not-log"
CHINESE = ["咱们分工合作吧。", "今天我们一起学习中文。"]


def refresh(jwt=JWT, expiry=None):
    return httpx.Response(
        200,
        json={
            "Success": True,
            "Jwt": jwt,
            "JwtExpiry": (expiry or NOW + timedelta(hours=1)).isoformat(),
            "RefreshToken": "rotated-" + SECRET,
            "RefreshTokenId": SECRET_ID + 1,
            "RefreshTokenExpiry": (NOW + timedelta(days=30)).isoformat(),
        },
    )


def updates(success=(), rejected=()):
    return httpx.Response(
        200,
        json={
            "SuccessfulUpdates": [{"UniqueIdentifier": i} for i in success],
            "UnsuccessfulUpdates": [{"UniqueIdentifier": i} for i in rejected],
        },
    )


def client(handler, clock=lambda: NOW):
    return MandarinMosaicClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        MosaicConfig(SECRET, SECRET_ID),
        clock,
    )


@pytest.fixture
def pack(tmp_path):
    p = tmp_path / "study"
    p.mkdir()
    transcript = "\n".join(CHINESE)
    (p / "transcript.txt").write_text(transcript)
    metadata = {
        "podcast_id": "1490732024",
        "episode_id": "123",
        "podcast": "Da Peng",
        "title": "“我们”和“咱们”",
        "study_settings": {"target_language": "zh"},
        "transcript_sha256": hashlib.sha256(transcript.encode()).hexdigest(),
    }
    (p / "metadata.json").write_text(json.dumps(metadata))
    study = {
        k: []
        for k in (
            "passages",
            "vocabulary",
            "patterns",
            "pragmatics",
            "cultural_references",
            "possible_asr_errors",
        )
    }
    study["mosaic_sentences"] = [
        {
            "chinese": s,
            "english": f"English translation {i}",
            "reason": "Authentic sentence",
            "source_start": None,
            "source_end": None,
        }
        for i, s in enumerate(CHINESE)
    ]
    (p / "study.json").write_text(json.dumps(study))
    for name in ("reader.md", "study.md", "hanly.csv"):
        (p / name).write_text("fixture")
    manifest(p)
    return p


def manifest(p):
    (p / "manifest.json").write_text(
        json.dumps(
            {
                f.name: hashlib.sha256(f.read_bytes()).hexdigest()
                for f in p.iterdir()
                if f.name != "manifest.json"
            }
        )
    )


class API:
    def __init__(self):
        self.calls = []
        self.pack_failure = None
        self.sentence_failure = None
        self.reject_first = False

    def __call__(self, request):
        body = json.loads(request.content)
        self.calls.append((str(request.url), body))
        if str(request.url) == REFRESH_URL:
            return refresh()
        assert request.headers["Authorization"] == "Bearer " + JWT
        if str(request.url) == PACK_URL:
            if self.pack_failure == "network":
                raise httpx.ReadError(SECRET, request=request)
            identifier = body["Packs"][0]["UniqueIdentifier"]
            if self.pack_failure == "reject":
                return updates(rejected=[identifier])
            if self.pack_failure == "missing":
                return updates()
            if self.pack_failure == "conflict":
                return updates([identifier], [identifier])
            return updates([identifier])
        assert str(request.url) == SENTENCE_URL
        if self.sentence_failure == "network":
            raise httpx.ReadTimeout(JWT, request=request)
        ids = [s["UniqueIdentifier"] for s in body["Sentences"]]
        if self.sentence_failure == "missing":
            return updates(ids[1:])
        return updates(ids[1:], ids[:1]) if self.reject_first else updates(ids)


def calls(api, url):
    return [body for endpoint, body in api.calls if endpoint == url]


async def test_successful_refresh_and_memory_cache():
    api = API()
    c = client(api)
    assert await c.ensure_jwt() == JWT
    assert await c.ensure_jwt() == JWT
    assert calls(api, REFRESH_URL) == [{"RefreshTokenId": SECRET_ID, "RefreshToken": SECRET}]


async def test_expiry_refresh_uses_rotated_credentials():
    now = [NOW]
    api = API()
    c = client(api, lambda: now[0])
    await c.ensure_jwt()
    now[0] += timedelta(minutes=59, seconds=31)
    await c.ensure_jwt()
    assert len(calls(api, REFRESH_URL)) == 2
    assert calls(api, REFRESH_URL)[1] == {
        "RefreshTokenId": SECRET_ID + 1,
        "RefreshToken": "rotated-" + SECRET,
    }


async def test_concurrent_calls_refresh_once():
    count = 0

    async def handle(request):
        nonlocal count
        count += 1
        await asyncio.sleep(0)
        return refresh()

    c = client(handle)
    assert await asyncio.gather(*(c.ensure_jwt() for _ in range(12))) == [JWT] * 12
    assert count == 1


@pytest.mark.parametrize("still_unauthorized", [False, True])
async def test_401_refresh_retry_exactly_once(still_unauthorized):
    requests = []
    identifier = str(uuid4())

    def handle(request):
        requests.append(request)
        if str(request.url) == REFRESH_URL:
            return refresh()
        n = sum(str(r.url) == PACK_URL for r in requests)
        return (
            httpx.Response(401, text=SECRET)
            if n == 1 or still_unauthorized
            else updates([identifier])
        )

    c = client(handle)
    if still_unauthorized:
        with pytest.raises(MosaicError, match="authentication failed after refreshing"):
            await c.create_pack({"UniqueIdentifier": identifier})
    else:
        await c.create_pack({"UniqueIdentifier": identifier})
    assert sum(str(r.url) == REFRESH_URL for r in requests) == 2
    assert sum(str(r.url) == PACK_URL for r in requests) == 2
    assert requests[1].content == requests[3].content


async def test_concurrent_401_reuses_refresh_even_if_jwt_string_unchanged():
    identifier = str(uuid4())
    refreshes = 0
    original = 0
    barrier = asyncio.Event()

    async def handle(request):
        nonlocal refreshes, original
        if str(request.url) == REFRESH_URL:
            refreshes += 1
            return refresh()
        if refreshes == 1:
            original += 1
            if original == 2:
                barrier.set()
            await barrier.wait()
            return httpx.Response(401)
        return updates([identifier])

    c = client(handle)
    await asyncio.gather(*(c.create_pack({"UniqueIdentifier": identifier}) for _ in range(2)))
    assert refreshes == 2


async def test_rejected_refresh_safe_error():
    c = client(lambda r: httpx.Response(401, text=SECRET + JWT))
    with pytest.raises(
        MosaicError, match="stored refresh credentials are no longer valid"
    ) as error:
        await c.ensure_jwt()
    assert SECRET not in str(error.value)


async def test_pack_and_sentences_success_payloads(store, pack):
    api = API()
    service = MosaicUploadService(store, client(api))
    result = await service.create_study_pack(pack)
    assert result.uploaded == result.total == 2
    assert result.rejected == result.unconfirmed == 0
    payload = calls(api, PACK_URL)[0]["Packs"][0]
    assert payload["MakePublic"] is None and payload["Deleted"] is False
    assert payload["UniqueIdentifier"] == result.pack_id
    assert payload["Created"] == payload["LastPropertiesChange"]
    assert payload["ImportedFrom"] == "apple:1490732024:123"
    sentences = calls(api, SENTENCE_URL)[0]["Sentences"]
    assert len({s["UniqueIdentifier"] for s in sentences}) == 2
    for s, chinese in zip(sentences, CHINESE, strict=True):
        assert s["PackId"] == result.pack_id
        assert s["Mandarin"] == chinese
        assert "".join(s["SegmentedMandarin"].split()) == chinese
        assert " " in s["SegmentedMandarin"]
        assert s["EnglishReviewed"] is False
        assert s["LastUpdated"] is None and s["ClozePosition"] is None
    assert "✓ 2/2 sentences uploaded" in result.message()
    await service.create_study_pack(pack)
    assert len(api.calls) == 3  # Completed uploads require no network.


@pytest.mark.parametrize("failure", ["reject", "missing", "conflict"])
async def test_pack_not_confirmed_never_uploads_sentences(store, pack, failure):
    api = API()
    api.pack_failure = failure
    with pytest.raises(MosaicError, match="did not confirm pack creation"):
        await MosaicUploadService(store, client(api)).create_study_pack(pack)
    assert not calls(api, SENTENCE_URL)


async def test_partial_retry_only_rejected_with_same_uuid(store, pack):
    api = API()
    api.reject_first = True
    service = MosaicUploadService(store, client(api))
    result = await service.create_study_pack(pack)
    assert result.uploaded == result.rejected == 1
    assert "1 sentences were rejected" in result.message()
    old = calls(api, SENTENCE_URL)[0]["Sentences"][0]
    api.reject_first = False
    result = await service.create_study_pack(pack)
    assert result.uploaded == 2
    assert calls(api, SENTENCE_URL)[1]["Sentences"] == [old]
    assert len(calls(api, PACK_URL)) == 1


async def test_missing_sentence_is_unconfirmed_not_rejected(store, pack):
    api = API()
    api.sentence_failure = "missing"
    result = await MosaicUploadService(store, client(api)).create_study_pack(pack)
    assert result.uploaded == result.unconfirmed == 1
    assert result.rejected == 0


@pytest.mark.parametrize("stage", ["pack", "sentence"])
async def test_network_failure_restart_reuses_persisted_ids(store, pack, stage):
    api = API()
    if stage == "pack":
        api.pack_failure = "network"
    else:
        api.sentence_failure = "network"
    service = MosaicUploadService(store, client(api))
    if stage == "pack":
        with pytest.raises(MosaicError, match="connection failed"):
            await service.create_study_pack(pack)
        assert not calls(api, SENTENCE_URL)
    else:
        result = await service.create_study_pack(pack)
        assert result.uploaded == 0 and result.unconfirmed == 2
        assert "Pack created" in result.message()
    snapshot = [tuple(r)[:3] for r in store.db.execute("SELECT * FROM mosaic_sentences")]
    assert len(snapshot) == 2
    api.pack_failure = api.sentence_failure = None
    # Fresh storage/client/service objects simulate a process restart.
    restarted = Storage(store.root)
    try:
        result = await MosaicUploadService(restarted, client(api)).create_study_pack(pack)
        assert result.uploaded == 2
        assert [
            tuple(r)[:3] for r in restarted.db.execute("SELECT * FROM mosaic_sentences")
        ] == snapshot
    finally:
        restarted.close()
    if stage == "pack":
        assert calls(api, PACK_URL)[0] == calls(api, PACK_URL)[1]
    else:
        assert len(calls(api, PACK_URL)) == 1
        assert calls(api, SENTENCE_URL)[0] == calls(api, SENTENCE_URL)[1]


async def test_secrets_not_logged_or_in_exceptions(store, pack, caplog):
    caplog.set_level(logging.DEBUG)
    api = API()
    api.pack_failure = "network"
    with pytest.raises(MosaicError) as error:
        await MosaicUploadService(store, client(api)).create_study_pack(pack)
    observed = caplog.text + str(error.value) + repr(MosaicConfig(SECRET, SECRET_ID))
    for secret in (SECRET, JWT, str(SECRET_ID)):
        assert secret not in observed
    # The persisted upload rows have no credentials or auth response payloads.
    rows = str([tuple(r) for r in store.db.execute("SELECT * FROM mosaic_packs")])
    assert SECRET not in rows and JWT not in rows


def test_segmentation_preserves_words_and_punctuation():
    result = segment_mandarin("今天我们一起学习中文。")
    assert result == "今天 我们 一起 学习 中文 。"


async def test_source_fidelity_before_network(store, pack):
    study = json.loads((pack / "study.json").read_text())
    study["mosaic_sentences"][0]["chinese"] = "这句话不在原文里。"
    (pack / "study.json").write_text(json.dumps(study))
    manifest(pack)
    api = API()
    with pytest.raises(Exception, match="source validation"):
        await MosaicUploadService(store, client(api)).create_study_pack(pack)
    assert not api.calls


async def test_mosaic_telegram_command_and_access(store, pack, config):
    store.remember_pack(42, pack, pack)
    handlers = BotHandlers(config, store)
    api = API()
    handlers.mosaic = MosaicUploadService(store, client(api))
    status = SimpleNamespace(edit_text=AsyncMock())
    message = SimpleNamespace(text="/mosaic", reply_text=AsyncMock(return_value=status))
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=99),
        effective_chat=SimpleNamespace(id=42, type="private"),
    )
    await handlers.handle(update, SimpleNamespace())
    assert not api.calls
    update.effective_user.id = 42
    await handlers.handle(update, SimpleNamespace())
    assert "2/2 sentences uploaded" in status.edit_text.call_args.args[0]
    assert store.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_optional_credentials_config(monkeypatch):
    monkeypatch.delenv("MANDARIN_MOSAIC_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("MANDARIN_MOSAIC_REFRESH_TOKEN_ID", raising=False)
    assert MosaicConfig.from_env() is None
    monkeypatch.setenv("MANDARIN_MOSAIC_REFRESH_TOKEN", SECRET)
    with pytest.raises(Exception, match="both Mandarin Mosaic"):
        MosaicConfig.from_env()
    monkeypatch.setenv("MANDARIN_MOSAIC_REFRESH_TOKEN_ID", str(SECRET_ID))
    value = MosaicConfig.from_env()
    assert value.refresh_token == SECRET and value.refresh_token_id == SECRET_ID


async def test_rotated_credentials_survive_restart_in_private_config(monkeypatch, tmp_path):
    monkeypatch.setenv("MANDARIN_MOSAIC_REFRESH_TOKEN", SECRET)
    monkeypatch.setenv("MANDARIN_MOSAIC_REFRESH_TOKEN_ID", str(SECRET_ID))
    path = tmp_path / "mosaic-session.json"
    config = MosaicConfig.from_env(path)
    c = MandarinMosaicClient(
        httpx.AsyncClient(transport=httpx.MockTransport(API())), config, lambda: NOW
    )
    await c.ensure_jwt()
    assert path.stat().st_mode & 0o777 == 0o600
    data = path.read_text()
    assert JWT not in data
    restored = MosaicConfig.from_env(path)
    assert restored.refresh_token == "rotated-" + SECRET
    assert restored.refresh_token_id == SECRET_ID + 1
    monkeypatch.setenv("MANDARIN_MOSAIC_REFRESH_TOKEN", "replacement-secret")
    assert MosaicConfig.from_env(path).refresh_token == "replacement-secret"


async def test_failed_rotation_save_retries_disk_without_refresh(monkeypatch, tmp_path):
    config = MosaicConfig(SECRET, SECRET_ID, tmp_path / "mosaic-session.json", "seed")
    original = MosaicConfig.save_rotation

    def fail(*args):
        from podcast_bot.models import UserError

        raise UserError("Could not save Mandarin Mosaic rotated credentials.")

    monkeypatch.setattr(MosaicConfig, "save_rotation", fail)
    api = API()
    c = MandarinMosaicClient(
        httpx.AsyncClient(transport=httpx.MockTransport(api)), config, lambda: NOW
    )
    with pytest.raises(MosaicError, match="Could not save"):
        await c.ensure_jwt()
    monkeypatch.setattr(MosaicConfig, "save_rotation", original)
    assert await c.ensure_jwt() == JWT
    assert len(calls(api, REFRESH_URL)) == 1
    assert config.session_file.exists()


@pytest.mark.parametrize(
    "body", [None, {}, {"Success": False}, {"Success": True, "JwtExpiry": "bad"}]
)
async def test_malformed_refresh_never_leaks_response(body):
    c = client(lambda r: httpx.Response(200, json=body))
    with pytest.raises(MosaicError):
        await c.ensure_jwt()


async def test_malformed_sentence_response_preserves_retriable_state(store, pack):
    api = API()

    def handle(request):
        if str(request.url) == SENTENCE_URL:
            return httpx.Response(
                200,
                json={
                    "SuccessfulUpdates": [{"UniqueIdentifier": SECRET}],
                    "UnsuccessfulUpdates": [],
                },
            )
        return api(request)

    result = await MosaicUploadService(store, client(handle)).create_study_pack(pack)
    assert result.unconfirmed == 2 and result.uploaded == 0
    assert SECRET not in result.message()


@pytest.mark.parametrize("change", ["empty", "non-Chinese", "corrupt"])
async def test_unusable_study_pack_does_not_call_api(store, pack, change):
    if change == "empty":
        p = pack / "study.json"
        data = json.loads(p.read_text())
        data["mosaic_sentences"] = []
        p.write_text(json.dumps(data))
    elif change == "non-Chinese":
        p = pack / "metadata.json"
        data = json.loads(p.read_text())
        data["study_settings"]["target_language"] = "de"
        p.write_text(json.dumps(data))
    else:
        (pack / "transcript.txt").write_text("corrupt")
    if change != "corrupt":
        manifest(pack)
    api = API()
    from podcast_bot.models import UserError

    with pytest.raises(UserError):
        await MosaicUploadService(store, client(api)).create_study_pack(pack)
    assert not api.calls


async def test_regenerated_episode_keeps_original_upload_snapshot(store, pack):
    api = API()
    api.sentence_failure = "network"
    service = MosaicUploadService(store, client(api))
    original = await service.create_study_pack(pack)
    p = pack / "study.json"
    data = json.loads(p.read_text())
    data["mosaic_sentences"][0]["english"] = "New English translation after regeneration"
    p.write_text(json.dumps(data))
    manifest(pack)
    api.sentence_failure = None
    result = await service.create_study_pack(pack)
    assert result.pack_id == original.pack_id
    assert calls(api, SENTENCE_URL)[0] == calls(api, SENTENCE_URL)[1]


async def test_cancelled_sentence_upload_resumes_with_same_ids(store, pack):
    api = API()

    def handle(request):
        if str(request.url) == SENTENCE_URL:
            api(request)
            raise asyncio.CancelledError
        return api(request)

    with pytest.raises(asyncio.CancelledError):
        await MosaicUploadService(store, client(handle)).create_study_pack(pack)
    assert (
        store.db.execute(
            "SELECT count(*) FROM mosaic_sentences WHERE status='unconfirmed'"
        ).fetchone()[0]
        == 2
    )
    result = await MosaicUploadService(store, client(api)).create_study_pack(pack)
    assert result.uploaded == 2
    assert len(calls(api, PACK_URL)) == 1
    assert calls(api, SENTENCE_URL)[0] == calls(api, SENTENCE_URL)[1]
