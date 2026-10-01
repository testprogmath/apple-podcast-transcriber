import base64
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_reader import call, episode_source, item, valid_init_data
from test_reader import lexical as lexical
from test_reader import reader as reader

from podcast_bot.models import UserError
from podcast_bot.reader.documents import MAX_FILE_BYTES, from_file, from_text, from_transcript
from podcast_bot.reader.translations import SentenceTranslations, TranslationResult
from podcast_bot.storage import Storage


def file_payload(name="lesson.txt", raw=b"\xe4\xbd\xa0\xe5\xa5\xbd\xe3\x80\x82"):
    return {"source_type": "file", "filename": name, "content": base64.b64encode(raw).decode()}


@pytest.mark.parametrize(
    "text",
    ["中文段落。", "繁體中文，閱讀！", "中文 English 😊。\n\n第二段。", "第一行\n  中文\t文字。"],
)
async def test_paste_preserves_unicode_and_newlines(reader, text):
    status, data = await call(
        reader,
        "POST",
        "/api/reader/documents",
        {"source_type": "pasted_text", "text": "  " + text + " ", "title": "我的中文笔记"},
    )
    assert status == 200
    d = reader.store.reader_document(data["id"])
    assert d.raw_text == text
    assert d.source_type == "pasted_text"
    assert d.source_reference == ""
    assert d.title == "我的中文笔记"
    second = (
        await call(
            reader, "POST", "/api/reader/documents", {"source_type": "pasted_text", "text": text}
        )
    )[1]
    assert second["id"] != d.id
    assert reader.store.reader_document(second["id"]).hanly_key != d.hanly_key


@pytest.mark.parametrize("title", [None, "", "  "])
def test_title_deterministic(title):
    a = from_text(42, "  我最近开始学习中文。\nNext line", title)
    b = from_text(42, "  我最近开始学习中文。\nNext line", title)
    assert a.title == b.title == "我最近开始学习中文。"
    assert from_text(42, "<中文>").title == "Untitled text"


@pytest.mark.parametrize("name", ["lesson.txt", "阅读.md", "閱讀.markdown", "READING.MD"])
def test_file_utf8_and_bom(name):
    d = from_file(42, b"\xef\xbb\xbf" + "中文 English 😊。".encode(), name)
    assert d.source_type == "file"
    assert d.raw_text == "中文 English 😊。"
    assert d.title == name.rsplit(".", 1)[0]
    assert d.source_reference == name.rsplit(".", 1)[0] + "." + name.rsplit(".", 1)[1].lower()


def test_markdown_and_filename():
    d = from_file(
        42,
        "# 中文标题\n\n- **学习** [中文](https://example.org)\n> 引用\n```text\n代码\n```".encode(),
        r"C:\fakepath\阅读.md",
    )
    assert d.title == "阅读"
    assert d.source_reference == "阅读.md"
    assert d.raw_text == "中文标题\n\n学习 中文\n引用\n\n代码"
    assert d.material() == {}
    assert d.native_language() == ""


@pytest.mark.parametrize(
    "name,raw",
    [
        ("a.pdf", b"abc"),
        ("a.txt", b"\xff"),
        ("a.md", b""),
        ("a.txt", b" \n"),
        ("a.txt", b"a" * (MAX_FILE_BYTES + 1)),
        ("a.txt", "中文\0".encode()),
    ],
)
def test_bad_files(name, raw):
    with pytest.raises(UserError):
        from_file(42, raw, name)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"source_type": "web"},
        {"source_type": "pasted_text", "text": " "},
        {"source_type": "pasted_text", "text": 123},
        {"source_type": "pasted_text", "text": "中文", "owner_id": 99},
        {"source_type": "pasted_text", "text": "中文", "chat_id": 99},
        {"source_type": "pasted_text", "text": "中文", "title": []},
        {"source_type": "file", "filename": "a.txt", "content": "%%%"},
        file_payload("a.txt", b"\xff"),
        file_payload("a.html"),
    ],
)
async def test_create_validation(reader, payload):
    assert (await call(reader, "POST", "/api/reader/documents", payload))[0] == 400
    assert len(reader.store.reader_library(42)) == 1


async def test_oversized_file_before_decode(reader):
    data = file_payload(raw=b"a" * (MAX_FILE_BYTES + 1))
    assert (await call(reader, "POST", "/api/reader/documents", data))[0] == 413


@pytest.mark.parametrize("auth", ["", "invalid", valid_init_data(99), valid_init_data(age=90000)])
async def test_library_authorization(reader, auth):
    headers = {"x-telegram-init-data": auth}
    for method, path, payload in [
        ("GET", "/api/reader/documents", None),
        ("POST", "/api/reader/documents", file_payload()),
        ("PATCH", f"/api/reader/documents/{reader.document.id}", {"title": "new"}),
    ]:
        assert (await call(reader, method, path, payload, headers))[0] == 401


async def test_foreign_document_not_listed_or_renamed(reader):
    foreign = from_text(99, "私人的文件。")
    reader.store.save_reader_document(foreign)
    _, data = await call(reader, "GET", "/api/reader/documents")
    assert [d["id"] for d in data["documents"]] == [reader.document.id]
    assert (await call(reader, "GET", f"/api/reader/{foreign.id}"))[0] == 404
    assert (await call(reader, "PATCH", f"/api/reader/documents/{foreign.id}", {"title": "x"}))[
        0
    ] == 404
    assert reader.store.reader_document(foreign.id).title == foreign.title


