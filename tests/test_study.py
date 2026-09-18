import csv
import io
import json
import zipfile
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from conftest import URL
from openai import AsyncOpenAI
from pydantic import ValidationError

from podcast_bot.bot import BotHandlers, send_files
from podcast_bot.models import UserError
from podcast_bot.queue import Worker
from podcast_bot.storage import atomic_json
from podcast_bot.study.chunking import (
    batches,
    normalized,
    split_blocks,
    validate_chunk,
)
from podcast_bot.study.client import OpenAIStudyClient, StudyRequests
from podcast_bot.study.lexical import LexicalChunk
from podcast_bot.study.models import (
    ASRIssue,
    ChunkMaterial,
    Passage,
    ReadingLine,
    Selection,
    StudyMaterial,
    Vocabulary,
)
from podcast_bot.study.pipeline import LearningPipeline
from podcast_bot.study.render import (
    hanly_csv,
    pinyin_markdown,
    study_markdown,
    translation_markdown,
)
from podcast_bot.study.service import StudyService, deduplicate, pack_valid
from podcast_bot.study.settings import StudySettings, pricing, study_key

SOURCE = "听到这儿，你心里会不会嘀咕一句？\n\n咱们分工合作吧。\n"
PINYIN = ["Tīng dào zhèr, nǐ xīnlǐ huì bu huì dígu yí jù?", "Zánmen fēngōng hézuò ba."]


def vocabulary(term="嘀咕"):
    return Vocabulary(
        term=term,
        pinyin="dígu",
        meaning="бормотать; подумать про себя",
        register_note="разговорное",
        example=SOURCE.split("\n")[0],
        example_translation="Услышав это, ты, возможно, про себя подумал…?",
        usage="心里嘀咕 — про себя сомневаться, а не обязательно говорить вслух.",
        tags=["spoken"],
    )


def material_for(blocks):
    return ChunkMaterial(
        passages=[
            Passage(
                block_id=b["id"],
                source=b["text"],
                lines=[ReadingLine(source=b["text"], pinyin=PINYIN[b["id"] % 2])],
                translation="Давайте распределим обязанности."
                if "分工合作" in b["text"]
                else "Услышав это, ты, возможно, про себя подумал…?",
            )
            for b in blocks
        ],
        vocabulary=[vocabulary()] if any("嘀咕" in b["text"] for b in blocks) else [],
        patterns=[],
        pragmatics=[],
        cultural_references=[],
        possible_asr_errors=[],
        mosaic_sentences=[],
    )


def complete_material():
    return StudyMaterial(
        **material_for([{"id": b.id, "text": b.text} for b in split_blocks(SOURCE)]).model_dump()
    )


class FakeStudyClient:
    def __init__(self):
        self.calls = []
        self.fail_at = None

    async def request(self, schema, instructions, payload, model, max_output):
        self.calls.append((schema, payload))
        if len(self.calls) == self.fail_at:
            raise UserError("Mock study failure")
        if schema is LexicalChunk:
            result = LexicalChunk(vocabulary=[vocabulary().model_dump(exclude={"pinyin"})])
        elif issubclass(schema, ChunkMaterial):
            result = material_for(payload["blocks"])
        else:
            result = Selection(
                selected_ids=[x["id"] for x in payload["candidates"][: payload["vocab_target"]]]
            )
        return result, {
            "input_tokens": 1000,
            "output_tokens": 500,
            "input_tokens_details": {"cached_tokens": 100},
        }


@pytest.fixture
def canonical(store):
    store.enqueue(URL, "zh", "gpt-transcribe", "", False, 42, 10)
    job = store.claim()
    source = store.root / "transcripts" / "original"
    source.mkdir(parents=True)
    (source / "transcript.txt").write_text(SOURCE, encoding="utf-8")
    atomic_json(
        source / "metadata.json",
        {
            "apple_url": URL,
            "model": "gpt-transcribe",
            "language": "zh",
            "podcast": "大鹏",
            "title": "我们和咱们",
            "filename": "episode",
            "duration": 15,
            "transcription_seconds": 1,
        },
    )
    store.save_output(job, source)
    store.finish(job.id, "completed")
    store.remember_source(42, source)
    return source


DEFAULT_STUDY_SETTINGS = StudySettings()


