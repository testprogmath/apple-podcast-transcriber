from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_reader import FakeHanlyClient, FakeMosaicClient, call, episode_source, valid_init_data

from podcast_bot.hanly.service import HanlyUploadService
from podcast_bot.mosaic.service import MosaicUploadService
from podcast_bot.reader.api import ReaderApi
from podcast_bot.reader.cedict import build
from podcast_bot.reader.dictionary import Dictionary
from podcast_bot.reader.documents import from_file, from_text, from_transcript
from podcast_bot.reader.enrich import enrich
from podcast_bot.reader.tokens import Override, segment, tokenize
from podcast_bot.storage import Storage, atomic_json

TOKEN = "123:test-only"
SAMPLE = """中國 中国 [Zhong1 guo2] /China/
人民 人民 [ren2 min2] /the people/
中國人民 中国人民 [Zhong1 guo2 ren2 min2] /Chinese people/
想象 想象 [xiang3 xiang4] /to imagine/
沒有 没有 [mei2 you3] /not have/
那麼 那么 [na4 me5] /so/
難 难 [nan2] /difficult/
"""
TEXT = "学中文没有想象中那么难。中国人民很热情。\n\n🙂好的，想象中很好，新的想象中也很好。"


@pytest.fixture
def dictionary(tmp_path):
    (tmp_path / "sample.u8").write_text(SAMPLE, encoding="utf-8")
    build(tmp_path / "sample.u8", tmp_path / "sample.sqlite3")
    value = Dictionary(tmp_path / "sample.sqlite3")
    yield value
    value.close()


@pytest.fixture
def env(config, store, dictionary):
    hanly = FakeHanlyClient()
    mosaic = FakeMosaicClient()
    services = SimpleNamespace(
        hanly=HanlyUploadService(store, hanly),
        mosaic=MosaicUploadService(store, mosaic),
        hanly_error=None,
        mosaic_error=None,
    )
    document = from_text(config.allowed_user_id, TEXT)
    store.save_reader_document(document)
    return SimpleNamespace(
        api=ReaderApi(replace(config, token=TOKEN), store, services, dictionary=dictionary),
        document=document,
        store=store,
        hanly=hanly,
        mosaic=mosaic,
        headers={"x-telegram-init-data": valid_init_data()},
    )


def span(sentence_id, text, expression, nth=0):
    start = -1
    for _ in range(nth + 1):
        start = text.index(expression, start + 1)
    return {
        "sentence_id": sentence_id,
        "start": start,
        "end": start + len(expression),
        "text": expression,
    }


def sentence(env, identifier):
    return next(s for s in env.document.sentences() if s.id == identifier)


def create(env, sentence_id, expression, nth=0, document=None):
    document = document or env.document
    text = next(s.text for s in document.sentences() if s.id == sentence_id)
    return call(
        env,
        "POST",
        f"/api/reader/{document.id}/lexical-overrides",
        span(sentence_id, text, expression, nth),
    )


async def read_words(env, sentence_id, document=None):
    _, data = await call(env, "GET", f"/api/reader/{(document or env.document).id}")
    return [t["t"] for t in data["sentences"][sentence_id]["tokens"] if t["w"]], data


async def test_override_makes_one_chunk_and_reports_the_sentence(env):
    before, _ = await read_words(env, 0)
    assert "想象中" not in before and "想象" in before
    status, data = await create(env, 0, "想象中")
    assert status == 200
    tokens = data["sentence"]["tokens"]
    custom = [t for t in tokens if t.get("override")]
    assert custom == [
        {"t": "想象中", "w": True, "start": 5, "end": 8, "override": data["override"]["id"]}
    ]
    assert "".join(t["t"] for t in tokens) == "学中文没有想象中那么难。"
    assert data["glossary"]["想象中"]["meaning_source"] == "missing"
    after, read = await read_words(env, 0)
    assert after[after.index("没有") + 1] == "想象中"
    assert read["sentences"][0]["text"] == sentence(env, 0).text


async def test_exact_duplicate_is_idempotent(env):
    _, first = await create(env, 0, "想象中")
    status, second = await create(env, 0, "想象中")
    assert status == 200
    assert second["override"]["id"] == first["override"]["id"]
    assert len(env.store.reader_lexical_overrides(env.document.id)[0]) == 1


async def test_overlapping_override_is_rejected(env):
    await create(env, 0, "想象中")
    status, data = await create(env, 0, "中那么")
    assert status == 409
    assert data == {"error": "That overlaps an existing custom expression."}
    status, _ = await create(env, 0, "没有想象中那么难")
    assert status == 409


async def test_adjacent_overrides_are_kept_separate(env):
    await create(env, 0, "没有想象")
    status, data = await create(env, 0, "中那么难")
    assert status == 200
    custom = [t["t"] for t in data["sentence"]["tokens"] if t.get("override")]
    assert custom == ["没有想象", "中那么难"]


