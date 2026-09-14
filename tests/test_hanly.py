import asyncio
import copy
import hashlib
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from test_study import SOURCE, complete_material

from podcast_bot.bot import BotHandlers
from podcast_bot.hanly.auth import AuthConfigError, HanlyAuth, HanlyError, RefreshError
from podcast_bot.hanly.client import (
    ConflictError,
    DocumentMissingError,
    FirestoreAuthError,
    FirestorePermissionError,
    HanlyClient,
    SchemaError,
    VerificationError,
    merge_glyphs,
    parse_document,
)
from podcast_bot.hanly.service import HanlyUploadService
from podcast_bot.integrations import StudyUploads
from podcast_bot.queue import Worker
from podcast_bot.storage import Storage

KEY = "private-firebase-api-key"
TOKEN = "private-firebase-refresh-token"
ID_TOKEN = "private-firebase-id-token"
UID = "fixture-user"
NOW = 1789402459


def collection(identifier, glyphs, **extra):
    return {
        "id": identifier,
        "name": "Existing",
        "comment": None,
        "glyphs": glyphs,
        "color": None,
        "deleted": False,
        **extra,
    }


def document(collections=None, timestamp=100, version=1):
    return {
        "fields": {
            "all_user_collections": {
                "stringValue": json.dumps(collections or {}, ensure_ascii=False)
            },
            "all_user_collections_timestamp": {"integerValue": str(timestamp)},
            "unrelated_firestore_field": {"mapValue": {"fields": {"keep": {"booleanValue": True}}}},
        },
        "updateTime": f"2026-09-14T10:00:{version:02d}.123456Z",
    }


@pytest.fixture
def auth(tmp_path):
    path = tmp_path / "hanly-auth.json"
    path.write_text(
        json.dumps(
            {"api_key": KEY, "refresh_token": TOKEN, "project_id": "hanzo-282fc", "extra": "keep"}
        )
    )
    path.chmod(0o644)
    return HanlyAuth.load(path)


class API:
    def __init__(self):
        self.doc = document(
            {"Favorites": collection("Favorites", ["我"], custom={"未知": [1, None]})}
        )
        self.calls = []
        self.conflicts = 0
        self.status = None
        self.fail_verify = None
        self.fail_network = False
        self.refresh_status = 200
        self.auth_body = {
            "id_token": ID_TOKEN,
            "user_id": UID,
            "refresh_token": "rotated-" + TOKEN,
            "expires_in": "3600",
        }
        self.patches = 0

    def __call__(self, request):
        self.calls.append(request)
        if request.url.host == "securetoken.googleapis.com":
            assert request.url.params["key"] == KEY
            assert parse_qs(request.content.decode())["grant_type"] == ["refresh_token"]
            return httpx.Response(self.refresh_status, json=self.auth_body)
        assert request.url.path.endswith("/userData/" + UID + "/collections/data")
        assert request.headers["Authorization"] == "Bearer " + ID_TOKEN
        if self.status:
            return httpx.Response(self.status, text=TOKEN + KEY + ID_TOKEN)
        if request.method == "GET":
            result = copy.deepcopy(self.doc)
            if self.patches and self.fail_verify:
                parsed = json.loads(result["fields"]["all_user_collections"]["stringValue"])
                for k in list(parsed):
                    if k != "Favorites":
                        if self.fail_verify == "glyph":
                            parsed[k]["glyphs"] = []
                        elif self.fail_verify == "name":
                            parsed[k]["name"] = "Wrong name"
                        elif self.fail_verify == "missing":
                            del parsed[k]
                result["fields"]["all_user_collections"]["stringValue"] = json.dumps(parsed)
                if self.fail_verify == "timestamp":
                    result["fields"]["all_user_collections_timestamp"]["integerValue"] = "1"
            return httpx.Response(200, json=result)
        assert request.method == "PATCH"
        self.patches += 1
        if self.conflicts:
            self.conflicts -= 1
            parsed = json.loads(self.doc["fields"]["all_user_collections"]["stringValue"])
            parsed["Favorites"]["glyphs"].append("并发修改")
            for k in parsed:
                if k != "Favorites":
                    parsed[k]["glyphs"].append("用户添加")
            self.doc = document(parsed, NOW * 1000 + self.patches, self.patches + 1)
            return httpx.Response(409, json={"error": {"status": "ABORTED"}})
        body = json.loads(request.content)
        self.doc["fields"].update(body["fields"])
        self.doc["updateTime"] = "2026-09-14T10:01:00.123456Z"
        if self.fail_network:
            self.fail_network = False
            raise httpx.ReadTimeout(TOKEN + KEY, request=request)
        return httpx.Response(200, json=self.doc)