def enqueue_study(store, canonical, settings=DEFAULT_STUDY_SETTINGS, force=False):
    store.enqueue_study(canonical, settings.to_json(), 42, 11, regenerate=force)
    return store.claim()


def service(store):
    client = FakeStudyClient()
    return StudyService(store, StudyRequests(store, client)), client


def test_structured_material_rejects_extra_missing_and_invalid_confidence():
    data = complete_material().model_dump()
    data["unexpected"] = "value"
    with pytest.raises(ValidationError):
        StudyMaterial.model_validate(data)
    with pytest.raises(ValidationError):
        Vocabulary.model_validate({"term": "词"})
    with pytest.raises(ValidationError):
        ASRIssue(original="词", suggested_correction="字", confidence=1.1, reason="test")


def test_pinyin_and_translation_are_ordered_and_readable():
    material = complete_material()
    pinyin = pinyin_markdown(material)
    assert (
        pinyin.index("听到这儿")
        < pinyin.index(PINYIN[0])
        < pinyin.index("咱们分工合作")
        < pinyin.index(PINYIN[1])
    )
    assert "Услышав" in translation_markdown(material)
    assert translation_markdown(material).index("Услышав") < translation_markdown(material).index(
        "Давайте"
    )
    assert pinyin_markdown(material) == pinyin


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "rewrite",
        "missing_line",
        "invented_quote",
        "invented_term",
        "numbered_pinyin",
    ],
)
def test_source_validation(mutation):
    blocks = split_blocks(SOURCE)
    material = material_for([{"id": b.id, "text": b.text} for b in blocks])
    if mutation == "missing":
        material.passages.pop()
    if mutation == "duplicate":
        material.passages[1] = material.passages[0].model_copy(deep=True)
    if mutation == "rewrite":
        material.passages[0].source = "改写"
    if mutation == "missing_line":
        material.passages[0].lines[0].source = "听到这儿"
    if mutation == "invented_quote":
        material.vocabulary[0].example = "不存在的句子"
    if mutation == "invented_term":
        material.vocabulary[0].term = "不存在"
    if mutation == "numbered_pinyin":
        material.passages[0].lines[0].pinyin = "ting1 dao4"
    with pytest.raises(UserError):
        validate_chunk(material, blocks, "zh")


def test_whitespace_preservation_and_full_coverage():
    text = (
        "  这是第一句。  这是第二句！\n\n" + "A sentence with words. " * 300 + "\n\n" + "字" * 1500
    )
    blocks = split_blocks(text)
    assert normalized("".join(b.text for b in blocks)) == normalized(text)
    assert all(len(b.text) <= 1200 for b in blocks)
    groups = batches(blocks, 3000)
    assert all(sum(len(b.text) for b in group) <= 3000 for group in groups)
    assert [b.id for group in groups for b in group] == list(range(len(blocks)))


@pytest.mark.parametrize("text", ["", " " * 10, "字" * 300001])
def test_transcript_safety_limits(text):
    with pytest.raises(UserError):
        split_blocks(text)


def test_csv_escaping_unicode_and_configured_order():
    material = complete_material()
    material.vocabulary[0].meaning = '"бормотать", думать\nпро себя'
    csv_text = hanly_csv(material, StudySettings())
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert rows[0]["Chinese"] == "嘀咕"
    assert rows[0]["Russian"] == '"бормотать", думать\nпро себя'
    assert rows[0]["Pinyin"] == "dígu"
    settings = replace(StudySettings(), csv_columns=tuple(reversed(StudySettings().csv_columns)))
    assert hanly_csv(material, settings).splitlines()[0].startswith("Tags,ExampleTranslation")


def test_exact_vocab_deduplication():
    assert len(deduplicate([vocabulary(), vocabulary("嘀 咕")], "term")) == 1


def test_cache_key_separate_settings():
    base = StudySettings()
    a = study_key(SOURCE.encode(), base)
    for kwargs in (
        {"learner_level": "HSK4"},
        {"native_language": "en"},
        {"model": "another-model"},
        {"vocab_target": 25},
        {"prompt_version": "v2"},
        {"target_language": "nl"},
    ):
        assert study_key(SOURCE.encode(), replace(base, **kwargs)) != a
    assert study_key((SOURCE + "。").encode(), base) != a