@pytest.mark.parametrize(
    "payload",
    [
        {"sentence_id": 0, "start": 5, "end": 5, "text": ""},
        {"sentence_id": 0, "start": 8, "end": 5, "text": "想象中"},
        {"sentence_id": 0, "start": -1, "end": 2, "text": "学中"},
        {"sentence_id": 0, "start": 5, "end": 99, "text": "想象中"},
        {"sentence_id": 0, "start": True, "end": 2, "text": "学中"},
        {"sentence_id": 0, "start": 5, "end": 8},
        {"sentence_id": 0, "start": 5, "end": 8, "text": "想象中", "chat_id": 7},
        {"sentence_id": "0", "start": 5, "end": 8, "text": "想象中"},
        {"sentence_id": 99, "start": 0, "end": 2, "text": "学中"},
    ],
)
async def test_invalid_offsets_and_shapes_are_rejected(env, payload):
    status, _ = await call(env, "POST", f"/api/reader/{env.document.id}/lexical-overrides", payload)
    assert status in (400, 409)
    assert not env.store.reader_lexical_overrides(env.document.id)


async def test_source_mismatch_is_rejected(env):
    payload = {**span(0, sentence(env, 0).text, "想象中"), "text": "想像中"}
    status, data = await call(
        env, "POST", f"/api/reader/{env.document.id}/lexical-overrides", payload
    )
    assert status == 409
    assert data == {"error": "This document has changed. Reopen Reader."}


async def test_selection_across_sentences_or_punctuation_is_rejected(env):
    # Sentence 0 ends with 。; offsets are sentence-local, so nothing reaches sentence 1.
    text = sentence(env, 0).text
    status, data = await call(
        env,
        "POST",
        f"/api/reader/{env.document.id}/lexical-overrides",
        {"sentence_id": 0, "start": 9, "end": len(text), "text": text[9:]},
    )
    assert status == 400
    assert data == {"error": "Select Chinese text within one sentence."}


async def test_one_character_cannot_become_an_expression(env):
    status, data = await create(env, 0, "难")
    assert status == 400
    assert data == {"error": "Select at least two characters to make an expression."}


async def test_another_users_document_is_unreachable(env, config):
    foreign = from_text(777, "研究成果很好。")
    env.store.save_reader_document(foreign)
    status, _ = await create(env, 0, "研究成果", document=foreign)
    assert status == 404
    status, _ = await call(
        env, "POST", f"/api/reader/{env.document.id}/lexical-overrides", {}, headers={}
    )
    assert status == 401
    other = {"x-telegram-init-data": valid_init_data(user_id=777)}
    status, _ = await call(
        env,
        "POST",
        f"/api/reader/{env.document.id}/lexical-overrides",
        span(0, sentence(env, 0).text, "想象中"),
        headers=other,
    )
    assert status == 401
    assert not env.store.reader_lexical_overrides(env.document.id)


async def test_override_of_another_document_cannot_be_deleted(env, config):
    _, data = await create(env, 0, "想象中")
    second = from_text(config.allowed_user_id, "想象中很好。")
    env.store.save_reader_document(second)
    status, _ = await call(
        env, "DELETE", f"/api/reader/{second.id}/lexical-overrides/{data['override']['id']}"
    )
    assert status == 404
    assert env.store.reader_lexical_overrides(env.document.id)


async def test_stale_override_is_ignored_and_reading_still_works(env):
    env.store.save_reader_lexical_override(env.document.id, 0, 5, 8, "想像中", [])
    words, _ = await read_words(env, 0)
    assert "想像中" not in words and "想象中" not in words
    # A stale row never blocks a fresh expression over the same text.
    status, data = await create(env, 0, "想象中")
    assert status == 200
    rows = env.store.reader_lexical_overrides(env.document.id)[0]
    assert [(o.start, o.end, o.text) for o in rows] == [(5, 8, "想象中")]


async def test_override_becomes_stale_when_the_stored_text_changes(env):
    await create(env, 0, "想象中")
    changed = replace(env.document, raw_text="学中文没有想像中那么难。")
    env.store.save_reader_document(changed)
    env.document = changed
    _, data = await read_words(env, 0)
    assert not any(t.get("override") for t in data["sentences"][0]["tokens"])
    assert data["sentences"][0]["text"] == "学中文没有想像中那么难。"


