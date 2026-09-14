import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from conftest import URL
from openai import AsyncOpenAI

from podcast_bot.bot import BotHandlers, authorized, send_files
from podcast_bot.models import Chunk, Episode, Transcript, UserError
from podcast_bot.pipeline import Pipeline, cleanup_abandoned
from podcast_bot.queue import Worker
from podcast_bot.transcription.openai import OpenAITranscriber


@pytest.mark.parametrize(
    ("identifier", "kind", "expected"),
    [(42, "private", True), (41, "private", False), (42, "group", False), (None, "private", False)],
)
def test_authorization(identifier, kind, expected):
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=identifier) if identifier else None,
        effective_chat=SimpleNamespace(type=kind),
    )
    assert authorized(update, 42) is expected


def update(text, identifier=42):
    status = SimpleNamespace(message_id=10, edit_text=AsyncMock())
    message = SimpleNamespace(text=text, reply_text=AsyncMock(return_value=status))
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=identifier),
        effective_chat=SimpleNamespace(id=identifier, type="private"),
        effective_message=message,
    )


async def test_unauthorized_ignored(config, store):
    handler = BotHandlers(config, store)
    u = update(URL, 99)
    await handler.handle(u, SimpleNamespace())
    u.effective_message.reply_text.assert_not_called()
    assert store.claim() is None


async def test_commands_language_force_retry(config, store):
    handler = BotHandlers(config, store)
    await handler.handle(update("/transcribe nl " + URL), SimpleNamespace())
    first = store.claim()
    assert first.language == "nl"
    store.finish(first.id, "failed")
    await handler.handle(update("/retry"), SimpleNamespace())
    assert store.claim().id == first.id
    store.finish(first.id, "completed")
    await handler.handle(update("/force " + URL), SimpleNamespace())
    assert store.claim().force


async def test_sdk_model_fields_and_no_translation(tmp_path):
    observed = []

    def response(request):
        body = request.content.decode(errors="replace")
        observed.append(body)
        assert str(request.url).endswith("/audio/transcriptions")
        return httpx.Response(
            200,
            json={
                "text": "我们和咱们。",
                "languages": [{"code": "zh"}],
                "segments": [{"start": 0, "end": 2, "text": "我们和咱们。"}],
            },
        )

    file = tmp_path / "test.mp3"
    file.write_bytes(b"test audio")
    async with AsyncOpenAI(
        api_key="test-only",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(response)),
    ) as client:
        transcriber = OpenAITranscriber(client)
        for model in ("gpt-transcribe", "gpt-4o-transcribe", "whisper-1"):
            result = await transcriber.transcribe(Chunk(file, 0, 2), model, "zh", "大鹏")
            assert result.text == "我们和咱们。"
    assert 'name="languages[]"' in observed[0]
    assert 'name="language"' not in observed[0]
    assert 'name="language"' in observed[1]
    assert "verbose_json" in observed[2]
    assert "timestamp_granularities[]" in observed[2]


async def test_sdk_error_is_safe(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"fake")
    async with AsyncOpenAI(
        api_key="test-only",
        max_retries=0,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(401, json={"error": {"message": "secret-key-sensitive"}})
            )
        ),
    ) as client:
        with pytest.raises(UserError) as error:
            await OpenAITranscriber(client).transcribe(Chunk(f, 0, 1), "gpt-transcribe", "zh", "")
    assert "secret-key-sensitive" not in str(error.value)


@pytest.fixture
def fake_audio(monkeypatch):
    import podcast_bot.pipeline as p

    ep = Episode(
        "1490732024",
        "1000789324203",
        "大鹏",
        "我们和咱们",
        "2026-09-13",
        URL,
        "https://example.test/rss",
        "guid",
        "https://example.test/a.mp3",
        "audio/mpeg",
        2,
        "zh",
        "rss-guid",
    )
    monkeypatch.setattr(p, "resolve", AsyncMock(return_value=ep))
    paths = []

    async def download(client, url, directory, limit):
        path = directory / "audio.mp3"
        path.write_bytes(b"fake audio")
        paths.append(path)
        return path

    async def prepare(path, duration, directory, seconds):
        return [Chunk(path, 0, 1), Chunk(path, 1, 1)]

    monkeypatch.setattr(p.audio, "download", download)
    monkeypatch.setattr(p.audio, "inspect_audio", AsyncMock(return_value=2))
    monkeypatch.setattr(p.audio, "validate_audio", AsyncMock())
    monkeypatch.setattr(p.audio, "prepare", prepare)
    return paths