def test_asr_suggestions_never_rewrite_source():
    material = complete_material()
    material.possible_asr_errors = [
        ASRIssue(
            original="咱们", suggested_correction="我们", confidence=0.99, reason="пример гипотезы"
        )
    ]
    rendered = study_markdown(material, StudySettings())
    assert "Possible ASR issues" in rendered and "99%" in rendered
    assert "咱们分工合作" in pinyin_markdown(material)
    assert "我们分工合作" not in pinyin_markdown(material)
    assert study_markdown(material, StudySettings()) == rendered


async def test_generate_pack_caches_preserves_canonical_and_records_usage(store, canonical):
    svc, client = service(store)
    job = enqueue_study(store, canonical)
    (canonical / "audio-timing.json").write_text('{"version": 1}')
    before = (canonical / "transcript.txt").read_bytes()
    pack = await svc.generate(canonical, StudySettings(), job, AsyncMock())
    assert (
        (canonical / "transcript.txt").read_bytes()
        == before
        == (pack / "transcript.txt").read_bytes()
    )
    assert (pack / "audio-timing.json").read_bytes() == (
        canonical / "audio-timing.json"
    ).read_bytes()
    assert pack_valid(pack)
    assert {
        "transcript.txt",
        "transcript_pinyin.md",
        "translation_ru.md",
        "study.md",
        "hanly.csv",
        "metadata.json",
    } <= {x.name for x in pack.iterdir()}
    assert len(list(csv.DictReader((pack / "hanly.csv").open()))) == 1  # target 20 is not a quota
    archive = next(pack.glob("*.zip"))
    with zipfile.ZipFile(archive) as z:
        assert z.read("transcript.txt") == before
    assert await svc.generate(canonical, StudySettings(), job, AsyncMock()) == pack
    assert len(client.calls) == 2
    assert store.db.execute("SELECT count(*) FROM usage").fetchone()[0] == 0
    rows = store.db.execute(
        "SELECT status,output_tokens,estimated_cost FROM study_usage"
    ).fetchall()
    assert len(rows) == 2 and all(
        r["status"] == "success" and r["output_tokens"] == 500 and r["estimated_cost"] > 0
        for r in rows
    )


async def test_level_change_and_explicit_regeneration_never_call_asr(store, canonical):
    svc, client = service(store)
    speech = SimpleNamespace(process=AsyncMock(side_effect=AssertionError("ASR forbidden")))
    pipeline = LearningPipeline(speech, svc, store, StudySettings())
    first = enqueue_study(store, canonical)
    pack1 = await pipeline.process(first, AsyncMock())
    store.finish(first.id, "completed")
    second = enqueue_study(store, canonical, replace(StudySettings(), learner_level="HSK4"))
    pack2 = await pipeline.process(second, AsyncMock())
    assert pack1 != pack2
    store.finish(second.id, "completed")
    third = enqueue_study(
        store, canonical, replace(StudySettings(), learner_level="HSK4"), force=True
    )
    assert third.force
    pack3 = await pipeline.process(third, AsyncMock())
    assert pack3 != pack2 and len(client.calls) == 6
    assert (
        await pipeline.process(third, AsyncMock()) == pack3
    )  # Delivery retry reuses forced result.
    assert len(client.calls) == 6
    speech.process.assert_not_called()


async def test_partial_study_failure_reuses_validated_chunk(store, canonical):
    svc, client = service(store)
    client.fail_at = 2  # ranking fails after the analysis checkpoint was saved
    current = enqueue_study(store, canonical)
    with pytest.raises(UserError):
        await svc.generate(canonical, StudySettings(), current, AsyncMock())
    store.finish(current.id, "failed")
    store.retry(42, 13)
    current = store.claim()
    client.fail_at = None
    await svc.generate(canonical, StudySettings(), current, AsyncMock())
    assert len([x for x in client.calls if issubclass(x[0], ChunkMaterial)]) == 1


async def test_corrupt_pack_rebuilt_from_checkpoints_without_api(store, canonical):
    svc, client = service(store)
    current = enqueue_study(store, canonical)
    pack = await svc.generate(canonical, StudySettings(), current, AsyncMock())
    (pack / "study.md").write_text("corrupted")
    assert not pack_valid(pack)
    repaired = await svc.generate(canonical, StudySettings(), current, AsyncMock())
    assert pack_valid(repaired) and len(client.calls) == 2