async def test_delete_restores_automatic_segmentation(env, dictionary):
    _, data = await create(env, 0, "想象中")
    status, removed = await call(
        env, "DELETE", f"/api/reader/{env.document.id}/lexical-overrides/{data['override']['id']}"
    )
    assert status == 200
    automatic = [t.text for t in tokenize(sentence(env, 0).text, frozenset(), dictionary)]
    assert [t["t"] for t in removed["sentence"]["tokens"]] == automatic
    assert not any(t.get("override") for t in removed["sentence"]["tokens"])
    status, _ = await call(
        env, "DELETE", f"/api/reader/{env.document.id}/lexical-overrides/{data['override']['id']}"
    )
    assert status == 404


@pytest.mark.parametrize("identifier", ["0", "abc", "1.5", "-1"])
async def test_malformed_override_ids_are_refused(env, identifier):
    status, _ = await call(
        env, "DELETE", f"/api/reader/{env.document.id}/lexical-overrides/{identifier}"
    )
    assert status == 404


async def test_overrides_survive_a_process_restart(env, config, dictionary):
    await create(env, 0, "想象中")
    env.store.close()
    restarted = Storage(config.data_dir)
    try:
        api = ReaderApi(
            replace(config, token=TOKEN),
            restarted,
            SimpleNamespace(hanly=None, mosaic=None, hanly_error="", mosaic_error=""),
            dictionary=dictionary,
        )
        words, _ = await read_words(SimpleNamespace(api=api, headers=env.headers), 0, env.document)
        assert "想象中" in words
    finally:
        restarted.close()


def test_segmentation_runs_around_the_override(dictionary):
    text = "中国人民很热情。"
    automatic = tokenize(text, frozenset(), dictionary)
    assert "中国人民" not in [t.text for t in automatic]
    tokens = segment(text, frozenset(), dictionary, [Override(3, 0, 4, "中国人民")])
    assert [(t.text, t.start, t.end, t.override) for t in tokens][0] == ("中国人民", 0, 4, 3)
    rest = tokenize(text[4:], frozenset(), dictionary)
    assert [t.text for t in tokens[1:]] == [t.text for t in rest]
    assert [t.start for t in tokens[1:]] == [t.start + 4 for t in rest]
    assert segment(text, frozenset(), dictionary, []) == automatic


def test_segmentation_never_splits_inside_an_override_over_a_study_chunk(dictionary):
    text = "学中文没有想象中那么难。"
    tokens = segment(text, frozenset({"想象中那么"}), dictionary, [Override(1, 3, 7, "没有想象")])
    words = [t.text for t in tokens if t.word]
    assert "没有想象" in words
    # The study claim no longer fits around the override, so it is not forced.
    assert "".join(t.text for t in tokens) == text


def test_overlapping_and_stale_overrides_never_break_segmentation(dictionary):
    text = "学中文没有想象中那么难。"
    tokens = segment(
        text,
        frozenset(),
        dictionary,
        [Override(1, 5, 8, "想象中"), Override(2, 6, 9, "象中那"), Override(3, 0, 2, "学英")],
    )
    assert [t.text for t in tokens if t.override] == ["想象中"]
    assert "".join(t.text for t in tokens) == text


async def test_custom_expression_enrichment_uses_the_whole_glyph(env, dictionary):
    _, data = await create(env, 1, "中国人民")
    entry = data["glossary"]["中国人民"]
    expected = enrich(["中国人民"], {}, {}, dictionary)["中国人民"]
    assert entry["p"] == expected.pinyin and entry["p"]
    assert entry["en"] == ["Chinese people"]
    assert entry["meaning_source"] == "dictionary"
    token = next(t for t in data["sentence"]["tokens"] if t.get("override"))
    assert (token["start"], token["end"]) == (0, 4)


async def test_custom_expression_vocabulary_state_uses_the_whole_glyph(env):
    _, data = await create(env, 0, "想象中")
    status, _ = await call(
        env,
        "POST",
        f"/api/reader/{env.document.id}/vocabulary-state",
        {"glyph": "想象中", "state": "known"},
    )
    assert status == 200
    assert env.store.vocabulary_state("想象中") == "known"
    await call(
        env, "DELETE", f"/api/reader/{env.document.id}/lexical-overrides/{data['override']['id']}"
    )
    assert env.store.vocabulary_state("想象中") == "known"


async def test_only_the_selected_occurrence_becomes_an_expression(env):
    text = sentence(env, 2).text
    status, data = await create(env, 2, "想象中", nth=1)
    assert status == 200
    custom = [t for t in data["sentence"]["tokens"] if t.get("override")]
    assert len(custom) == 1
    # 🙂 is one code point: offsets match Python string indices and JavaScript Array.from.
    assert custom[0]["start"] == text.index("想象中", text.index("想象中") + 1)
    assert custom[0]["start"] == len("🙂好的，想象中很好，新的") == 12


async def test_make_expression_never_uploads_to_hanly(env):
    await create(env, 0, "想象中")
    assert not env.hanly.calls