def job(store, force=False):
    store.enqueue(URL, "zh", "gpt-transcribe", "", force, 42, 10)
    return store.claim()


@pytest.mark.parametrize("failure", [False, True, "cancel"])
async def test_cleanup_success_failure_cancel(config, store, fake_audio, failure):
    transcriber = SimpleNamespace(
        transcribe=AsyncMock(return_value=Transcript("你好。", language="zh"))
    )
    if failure:
        transcriber.transcribe.side_effect = (
            asyncio.CancelledError() if failure == "cancel" else UserError("Test failure")
        )
    pipeline = Pipeline(config, store, None, transcriber)
    current = job(store)
    if failure:
        with pytest.raises(asyncio.CancelledError if failure == "cancel" else UserError):
            await pipeline.process(current, AsyncMock())
    else:
        output = await pipeline.process(current, AsyncMock())
        assert (output / "transcript.txt").read_text() == "你好。\n\n你好。\n"
        assert store.cached(current.cache_key) == output
    assert fake_audio and all(not p.exists() for p in fake_audio)
    assert list((store.root / "tmp").iterdir()) == []


async def test_partial_retry_no_duplicate_charge(config, store, fake_audio):
    transcriber = SimpleNamespace(
        transcribe=AsyncMock(side_effect=[Transcript("我们。", language="zh"), UserError("test")])
    )
    pipeline = Pipeline(config, store, None, transcriber)
    current = job(store)
    with pytest.raises(UserError):
        await pipeline.process(current, AsyncMock())
    store.finish(current.id, "failed")
    store.retry(42, 11)
    current = store.claim()
    transcriber.transcribe = AsyncMock(return_value=Transcript("咱们。", language="zh"))
    output = await pipeline.process(current, AsyncMock())
    assert transcriber.transcribe.await_count == 1
    assert (output / "transcript.txt").read_text() == "我们。\n\n咱们。\n"


async def test_cache_and_force_delivery_retry(config, store, fake_audio):
    transcriber = SimpleNamespace(
        transcribe=AsyncMock(return_value=Transcript("你好。", language="zh"))
    )
    pipeline = Pipeline(config, store, None, transcriber)
    deliver = AsyncMock(side_effect=RuntimeError("Telegram failed"))
    worker = Worker(store, pipeline, AsyncMock(), deliver)
    store.enqueue(URL, "zh", "gpt-transcribe", "", True, 42, 10)
    assert await worker.once()
    assert transcriber.transcribe.await_count == 2
    assert store.retry(42, 11)
    worker.deliver = AsyncMock()
    assert await worker.once()
    assert transcriber.transcribe.await_count == 2
    store.enqueue(URL, "zh", "gpt-transcribe", "", False, 42, 12)
    assert await worker.once()
    assert transcriber.transcribe.await_count == 2
    store.enqueue(URL, "zh", "gpt-transcribe", "", True, 42, 13)
    assert await worker.once()
    assert transcriber.transcribe.await_count == 4


async def test_duration_protection_before_api(config, store, fake_audio, monkeypatch):
    import podcast_bot.pipeline as p

    monkeypatch.setattr(p.audio, "inspect_audio", AsyncMock(return_value=10801))
    transcriber = SimpleNamespace(transcribe=AsyncMock())
    with pytest.raises(UserError, match="exceeds"):
        await Pipeline(config, store, None, transcriber).process(job(store), AsyncMock())
    transcriber.transcribe.assert_not_called()
    assert all(not x.exists() for x in fake_audio)