def client(auth, api, clock=lambda: NOW):
    return HanlyClient(httpx.AsyncClient(transport=httpx.MockTransport(api)), auth, clock)


def firestore_calls(api):
    return [r for r in api.calls if r.url.host == "firestore.googleapis.com"]


async def test_refresh_rotation_atomic_permissions(auth, monkeypatch):
    api = API()
    c = client(auth, api)
    replacements = []
    import podcast_bot.hanly.auth as module

    original = module.os.replace

    def replace(src, dst):
        assert src != str(dst)
        assert auth.path.exists()
        replacements.append((src, dst))
        original(src, dst)

    monkeypatch.setattr(module.os, "replace", replace)
    await c.refresh_auth()
    assert replacements
    saved = json.loads(auth.path.read_text())
    assert saved["refresh_token"] == "rotated-" + TOKEN and saved["extra"] == "keep"
    assert auth.path.stat().st_mode & 0o777 == 0o600
    assert ID_TOKEN not in auth.path.read_text()
    await c.get_collections_document()
    assert sum(r.url.host == "securetoken.googleapis.com" for r in api.calls) == 1


async def test_expiry_and_concurrent_refresh(auth):
    api = API()
    now = [NOW]

    async def handler(request):
        await asyncio.sleep(0)
        return api(request)

    c = client(auth, handler, lambda: now[0])
    await asyncio.gather(*(c.refresh_auth() for _ in range(10)))
    assert len(api.calls) == 1
    now[0] += 3541
    await c.refresh_auth()
    assert len(api.calls) == 2
    assert parse_qs(api.calls[-1].content.decode())["refresh_token"] == ["rotated-" + TOKEN]


async def test_get_parses_json_string(auth):
    api = API()
    result = await client(auth, api).get_collections_document()
    assert result.collections["Favorites"]["glyphs"] == ["我"]
    assert result.timestamp == 100
    assert result.update_time == api.doc["updateTime"]


async def test_create_and_patch_contract_preserves_unrelated(auth):
    api = API()
    original = copy.deepcopy(api.doc)
    identifier = str(uuid4())
    total = await client(auth, api).upsert_episode_collection(
        identifier, "Podcast｜我们", "source", [" 嘀咕 ", "咱们", "嘀咕"], "apple:1:2"
    )
    assert total == 2
    assert [r.method for r in firestore_calls(api)] == ["GET", "PATCH", "GET"]
    patch = firestore_calls(api)[1]
    assert patch.url.params.get_list("updateMask.fieldPaths") == [
        "all_user_collections",
        "all_user_collections_timestamp",
    ]
    assert patch.url.params["currentDocument.updateTime"] == original["updateTime"]
    fields = json.loads(patch.content)["fields"]
    assert set(fields) == {"all_user_collections", "all_user_collections_timestamp"}
    encoded = fields["all_user_collections"]["stringValue"]
    assert "嘀咕" in encoded and "\\u" not in encoded
    parsed = json.loads(encoded)
    assert parsed[identifier] == collection(
        identifier, ["嘀咕", "咱们"], name="Podcast｜我们", comment="source"
    )
    assert parsed["Favorites"] == parse_document(original).collections["Favorites"]
    assert (
        api.doc["fields"]["unrelated_firestore_field"]
        == original["fields"]["unrelated_firestore_field"]
    )
    assert int(fields["all_user_collections_timestamp"]["integerValue"]) == NOW * 1000


async def test_existing_glyphs_order_and_strictly_newer_timestamp(auth):
    api = API()
    identifier = str(uuid4())
    api.doc = document(
        {identifier: collection(identifier, ["  已有 ", "咱们"], color="blue", custom=True)},
        NOW * 1000 + 500,
    )
    await client(auth, api).upsert_episode_collection(
        identifier, "Name", "comment", ["已有", " 嘀咕 ", "咱们", "熟络"], "apple:1:2"
    )
    parsed = parse_document(api.doc)
    assert parsed.collections[identifier]["glyphs"] == ["  已有 ", "咱们", "嘀咕", "熟络"]
    assert parsed.collections[identifier]["color"] == "blue"
    assert parsed.collections[identifier]["custom"] is True
    assert parsed.timestamp == NOW * 1000 + 501


def test_deduplication_no_aggressive_normalization():
    assert merge_glyphs(["我", "  熟络 "], [" 熟络", "咱们", "咱们", "", "我们", "我們"]) == [
        "我",
        "  熟络 ",
        "咱们",
        "我们",
        "我們",
    ]