async def test_unknown_cost_does_not_block(store, canonical):
    svc, client = service(store)
    settings = replace(StudySettings(), model="custom-model")
    await svc.generate(canonical, settings, enqueue_study(store, canonical, settings), AsyncMock())
    assert store.db.execute("SELECT estimated_cost FROM study_usage LIMIT 1").fetchone()[0] is None


def test_pricing_bad_override_nonfatal(monkeypatch):
    monkeypatch.setenv("STUDY_INPUT_USD_PER_MILLION", "invalid")
    assert pricing("gpt-5.4-mini") is None


@pytest.mark.parametrize("study_chunk", [False, True])
async def test_real_sdk_structured_request_mocked(study_chunk):
    from podcast_bot.study.models import chunk_schema

    observed = []
    schema = chunk_schema(2) if study_chunk else Selection
    output = (
        material_for([{"id": 0, "text": "你好。"}, {"id": 1, "text": "谢谢。"}]).model_dump_json()
        if study_chunk
        else '{"selected_ids": [1]}'
    )

    def handler(request):
        observed.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "created_at": 1,
                "status": "completed",
                "model": "gpt-5.4-mini",
                "object": "response",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_test",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": output,
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
            },
        )

    async with AsyncOpenAI(
        api_key="test-only",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        result, usage = await OpenAIStudyClient(sdk).request(
            schema,
            "Choose IDs",
            {
                "candidates": [1],
                "blocks": [{"id": 0, "text": "你好。"}, {"id": 1, "text": "谢谢。"}],
            },
            "gpt-5.4-mini",
            1000,
        )
    assert usage["output_tokens"] == 10
    if study_chunk:
        assert len(result.passages) == 2
        shape = observed[0]["text"]["format"]["schema"]["properties"]["passages"]
        assert shape["minItems"] == shape["maxItems"] == 2
    else:
        assert result.selected_ids == [1]
    assert observed[0]["text"]["format"]["type"] == "json_schema"
    assert observed[0]["text"]["format"]["strict"] is True
    assert observed[0]["store"] is False


async def test_study_failure_delivers_canonical(store, canonical):
    svc, client = service(store)
    client.fail_at = 1
    speech = SimpleNamespace(process=AsyncMock(side_effect=AssertionError("ASR forbidden")))
    pipeline = LearningPipeline(speech, svc, store, StudySettings())
    store.enqueue_study(canonical, StudySettings().to_json(), 42, 11)
    deliver = AsyncMock()
    worker = Worker(store, pipeline, AsyncMock(), deliver)
    await worker.once()
    assert deliver.call_args.args[1] == canonical
    assert store.recent_source(42) == canonical


def update(text):
    status = SimpleNamespace(message_id=12, edit_text=AsyncMock())
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(type="private", id=42),
        effective_message=SimpleNamespace(text=text, reply_text=AsyncMock(return_value=status)),
    )


async def test_commands_persist_level_native_language_and_regenerate(config, store, canonical):
    handler = BotHandlers(replace(config, study_enabled=True), store)
    for text in ("/level HSK4", "/native en", "/language de"):
        await handler.handle(update(text), SimpleNamespace())
    assert handler.study_settings().learner_level == "HSK4"
    assert handler.study_settings().native_language == "en"
    assert handler.study_settings().target_language == "de"
    await handler.handle(update("/regenerate"), SimpleNamespace())
    current = store.claim()
    assert current.kind == "study" and current.force
    snapshot = StudySettings.from_json(current.study_settings)
    assert (
        snapshot.learner_level == "HSK4" and snapshot.target_language == "zh"
    )  # source language, not new default
    assert snapshot.native_language == "en"


async def test_non_chinese_outputs_no_pinyin(store, canonical):
    svc, client = service(store)
    settings = replace(StudySettings(), target_language="nl")
    pack = await svc.generate(
        canonical, settings, enqueue_study(store, canonical, settings), AsyncMock()
    )
    assert not (pack / "transcript_pinyin.md").exists()
    assert (pack / "vocabulary.md").is_file()
    assert not (pack / "translation_ru.md").exists()
    assert not (pack / "reader.md").exists()
    assert not (pack / "hanly.csv").exists()


