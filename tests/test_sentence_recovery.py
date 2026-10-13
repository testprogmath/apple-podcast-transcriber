import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_pipeline_bot import fake_audio as fake_audio
from test_pipeline_bot import job
from test_reader import call, item
from test_reader import reader as reader

from podcast_bot.models import Chunk, Transcript
from podcast_bot.pipeline import Pipeline, completion
from podcast_bot.reader.audio import align
from podcast_bot.reader.documents import from_text
from podcast_bot.reader.sentences import (
    RECOVERED_ID_BASE,
    paragraphs,
    parse_sentences,
    sentence_quality,
    source_sentences,
)
from podcast_bot.transcription.openai import OpenAITranscriber

# Actual opening supplied by the user; no speculative ASR corrections.
OPENING = (
    "大家好 欢迎来到聊聊东西 我们聊东西文化 也聊很多东西 我是Candice "
    "现在是晚上的11点11分 我抬头一看正好11点11分 非常抱歉 上个月我没有更新播客 "
    "主要因为我实在太忙和太累了 前面的播客我有提到过 基本上是早上八点到晚上十点 "
    "在工作 那十点到十一点可能还要照顾狗狗 所以十一点之后 理论上是有时间可以录播客的 "
    "但是我太累了 我实在是不想做 一直在拖延 所以上个月我就决定休息 彻底的休息 "
    "但是我也没彻底的休息 其实 五一的假期 我有六天 还包括请了一天假 "
    "5月1号到6号 去参加了一个舞蹈的集训"
)


def assert_lossless(text, units):
    previous = 0
    for unit in units:
        assert not text[previous : unit.start].strip()
        assert text[unit.start : unit.end] == unit.text
        previous = unit.end
    assert not text[previous:].strip()


def test_real_opening_is_recovered_without_word_or_punctuation_changes():
    assert len(source_sentences(OPENING)) == 1
    result = parse_sentences(OPENING)
    assert len(result) > 3
    assert all(s.boundary_source == "approximate" and len(s.text) <= 60 for s in result)
    assert all(s.id >= RECOVERED_ID_BASE for s in result)
    assert_lossless(OPENING, result)
    assert any("Candice" in s.text for s in result)
    assert parse_sentences(OPENING) == result
    assert sentence_quality(OPENING)["status"] == "sparse_sentence_boundaries"


@pytest.mark.parametrize(
    "text",
    [
        "你好。Hello! 今天是2026年10月13日。",
        "你好 欢迎来到播客",
        "English words without punctuation " * 30,
        "现在是11.11。价格是3.14。",
    ],
)
def test_normal_or_short_units_unchanged(text):
    assert parse_sentences(text) == source_sentences(text)
    assert sentence_quality(text)["status"] == "ok"


@pytest.mark.parametrize(
    "text", [OPENING, "你好世界" * 70, "繁體中文讀書" * 40, OPENING + "\n\n" + OPENING]
)
def test_source_offsets_and_paragraphs_survive(text):
    units = parse_sentences(text)
    assert_lossless(text, units)
    assert len(set(s.id for s in units)) == len(units)
    assert len(paragraphs(text, units)) == (2 if "\n\n" in text else 1)


def test_unaffected_ids_and_stale_block_ids():
    text = "开始。" + OPENING + "\n\n结束。"
    old = source_sentences(text)
    new = parse_sentences(text)
    assert new[0] == old[0]
    assert new[-1] == old[-1]
    assert old[1].id not in {s.id for s in new}


def test_audio_remains_conservative():
    units = parse_sentences(OPENING)
    timings = [{"start": i, "end": i + 1, "text": s.text} for i, s in enumerate(units)]
    assert set(align(units, timings, len(units))) == {s.id for s in units}
    # No invented timestamps inside a provider segment spanning several fragments.
    assert align(units, [{"start": 0, "end": 30, "text": OPENING}], 30) == {}


async def test_pipeline_checks_output_without_retrying_or_rewriting(config, store, fake_audio):
    client = SimpleNamespace(transcribe=AsyncMock(return_value=Transcript(OPENING, language="zh")))
    status = AsyncMock()
    current = job(store)
    pipeline = Pipeline(config, store, None, client)
    output = await pipeline.process(current, status)
    assert client.transcribe.await_count == 2  # fixture supplies two chunks, no extra calls
    source = OPENING + "\n\n" + OPENING + "\n"
    assert (output / "transcript.txt").read_text() == source
    assert json.loads((output / "audio-timing.json").read_text())["text"] == source
    metadata = json.loads((output / "metadata.json").read_text())
    assert metadata["sentence_quality"]["affected_units"] == 2
    assert "Sparse sentence punctuation" in completion(output)
    assert any("sparse sentence punctuation" in c.args[0] for c in status.await_args_list)
    await pipeline.process(current, status)
    assert client.transcribe.await_count == 2


async def test_whisper_prompt_keeps_punctuation_even_with_long_title(tmp_path):
    path = tmp_path / "audio.wav"
    path.write_bytes(b"fake")
    create = AsyncMock(return_value=SimpleNamespace(model_dump=lambda: {"text": OPENING}))
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
    result = await OpenAITranscriber(client).transcribe(
        Chunk(path, 0, 1), "whisper-1", "zh", "Episode title: " + "title" * 100
    )
    assert result.text == OPENING
    prompt = create.call_args.kwargs["prompt"]
    assert "自然的中文标点" in prompt
    assert len(prompt) <= 80
    assert create.await_count == 1


async def test_reader_existing_document_recovery_and_hanly_use_same_units(reader):
    doc = from_text(42, OPENING)
    reader.store.save_reader_document(doc)
    status, data = await call(reader, "GET", f"/api/reader/{doc.id}")
    assert status == 200 and data["sentence_boundaries_approximate"]
    first = data["sentences"][0]
    assert first["boundary_source"] == "approximate"
    glyph = next(t["t"] for t in first["tokens"] if t["w"])
    assert (await call(reader, "POST", f"/api/reader/{doc.id}/hanly", item(glyph, first["id"])))[
        0
    ] == 200
    # A tab opened before recovery must not silently target a different sentence.
    assert (await call(reader, "POST", f"/api/reader/{doc.id}/hanly", item(glyph, 0)))[0] == 400
    assert reader.store.reader_document(doc.id).raw_text == OPENING