async def test_conflict_refetches_and_remerges(auth):
    api = API()
    identifier = str(uuid4())
    parsed = parse_document(api.doc).collections
    parsed[identifier] = collection(identifier, ["已有"])
    api.doc = document(parsed)
    api.conflicts = 1
    await client(auth, api).upsert_episode_collection(
        identifier, "Name", None, ["嘀咕"], "apple:1:2"
    )
    assert [r.method for r in firestore_calls(api)] == ["GET", "PATCH", "GET", "PATCH", "GET"]
    final = parse_document(api.doc)
    assert final.collections["Favorites"]["glyphs"] == ["我", "并发修改"]
    assert final.collections[identifier]["glyphs"] == ["已有", "用户添加", "嘀咕"]
    patches = [r for r in firestore_calls(api) if r.method == "PATCH"]
    assert (
        patches[0].url.params["currentDocument.updateTime"]
        != patches[1].url.params["currentDocument.updateTime"]
    )


async def test_bounded_conflicts(auth):
    api = API()
    api.conflicts = 10
    with pytest.raises(ConflictError):
        await client(auth, api).upsert_episode_collection(
            str(uuid4()), "Name", None, ["嘀咕"], "apple:1:2"
        )
    assert api.patches == 3


@pytest.mark.parametrize("failure", ["glyph", "name", "missing", "timestamp"])
async def test_verification_failure(auth, failure):
    api = API()
    api.fail_verify = failure
    with pytest.raises(VerificationError):
        await client(auth, api).upsert_episode_collection(
            str(uuid4()), "Name", None, ["嘀咕"], "apple:1:2"
        )


@pytest.mark.parametrize("repeated", [False, True])
async def test_401_exactly_one_refresh_retry(auth, repeated):
    api = API()
    count = 0

    def handler(request):
        nonlocal count
        if request.url.host == "firestore.googleapis.com":
            count += 1
            if repeated or count == 1:
                api.calls.append(request)
                return httpx.Response(401, text=KEY + TOKEN + ID_TOKEN)
        return api(request)

    c = client(auth, handler)
    if repeated:
        with pytest.raises(FirestoreAuthError):
            await c.get_collections_document()
    else:
        await c.get_collections_document()
    assert count == 2
    assert sum(r.url.host == "securetoken.googleapis.com" for r in api.calls) == 2


@pytest.mark.parametrize(
    "status,error", [(403, FirestorePermissionError), (404, DocumentMissingError)]
)
async def test_firestore_errors(auth, status, error):
    api = API()
    api.status = status
    with pytest.raises(error):
        await client(auth, api).get_collections_document()


@pytest.mark.parametrize(
    "mutation", ["string_type", "timestamp", "updateTime", "json", "map", "glyphs", "duplicates"]
)
def test_unexpected_schema(mutation):
    doc = document()
    if mutation == "string_type":
        doc["fields"]["all_user_collections"] = {"mapValue": {}}
    elif mutation == "timestamp":
        doc["fields"]["all_user_collections_timestamp"]["integerValue"] = 123
    elif mutation == "updateTime":
        del doc["updateTime"]
    else:
        value = {
            "json": "{broken",
            "map": "[]",
            "glyphs": json.dumps({"x": collection("x", [5])}),
            "duplicates": '{"x":{},"x":{}}',
        }[mutation]
        doc["fields"]["all_user_collections"]["stringValue"] = value
    with pytest.raises(SchemaError):
        parse_document(doc)


@pytest.mark.parametrize("missing", ["id_token", "user_id"])
async def test_missing_auth_fields(auth, missing):
    api = API()
    del api.auth_body[missing]
    with pytest.raises(RefreshError, match="ID token or UID"):
        await client(auth, api).refresh_auth()


async def test_refresh_failure(auth):
    api = API()
    api.refresh_status = 400
    with pytest.raises(RefreshError):
        await client(auth, api).refresh_auth()


def test_missing_malformed_auth(tmp_path):
    path = tmp_path / "hanly-auth.json"
    with pytest.raises(AuthConfigError, match="missing"):
        HanlyAuth.load(path)
    path.write_text("{broken")
    with pytest.raises(AuthConfigError, match="malformed"):
        HanlyAuth.load(path)


async def test_secret_safe_logs_errors(auth, caplog):
    caplog.set_level(logging.DEBUG)
    api = API()
    api.status = 403
    with pytest.raises(FirestorePermissionError) as error:
        await client(auth, api).get_collections_document()
    output = caplog.text + str(error.value) + repr(auth)
    for secret in (KEY, TOKEN, ID_TOKEN):
        assert secret not in output
    assert "http_status=403" in output