async def test_pack_delivery_and_zip_command(config, store, canonical):
    svc, client = service(store)
    pack = await svc.generate(
        canonical, StudySettings(), enqueue_study(store, canonical), AsyncMock()
    )
    store.remember_pack(42, canonical, pack)
    bot = SimpleNamespace(send_document=AsyncMock(), send_media_group=AsyncMock())
    await send_files(bot, 42, pack)
    assert bot.send_media_group.await_count == 1
    assert len(bot.send_media_group.call_args.kwargs["media"]) == 8
    bot.send_document.reset_mock()
    await BotHandlers(replace(config, study_enabled=True), store).handle(
        update("/zip"), SimpleNamespace(bot=bot)
    )
    assert bot.send_document.call_args.kwargs["filename"].endswith("-study-pack.zip")


def mosaic(chinese, english="A natural English translation."):
    from podcast_bot.study.models import MosaicSentence

    return MosaicSentence(
        chinese=chinese,
        english=english,
        reason="Useful spoken construction.",
        source_start=None,
        source_end=None,
    )


def test_mosaic_verbatim_source_and_no_paraphrase():
    from podcast_bot.study.mosaic import select_candidates

    authentic = mosaic("咱们分工合作吧。")
    assert select_candidates([authentic], SOURCE) == [authentic]
    with pytest.raises(UserError):
        select_candidates([mosaic("我们分工合作吧。")], SOURCE)


def test_mosaic_deduplicates_listening_dialogue_and_punctuation():
    from podcast_bot.study.mosaic import select_candidates

    text = "咱们一起去喝茶吧。\n咱们一起去喝茶吧！\n咱们一起去喝茶吧。"
    result = select_candidates([mosaic(x) for x in text.splitlines()], text)
    assert len(result) == 1
    assert result[0].chinese == "咱们一起去喝茶吧。"


def test_mosaic_near_repetitions_but_preserves_negation():
    from podcast_bot.study.mosaic import select_candidates

    texts = [
        "其实想要用得恰到好处并不是一件容易的事情。",
        "其实想要用得恰到好处并不是一件容易的事情啊。",
        "其实想要用得恰到好处并是一件容易的事情。",
    ]
    result = select_candidates([mosaic(s) for s in texts], "\n".join(texts))
    assert len(result) == 2
    assert texts[2] in [x.chinese for x in result]


@pytest.mark.parametrize(
    "advertisement",
    [
        "请大家订阅我们的频道。",
        "欢迎关注我们的频道。",
        "感谢您的支持。",
        "我的邮箱是abc@example.com。",
        "请访问https://patreon.com/demo。",
    ],
)
def test_mosaic_excludes_promotions(advertisement):
    from podcast_bot.study.mosaic import select_candidates

    assert select_candidates([mosaic(advertisement)], advertisement) == []


def test_mosaic_csv_exact_two_columns_unicode_quotes():
    from podcast_bot.study.render import mosaic_csv

    material = complete_material()
    chinese = "听到这儿，你心里会不会嘀咕一句？"
    english = 'You might think, "Is that it?"'
    material.mosaic_sentences = [mosaic(chinese, english)]
    value = mosaic_csv(material).encode("utf-8").decode("utf-8")
    rows = list(csv.reader(io.StringIO(value)))
    assert rows == [["Chinese", "English"], [chinese, english]]
    assert "Pinyin" not in value and "reason" not in value and "Russian" not in value
    assert len(rows) == 2  # no padding to a 20-sentence target


def test_mosaic_rejects_invented_source_in_structured_material():
    blocks = split_blocks(SOURCE)
    material = material_for([{"id": b.id, "text": b.text} for b in blocks])
    material.mosaic_sentences = [mosaic("大家应该一起分工合作。")]
    with pytest.raises(UserError):
        validate_chunk(material, blocks, "zh")


def test_mosaic_target_changes_cache_and_reader_combines_languages():
    from podcast_bot.study.render import reader_markdown

    settings = StudySettings()
    assert study_key(SOURCE.encode(), settings) != study_key(
        SOURCE.encode(), replace(settings, mosaic_target=12)
    )
    reader = reader_markdown(complete_material(), settings)
    assert "听到这儿" in reader and PINYIN[0] in reader and "Услышав" in reader


