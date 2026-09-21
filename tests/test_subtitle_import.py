import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_pipeline_bot import update
from test_study import complete_material, service

from podcast_bot.bot import BotHandlers, send_files
from podcast_bot.identity import hanly_identity, mosaic_identity
from podcast_bot.models import UserError
from podcast_bot.reader.audio import document_audio
from podcast_bot.reader.documents import from_transcript
from podcast_bot.storage import Storage
from podcast_bot.study.pipeline import LearningPipeline
from podcast_bot.study.render import exercises_markdown
from podcast_bot.study.settings import StudySettings, source_study_key
from podcast_bot.subtitle_import import import_srt
from podcast_bot.subtitles import parse_srt

SRT = "1\n00:00:01,000 --> 00:00:06,000\n听到这儿，你心里会不会嘀咕一句？\n"


def uploaded(raw=None, name="lesson.srt"):
    raw = SRT.encode() if raw is None else raw
    u = update("")
    u.effective_message.document = SimpleNamespace(
        file_name=name,
        file_size=len(raw),
        get_file=AsyncMock(
            return_value=SimpleNamespace(download_as_bytearray=AsyncMock(return_value=raw))
        ),
    )
    return u


def test_import_preserves_source_timing_identity_and_reload(store):
    path = import_srt(store.root, SRT.encode(), "lesson.srt", 42, "zh")
    assert import_srt(store.root, SRT.encode(), "renamed.srt", 42, "zh") == path
    assert (path / "transcript.srt").read_bytes() == SRT.encode()
    assert (path / "transcript.txt").read_text() == "听到这儿，你心里会不会嘀咕一句？\n"
    timing = json.loads((path / "audio-timing.json").read_text())
    assert timing["segments"][0]["start"] == 1
    assert timing["audio_url"] is None
    document = from_transcript(42, path)
    assert document.source_type == "subtitles"
    assert document.hanly_key.startswith("subtitles:")
    assert document_audio(document, document.sentences())[0] is None
    store.save_reader_document(document)
    reopened = Storage(store.root)
    try:
        assert reopened.reader_document(document.id) == document
    finally:
        reopened.close()
    metadata = json.loads((path / "metadata.json").read_text())
    assert hanly_identity(metadata) == mosaic_identity(metadata, path)
    different = import_srt(
        store.root, SRT.replace("00:00:01,000", "00:00:02,000").encode(), "lesson.srt", 42, "zh"
    )
    assert source_study_key(path, StudySettings()) != source_study_key(different, StudySettings())
    assert path != import_srt(store.root, SRT.encode(), "lesson.srt", 99, "zh")


@pytest.mark.parametrize(
    "srt",
    [
        SRT.replace("00:00:06,000", "00:00:00,000"),
        SRT.replace("00:00:01,000", "00:61:01,000"),
        SRT + "\n" + SRT.replace("00:00:01,000", "00:00:00,000"),
    ],
)
def test_bad_timestamps_rejected(srt):
    with pytest.raises(UserError):
        parse_srt(srt)


@pytest.mark.parametrize("raw", [b"", b"\xff", b"not subtitles", b"x" * 2_000_001])
def test_invalid_import_does_not_write_source(store, raw):
    with pytest.raises(UserError):
        import_srt(store.root, raw, "lesson.srt", 42, "zh")
    assert not (store.root / "transcripts" / "subtitles").exists()


async def test_upload_opens_reader_and_queues_only_study(config, store):
    handlers = BotHandlers(
        replace(config, study_enabled=True, reader_url="https://reader.example"), store
    )
    handlers.worker = SimpleNamespace(wake=asyncio.Event())
    u = uploaded()
    await handlers.handle_document(u, SimpleNamespace())
    document = store.recent_reader_document(42)
    assert document.source_type == "subtitles"
    job = store.claim()
    assert job.kind == "study" and job.model == "imported-subtitles"
    assert job.url == ""
    assert handlers.worker.wake.is_set()
    assert document.source_reference == job.source_path


async def test_non_chinese_import_uses_lexical_settings_without_reader(config, store):
    handlers = BotHandlers(
        replace(config, study_enabled=True, reader_url="https://reader.example"), store
    )
    store.set_preference("target_language", "en")
    await handlers.handle_document(
        uploaded(SRT.replace("听到这儿，你心里会不会嘀咕一句？", "Hello world.").encode()),
        SimpleNamespace(),
    )
    assert store.recent_reader_document(42) is None
    assert StudySettings.from_json(store.claim().study_settings).target_language == "en"