async def test_immediate_cached_telegram_delivery(config, store, fake_audio):
    transcriber = SimpleNamespace(
        transcribe=AsyncMock(return_value=Transcript("你好。", language="zh"))
    )
    pipeline = Pipeline(config, store, None, transcriber)
    current = job(store)
    await pipeline.process(current, AsyncMock())
    store.finish(current.id, "completed")
    u = update(URL)
    context = SimpleNamespace(bot=SimpleNamespace(send_document=AsyncMock()))
    await BotHandlers(config, store).handle(u, context)
    u.effective_message.reply_text.assert_awaited_once_with("Already transcribed.")
    assert context.bot.send_document.await_count == 1
    assert transcriber.transcribe.await_count == 2


def test_abandoned_cleanup(store):
    work = store.root / "tmp" / "job-1-old"
    work.mkdir(parents=True)
    (work / "audio.mp3").write_bytes(b"old")
    cleanup_abandoned(store)
    assert not work.exists()


async def test_whisper_auto_language_without_languages_array(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"fake")
    async with AsyncOpenAI(
        api_key="test-only",
        max_retries=0,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    json={"text": "你好。", "language": "chinese", "duration": 1, "segments": []},
                )
            )
        ),
    ) as client:
        result = await OpenAITranscriber(client).transcribe(Chunk(f, 0, 1), "whisper-1", None, "")
        assert result.language == "zh"


async def test_interrupted_request_requires_explicit_retry(config, store, fake_audio):
    transcriber = SimpleNamespace(transcribe=AsyncMock())
    current = job(store)
    store.start_usage(current, "episode", 10, None)
    store.recover()
    current = store.claim()
    with pytest.raises(UserError, match="may have been billed"):
        await Pipeline(config, store, None, transcriber).process(current, AsyncMock())
    transcriber.transcribe.assert_not_called()


async def test_auto_uses_history_when_metadata_missing(config, store, fake_audio, monkeypatch):
    import podcast_bot.pipeline as p

    ep = await p.resolve(URL, None)
    monkeypatch.setattr(p, "resolve", AsyncMock(return_value=replace(ep, language=None)))
    store.remember_language(ep.podcast_id, "nl")
    store.enqueue(URL, None, "gpt-transcribe", "", False, 42, 10)
    transcriber = SimpleNamespace(
        transcribe=AsyncMock(return_value=Transcript("Hallo.", language="nl"))
    )
    await Pipeline(config, store, None, transcriber).process(store.claim(), AsyncMock())
    assert transcriber.transcribe.call_args.args[2] == "nl"


async def test_end_to_end_dry_run_with_real_audio_and_mock_services(config, store, tmp_path):
    from conftest import FIXTURES

    from podcast_bot.transcription.audio import run

    source = tmp_path / "synthetic.wav"
    await run(
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=3", str(source)
    )
    requests = []

    def podcast_http(request):
        if request.url.host == "itunes.apple.com":
            return httpx.Response(200, content=(FIXTURES / "apple.json").read_bytes())
        if request.url.path.endswith(".rss"):
            return httpx.Response(200, content=(FIXTURES / "feed.xml").read_bytes())
        return httpx.Response(
            200, content=source.read_bytes(), headers={"content-type": "audio/wav"}
        )

    def openai_http(request):
        requests.append(request)
        assert "/audio/transcriptions" in str(request.url)
        return httpx.Response(200, json={"text": "我们和咱们。", "languages": [{"code": "zh"}]})

    bot = SimpleNamespace(send_document=AsyncMock())
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(podcast_http)) as http,
        AsyncOpenAI(
            api_key="test-only",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(openai_http)),
        ) as sdk,
    ):
        pipeline = Pipeline(config, store, http, OpenAITranscriber(sdk))

        async def deliver(current, output):
            await send_files(bot, current.chat_id, output)

        worker = Worker(store, pipeline, AsyncMock(), deliver)
        handlers = BotHandlers(config, store)
        handlers.worker = worker
        await handlers.handle(update(URL), SimpleNamespace(bot=bot))
        assert await worker.once()
        assert len(requests) == 1
        assert bot.send_document.await_count == 1
        assert list((store.root / "tmp").iterdir()) == []
        result = next((store.root / "transcripts").rglob("transcript.txt"))
        assert result.read_text() == "我们和咱们。\n"
        # Same URL is delivered immediately without another API request.
        await handlers.handle(update(URL), SimpleNamespace(bot=bot))
        assert len(requests) == 1
        assert bot.send_document.await_count == 2