@pytest.fixture
def pack(tmp_path):
    p = tmp_path / "pack"
    p.mkdir()
    material = complete_material()
    (p / "study.json").write_text(material.model_dump_json())
    (p / "transcript.txt").write_text(SOURCE)
    (p / "metadata.json").write_text(
        json.dumps(
            {
                "podcast_id": "1490732024",
                "episode_id": "123",
                "podcast": "大鹏说中文",
                "title": "我们 vs 咱们",
                "study_settings": {"target_language": "zh"},
                "study_complete": True,
                "vocabulary_count": 1,
                "pattern_count": 0,
                "transcript_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(),
            }
        )
    )
    for name in ("reader.md", "study.md", "hanly.csv"):
        (p / name).write_text("fixture")
    (p / "manifest.json").write_text(
        json.dumps({f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in p.iterdir()})
    )
    return p


async def test_retry_after_lost_write_response_reuses_uuid(auth, store, pack):
    api = API()
    api.fail_network = True
    with pytest.raises(HanlyError, match="connection failed"):
        await HanlyUploadService(store, client(auth, api)).upload(pack)
    row = store.db.execute("SELECT * FROM hanly_collections").fetchone()
    assert row["status"] == "unconfirmed"
    identifier = row["uuid"]
    restarted = Storage(store.root)
    try:
        service = HanlyUploadService(restarted, client(HanlyAuth.load(auth.path), api))
        result = await service.upload(pack)
        assert result.collection_id == identifier
        assert result.requested == 1
        assert parse_document(api.doc).collections[identifier]["glyphs"] == ["嘀咕"]
        # Recreate a deleted/absent remote collection using the persisted UUID.
        api.doc = document()
        assert (await service.upload(pack)).collection_id == identifier
        assert identifier in parse_document(api.doc).collections
    finally:
        restarted.close()


async def test_uploads_hanly_then_mosaic_and_failure_isolated(pack):
    calls = []

    async def hanly(path):
        calls.append("hanly")
        raise HanlyError("Fixture Hanly failure")

    async def mosaic(path):
        calls.append("mosaic")
        return SimpleNamespace(message=lambda: "Mosaic uploaded")

    result = await StudyUploads(
        SimpleNamespace(upload=hanly), SimpleNamespace(create_study_pack=mosaic)
    ).run(pack)
    assert calls == ["hanly", "mosaic"]
    assert "Fixture Hanly failure" in result and "Mosaic uploaded" in result


async def test_worker_upload_failure_keeps_completed_pack(store, pack, monkeypatch):
    from conftest import URL

    store.enqueue(URL, "zh", "gpt-transcribe", "", False, 42, 1)
    pipeline = SimpleNamespace(process=AsyncMock(return_value=pack))
    status, deliver = AsyncMock(), AsyncMock()
    monkeypatch.setattr("podcast_bot.queue.completion", lambda path: "Study ready")
    upload = AsyncMock(side_effect=HanlyError("failure"))
    worker = Worker(store, pipeline, status, deliver, upload)
    assert await worker.once()
    assert store.db.execute("SELECT state FROM jobs").fetchone()[0] == "completed"
    deliver.assert_awaited_once()
    assert (pack / "transcript.txt").read_text() == SOURCE


async def test_telegram_hanly_no_new_job(auth, store, pack, config):
    store.remember_pack(42, pack, pack)
    handlers = BotHandlers(config, store)
    handlers.hanly = HanlyUploadService(store, client(auth, API()))
    status = SimpleNamespace(edit_text=AsyncMock())
    message = SimpleNamespace(text="/hanly", reply_text=AsyncMock(return_value=status))
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42, type="private"),
    )
    await handlers.handle(update, SimpleNamespace())
    assert "selected words/expressions verified" in status.edit_text.call_args.args[0]
    assert store.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


async def test_failed_rotation_write_preserves_old_config_and_retries_without_refresh(
    auth, monkeypatch
):
    import podcast_bot.hanly.auth as module

    old = auth.path.read_bytes()
    original = module.os.replace

    def fail(src, dst):
        raise OSError("fixture filesystem error")

    monkeypatch.setattr(module.os, "replace", fail)
    api = API()
    c = client(auth, api)
    with pytest.raises(AuthConfigError, match="save rotated"):
        await c.refresh_auth()
    assert auth.path.read_bytes() == old
    assert not list(auth.path.parent.glob(".hanly-auth-*"))
    monkeypatch.setattr(module.os, "replace", original)
    await c.refresh_auth()
    assert len(api.calls) == 1
    assert HanlyAuth.load(auth.path).refresh_token == "rotated-" + TOKEN