async def test_mosaic_integrated_global_ranking_and_pack(store, canonical):

    class Client(FakeStudyClient):
        async def request(self, schema, instructions, payload, model, max_output):
            result, usage = await super().request(schema, instructions, payload, model, max_output)
            if issubclass(schema, ChunkMaterial):
                result.mosaic_sentences = [mosaic(b["text"]) for b in payload["blocks"]]
            return result, usage

    client = Client()
    svc = StudyService(store, StudyRequests(store, client))
    pack = await svc.generate(
        canonical, StudySettings(), enqueue_study(store, canonical), AsyncMock()
    )
    rows = list(csv.DictReader((pack / "mandarin_mosaic.csv").open(encoding="utf-8")))
    assert len(rows) == 2
    assert all(r["Chinese"] in SOURCE and set(r) == {"Chinese", "English"} for r in rows)
    metadata = json.loads((pack / "metadata.json").read_text())
    assert metadata["mosaic_sentence_count"] == 2
    assert (pack / "reader.md").is_file()
    with zipfile.ZipFile(next(pack.glob("*.zip"))) as z:
        assert "mandarin_mosaic.csv" in z.namelist() and "reader.md" in z.namelist()


async def test_sdk_refusal_and_truncation_are_safe():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "created_at": 1,
                "status": "completed",
                "model": "gpt-5.4-mini",
                "object": "response",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_test",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "refusal", "refusal": "Cannot comply."}],
                    }
                ],
            },
        )

    async with AsyncOpenAI(
        api_key="test-only",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        with pytest.raises(UserError, match="refused"):
            await OpenAIStudyClient(sdk).request(Selection, "Choose", {}, "gpt-5.4-mini", 1000)


async def test_study_restart_requires_retry_for_uncertain_call(store, canonical):
    svc, client = service(store)
    current = enqueue_study(store, canonical)
    key = study_key(SOURCE.encode(), StudySettings())
    with store.db:
        store.db.execute(
            "INSERT INTO study_usage(job_id,key,step,model,input_chars,timestamp,status) VALUES (?,?,?,'gpt-5.4-mini',100,'now','started')",
            (current.id, key, "chunk-0"),
        )
    store.recover()
    with pytest.raises(UserError, match="interrupted"):
        await svc.generate(canonical, StudySettings(), current, AsyncMock())
    assert not client.calls
    store.finish(current.id, "failed")
    assert store.retry(42, 12) == current.id
    await svc.generate(canonical, StudySettings(), store.claim(), AsyncMock())
    assert len(client.calls) == 2


@pytest.mark.parametrize("reversed_order", [False, True])
def test_passages_bound_to_exact_source_despite_wrong_ids(reversed_order):
    from podcast_bot.study.chunking import SourceBlock

    blocks = [SourceBlock(7, "你好。"), SourceBlock(8, "谢谢。"), SourceBlock(9, "你好。")]
    result = material_for([{"id": b.id, "text": b.text} for b in blocks])
    for passage in result.passages:
        passage.block_id = 1
    if reversed_order:
        result.passages.reverse()
    validate_chunk(result, blocks, "zh")
    assert [p.block_id for p in result.passages] == [7, 8, 9]
    assert [p.source for p in result.passages] == [b.text for b in blocks]
    assert [p.lines[0].source for p in result.passages] == [b.text for b in blocks]
    validate_chunk(result, blocks, "zh")  # Checkpoint validation remains idempotent.


def test_extra_passage_rejected():
    blocks = split_blocks(SOURCE)
    result = material_for([{"id": b.id, "text": b.text} for b in blocks])
    result.passages.append(result.passages[0].model_copy(deep=True))
    with pytest.raises(UserError, match="complete transcript paragraphs"):
        validate_chunk(result, blocks, "zh")


@pytest.mark.parametrize("count", [1, 2, 7])
def test_chunk_schema_requires_exact_passage_count(count):
    from podcast_bot.study.models import chunk_schema

    schema = chunk_schema(count)
    shape = schema.model_json_schema()["properties"]["passages"]
    assert shape["minItems"] == shape["maxItems"] == count
    valid = material_for([{"id": i, "text": "你好。"} for i in range(count)]).model_dump()
    assert len(schema.model_validate(valid).passages) == count
    for wrong_count in (count - 1, count + 1):
        broken = dict(valid, passages=[valid["passages"][0]] * wrong_count)
        with pytest.raises(ValidationError):
            schema.model_validate(broken)


async def test_generation_schema_requires_both_source_blocks(store, canonical):
    svc, client = service(store)
    await svc.generate(canonical, StudySettings(), enqueue_study(store, canonical), AsyncMock())
    schema, payload = client.calls[0]
    assert len(payload["blocks"]) == 2
    assert schema.model_json_schema()["properties"]["passages"]["minItems"] == 2