async def test_selection_lookup_enriches_without_persisting(env):
    status, data = await call(
        env,
        "POST",
        f"/api/reader/{env.document.id}/selection",
        span(0, sentence(env, 0).text, "没有想象中那么难"),
    )
    assert status == 200
    assert data["glyph"] == "没有想象中那么难"
    assert (data["start"], data["end"]) == (3, 11)
    assert data["entry"]["p"]
    assert data["entry"]["meaning_source"] == "missing"
    assert not env.store.reader_lexical_overrides(env.document.id)
    assert not env.hanly.calls


async def test_hanly_accepts_an_exact_selected_span_without_an_override(env):
    selected = span(0, sentence(env, 0).text, "没有想象中那么难")
    item = {
        "glyph": selected["text"],
        "sentence_id": 0,
        "start": selected["start"],
        "end": selected["end"],
    }
    status, data = await call(
        env, "POST", f"/api/reader/{env.document.id}/hanly", {"items": [item]}
    )
    assert status == 200
    assert env.hanly.calls[0][2] == ["没有想象中那么难"]
    assert data["vocabulary_states"] == {"没有想象中那么难": "learning"}
    assert not env.store.reader_lexical_overrides(env.document.id)
    words, _ = await read_words(env, 0)
    assert "没有想象中那么难" not in words


@pytest.mark.parametrize(
    "item",
    [
        {"glyph": "没有想像", "sentence_id": 0, "start": 3, "end": 7},
        {"glyph": "没有想象", "sentence_id": 1, "start": 3, "end": 7},
        {"glyph": "难。", "sentence_id": 0, "start": 10, "end": 12},
    ],
)
async def test_hanly_rejects_spans_that_are_not_the_canonical_slice(env, item):
    status, data = await call(
        env, "POST", f"/api/reader/{env.document.id}/hanly", {"items": [item]}
    )
    assert status == 400
    assert data == {"error": "A selected expression is not part of the selected sentence."}
    assert not env.hanly.calls


async def test_hanly_item_of_a_removed_expression_still_uploads_by_span(env):
    _, data = await create(env, 0, "想象中")
    await call(
        env, "DELETE", f"/api/reader/{env.document.id}/lexical-overrides/{data['override']['id']}"
    )
    status, _ = await call(
        env,
        "POST",
        f"/api/reader/{env.document.id}/hanly",
        {"items": [{"glyph": "想象中", "sentence_id": 0, "start": 5, "end": 8}]},
    )
    assert status == 200
    status, _ = await call(
        env,
        "POST",
        f"/api/reader/{env.document.id}/hanly",
        {"items": [{"glyph": "想象中", "sentence_id": 0}]},
    )
    assert status == 400


async def test_custom_expression_is_a_hanly_reader_word_while_it_exists(env):
    await create(env, 0, "想象中")
    status, _ = await call(
        env,
        "POST",
        f"/api/reader/{env.document.id}/hanly",
        {"items": [{"glyph": "想象中", "sentence_id": 0}]},
    )
    assert status == 200


async def test_mosaic_and_sentence_identity_are_unchanged(env):
    _, before = await call(env, "GET", f"/api/reader/{env.document.id}")
    await create(env, 0, "想象中")
    _, after = await call(env, "GET", f"/api/reader/{env.document.id}")
    assert [(s["id"], s["text"], s["audio"]) for s in after["sentences"]] == [
        (s["id"], s["text"], s["audio"]) for s in before["sentences"]
    ]
    status, _ = await call(
        env, "POST", f"/api/reader/{env.document.id}/mandarin-mosaic", {"sentence_ids": [0]}
    )
    assert status == 200
    assert [s["Mandarin"] for s in env.mosaic.batches[0]] == ["学中文没有想象中那么难。"]


async def test_file_documents_support_overrides(env, config):
    document = from_file(config.allowed_user_id, "学中文没有想象中那么难。".encode(), "notes.txt")
    env.store.save_reader_document(document)
    status, _ = await create(env, 0, "想象中", document=document)
    assert status == 200
    words, _ = await read_words(env, 0, document)
    assert "想象中" in words


async def test_podcast_documents_keep_study_chunks_around_overrides(env, config, store):
    path = episode_source(store, "学中文没有想象中那么难。很多人都在讨论数学家。\n")
    material = {"vocabulary": [{"term": "数学家", "meaning": "математик"}]}
    atomic_json(path / "study.json", material)
    document = from_transcript(config.allowed_user_id, path)
    store.save_reader_document(document)
    status, _ = await create(env, 0, "想象中", document=document)
    assert status == 200
    words, data = await read_words(env, 1, document)
    assert "数学家" in words
    assert data["glossary"]["数学家"]["ru"] == ["математик"]
    first, _ = await read_words(env, 0, document)
    assert "想象中" in first