@pytest.mark.parametrize("status", [412, 400])
async def test_other_precondition_statuses_remerge(auth, status):
    api = API()
    first = True

    def handler(request):
        nonlocal first
        if request.method == "PATCH" and first:
            first = False
            api.calls.append(request)
            return httpx.Response(status, json={"error": {"status": "FAILED_PRECONDITION"}})
        return api(request)

    await client(auth, handler).upsert_episode_collection(
        str(uuid4()), "Name", None, ["嘀咕"], "apple:1:2"
    )
    assert [r.method for r in firestore_calls(api)] == ["GET", "PATCH", "GET", "PATCH", "GET"]


async def test_concurrent_401_one_forced_refresh(auth):
    api = API()
    barrier = asyncio.Event()
    unauthorized = 0

    async def handler(request):
        nonlocal unauthorized
        if request.method == "GET" and unauthorized < 2:
            unauthorized += 1
            if unauthorized == 2:
                barrier.set()
            await barrier.wait()
            return httpx.Response(401)
        return api(request)

    c = client(auth, handler)
    await asyncio.gather(c.get_collections_document(), c.get_collections_document())
    assert sum(r.url.host == "securetoken.googleapis.com" for r in api.calls) == 2


async def test_patch_401_retries_identical_conditional_write(auth):
    api = API()
    first = True

    def handler(request):
        nonlocal first
        if request.method == "PATCH" and first:
            first = False
            api.calls.append(request)
            return httpx.Response(401)
        return api(request)

    await client(auth, handler).upsert_episode_collection(
        str(uuid4()), "Name", None, ["嘀咕"], "apple:1:2"
    )
    patches = [r for r in api.calls if r.method == "PATCH"]
    assert len(patches) == 2
    assert patches[0].content == patches[1].content
    assert patches[0].url == patches[1].url


async def test_no_schema_write_on_malformed_remote_document(auth):
    api = API()
    api.doc["fields"]["all_user_collections"]["stringValue"] = "{broken"
    with pytest.raises(SchemaError):
        await client(auth, api).upsert_episode_collection(
            str(uuid4()), "Name", None, ["嘀咕"], "apple:1:2"
        )
    assert api.patches == 0


async def test_uid_and_id_persisted_before_network_write(auth, store, pack):
    api = API()

    def handler(request):
        assert store.db.execute("SELECT count(*) FROM hanly_collections").fetchone()[0] == 1
        return api(request)

    result = await HanlyUploadService(store, client(auth, handler)).upload(pack)
    assert result.collection_id in parse_document(api.doc).collections


async def test_hanly_command_rejects_other_users(auth, store, pack, config):
    store.remember_pack(42, pack, pack)
    handlers = BotHandlers(config, store)
    api = API()
    handlers.hanly = HanlyUploadService(store, client(auth, api))
    message = SimpleNamespace(text="/hanly", reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=99),
        effective_chat=SimpleNamespace(id=42, type="private"),
    )
    await handlers.handle(update, SimpleNamespace())
    assert not api.calls


async def test_manual_only_and_non_chinese_uploads_skip(pack):
    assert await StudyUploads().run(pack) == ""
    p = pack / "metadata.json"
    data = json.loads(p.read_text())
    data["study_settings"]["target_language"] = "de"
    p.write_text(json.dumps(data))
    hanly = SimpleNamespace(upload=AsyncMock())
    assert await StudyUploads(hanly).run(pack) == ""
    hanly.upload.assert_not_called()


@pytest.mark.parametrize("mode", ["auto", "manual", "malformed"])
async def test_application_wires_optional_uploads_without_auth_requests(
    auth, store, config, monkeypatch, mode
):
    from podcast_bot.bot import build_application

    monkeypatch.setattr("podcast_bot.bot.auth_path", lambda: auth.path)
    monkeypatch.setattr("podcast_bot.bot.MosaicConfig.from_env", lambda *a: None)
    monkeypatch.setattr("podcast_bot.queue.Worker.start", lambda self: None)
    monkeypatch.setenv("STUDY_AUTO_UPLOAD", "false" if mode == "manual" else "true")
    if mode == "malformed":
        auth.path.write_text("{invalid")
    app = build_application(config, store)
    try:
        await app.post_init(app)
        handler = next(
            h
            for group in app.handlers.values()
            for h in group
            if "hanly" in getattr(h, "commands", ())
        ).callback.__self__
        assert bool(handler.hanly) == (mode != "malformed")
        assert bool(handler.uploads.hanly) == (mode == "auto")
        assert bool(handler.hanly_error) == (mode == "malformed")
    finally:
        await app.post_stop(app)