async def test_disabled_study_still_saves_reader_without_paid_job(config, store):
    handlers = BotHandlers(replace(config, reader_url="https://reader.example"), store)
    await handlers.handle_document(uploaded(), SimpleNamespace())
    assert store.recent_reader_document(42)
    assert store.claim() is None


async def test_mocked_pipeline_creates_hanly_reader_exercises_without_asr(config, store):
    source = import_srt(store.root, SRT.encode(), "lesson.srt", 42, "zh")
    store.enqueue_study(source, StudySettings().to_json(), 42, 10)
    job = store.claim()
    study, client = service(store)
    transcription = SimpleNamespace(process=AsyncMock(side_effect=AssertionError("ASR forbidden")))
    pipeline = LearningPipeline(transcription, study, store, StudySettings())
    pack = await pipeline.process(job, AsyncMock())
    transcription.process.assert_not_called()
    assert (pack / "hanly.csv").is_file()
    assert (pack / "reader.md").is_file()
    assert "嘀咕" in (pack / "exercises.md").read_text()
    assert (pack / "transcript.srt").read_bytes() == SRT.encode()
    assert from_transcript(42, pack).id == from_transcript(42, source).id
    bot = SimpleNamespace(send_media_group=AsyncMock())
    await send_files(bot, 42, pack)
    assert len(bot.send_media_group.call_args.kwargs["media"]) <= 10
    assert study.cached(source, StudySettings()) == pack
    assert store.db.execute("SELECT count(*) FROM usage").fetchone()[0] == 0


def test_exercises_use_only_exact_canonical_quotes():
    material = complete_material()
    assert "嘀咕" in exercises_markdown(material, material.vocabulary[0].example)
    assert "嘀咕" not in exercises_markdown(material, "different source")


async def test_reader_does_not_open_previous_pack_after_new_import(config, store, tmp_path):
    first = import_srt(store.root, SRT.encode(), "first.srt", 42, "zh")
    old_pack = tmp_path / "old-pack"
    old_pack.mkdir()
    (old_pack / "metadata.json").write_text(json.dumps({"canonical_source": str(first)}))
    store.remember_pack(42, first, old_pack)
    new = import_srt(store.root, SRT.replace("嘀咕一句", "说几句").encode(), "second.srt", 42, "zh")
    store.remember_source(42, new)
    handlers = BotHandlers(replace(config, reader_url="https://reader.example"), store)
    await handlers.handle(update("/reader"), SimpleNamespace())
    assert store.recent_reader_document(42).source_reference == str(new)


async def test_repeat_upload_deduplicates_jobs_and_reuses_finished_pack(config, store):
    handlers = BotHandlers(
        replace(config, study_enabled=True, reader_url="https://reader.example"), store
    )
    context = SimpleNamespace(bot=SimpleNamespace(send_media_group=AsyncMock()))
    await handlers.handle_document(uploaded(), context)
    await handlers.handle_document(uploaded(), context)
    assert store.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    job = store.claim()
    source = store.recent_source(42)
    study, client = service(store)
    pack = await study.generate(source, StudySettings(), job, AsyncMock())
    store.finish(job.id, "completed")
    await handlers.handle_document(uploaded(), context)
    assert store.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert store.recent_pack(42) == pack
    assert store.recent_reader_document(42).source_reference == str(pack)
    assert context.bot.send_media_group.await_count == 1
    other = uploaded(SRT.replace("嘀咕一句", "说几句").encode())
    await handlers.handle_document(other, context)
    assert store.recent_pack(42) is None


async def test_unauthorized_and_invalid_srt_never_enqueue(config, store):
    handlers = BotHandlers(
        replace(config, study_enabled=True, reader_url="https://reader.example"), store
    )
    u = uploaded()
    u.effective_user.id = 99
    await handlers.handle_document(u, SimpleNamespace())
    u.effective_message.document.get_file.assert_not_called()
    await handlers.handle_document(uploaded(b"broken"), SimpleNamespace())
    assert store.claim() is None
    assert store.recent_reader_document(42) is None