async def test_library_metadata_order_and_rename(reader):
    source = episode_source(reader.store)
    podcast = from_transcript(42, source)
    file = from_file(42, "中文文件。".encode(), "reading.md")
    reader.store.save_reader_document(podcast)
    reader.store.save_reader_document(file)
    await call(reader, "GET", f"/api/reader/{reader.document.id}")
    queries = []
    reader.store.db.set_trace_callback(queries.append)
    _, data = await call(reader, "GET", "/api/reader/documents")
    reader.store.db.set_trace_callback(None)
    assert data["documents"][0]["id"] == reader.document.id
    assert {d["source_label"] for d in data["documents"]} == {"Podcast", "Pasted text", "Markdown"}
    assert all("raw_text" not in d and "source_reference" not in d for d in data["documents"])
    assert not any("raw_text" in q or "SELECT *" in q for q in queries)
    before = reader.document
    reader.store.save_reader_translation(before.id, 0, before.sentences()[0].text, "Перевод.")
    assert (await call(reader, "PATCH", f"/api/reader/documents/{before.id}", {"title": "新名字"}))[
        0
    ] == 200
    # Recreating the podcast adapter or saving an older instance must not undo rename.
    reader.store.save_reader_document(before)
    with_storage = Storage(reader.store.root)
    after = with_storage.reader_document(before.id)
    assert after == replace(before, title="新名字")
    assert with_storage.reader_translation(before.id, 0, before.sentences()[0].text) == "Перевод."
    assert with_storage.reader_library(42)[0]["title"] == "新名字"
    with_storage.close()


def test_empty_and_bounded_library(store):
    assert store.reader_library(42) == []
    for i in range(105):
        store.save_reader_document(from_text(42, f"中文 {i}"))
    assert len(store.reader_library(42)) == 100
    assert store.reader_library(99) == []


def test_legacy_schema_migration(store):
    d = from_text(42, "旧文档。", source_type="text")
    store.save_reader_document(d)
    store.db.execute("ALTER TABLE reader_documents DROP COLUMN updated")
    store.db.execute("ALTER TABLE reader_documents DROP COLUMN last_opened")
    store.db.commit()
    reopened = Storage(store.root)
    assert reopened.reader_document(d.id).source_type == "pasted_text"
    assert reopened.reader_document(d.id).hanly_key == d.hanly_key
    assert reopened.reader_library(42)[0]["updated"] == d.created
    reopened.close()


@pytest.mark.parametrize("source", ["pasted_text", "file"])
async def test_study_parity_and_stable_integration_identity(reader, source):
    payload = (
        {"source_type": source, "text": "他们获得了奖。"}
        if source == "pasted_text"
        else file_payload(raw="他们获得了奖。".encode())
    )
    status, data = await call(reader, "POST", "/api/reader/documents", payload)
    assert status == 200
    identifier = data["id"]
    route = f"/api/reader/{identifier}"
    _, data = await call(reader, "GET", route)
    assert data["sentences"][0]["text"] == "他们获得了奖。"
    assert data["glossary"]["获得"]["p"]
    assert data["audio"] is None and data["audio_status"] == "text"
    client = SimpleNamespace(
        request=AsyncMock(return_value=(TranslationResult(translation="Они получили награду."), {}))
    )
    reader.api.translations = SentenceTranslations(reader.store, client)
    assert (await call(reader, "POST", route + "/sentences/0/translation", {}))[0] == 200
    h = []
    m = []
    for title in ("first", "renamed"):
        await call(reader, "PATCH", f"/api/reader/documents/{identifier}", {"title": title})
        hs, hd = await call(reader, "POST", route + "/hanly", item("获得", 0))
        ms, md = await call(reader, "POST", route + "/mandarin-mosaic", {"sentence_ids": [0]})
        assert hs == ms == 200
        h.append(hd["collection_id"])
        m.append(md["pack_id"])
    assert h[0] == h[1] and m[0] == m[1]
    assert (await call(reader, "POST", route + "/sentences/0/translation", {}))[1][
        "source"
    ] == "generated"
    client.request.assert_awaited_once()
    assert reader.services.hanly.client.notes


async def test_malformed_json(reader):
    status, _, _ = await reader.api.dispatch("POST", "/api/reader/documents", reader.headers, b"{")
    assert status == 400


def test_script_looking_file_is_literal_text():
    text = '<script>alert("中文")</script>\n<img src=x onerror=alert(1)>中文'
    assert from_file(42, text.encode(), "test.md").raw_text == text


@pytest.mark.parametrize("source", ["pasted_text", "file"])
async def test_standalone_uses_existing_cedict_pipeline(lexical, source):
    d = from_text(42, "他在银行学习。", source_type=source)
    lexical.store.save_reader_document(d)
    status, data = await call(lexical, "GET", f"/api/reader/{d.id}")
    assert status == 200
    assert data["glossary"]["银行"]["en"]
    assert data["glossary"]["银行"]["p"] == "yín háng"
    assert lexical.api.items(d, item("银行", 0))[0][0] == "银行"
    assert lexical.api.selected(d, {"sentence_ids": [0]})[0].text == "他在银行学习。"
