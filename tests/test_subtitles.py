import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_pipeline_bot import update

from podcast_bot.bot import BotHandlers
from podcast_bot.models import UserError
from podcast_bot.subtitles import Subtitles, choose_track, download_subtitles, plain_text, request

URL = "https://www.youtube.com/watch?v=rHyuQctiDZM"
SRT = "1\n00:00:06,300 --> 00:00:11,433\n你好，<i>世界</i>。\n\n2\n00:00:12,000 --> 00:00:14,000\nA &amp; B\n"


def track(url="https://captions.example/api?lang=zh"):
    return [{"ext": "srt", "url": url}]


@pytest.mark.parametrize(
    ("command", "language"),
    [(f"/subs {URL}", "zh"), (f"/subs en {URL}", "en"), (f"/subs@mybot zh-Hant {URL}", "zh-Hant")],
)
def test_request(command, language):
    assert request(command, "zh") == (URL, language)
    assert request(f"/subs {URL}", "de")[1] == "de"
    assert request(f"/subs {URL}", "auto")[1] == "zh"


@pytest.mark.parametrize(
    "command",
    [
        "/subs",
        "/subs all url",
        "/subs zh https://evil.test",
        "/subs zh https://youtube.com/playlist?list=x",
        f"/subs zh {URL} extra",
    ],
)
def test_reject_invalid_requests(command):
    with pytest.raises(UserError):
        request(command, "zh")


def test_published_preferred_and_no_auto_translation():
    info = {"subtitles": {"zh-Hant": track()}, "automatic_captions": {"zh": track()}}
    assert choose_track(info, "zh")[1:] == ("zh-Hant", False)
    info["subtitles"]["zh"] = track()
    assert choose_track(info, "zh")[1] == "zh"
    info["subtitles"] = {}
    assert choose_track(info, "zh")[1:] == ("zh", True)
    info["automatic_captions"] = {"zh": track("https://captions.example/api?lang=en&tlang=zh")}
    with pytest.raises(UserError, match="No downloadable"):
        choose_track(info, "zh")


@pytest.mark.parametrize(
    "info",
    [
        {},
        {"subtitles": {"en": track()}},
        {"automatic_captions": {"zh": [{"ext": "vtt", "url": "https://example.test"}]}},
    ],
)
def test_absent_unsupported_or_wrong_language(info):
    with pytest.raises(UserError):
        choose_track(info, "zh")


def test_plain_text_keeps_cue_content_and_repetition():
    assert plain_text(SRT) == "你好，世界。\nA & B\n"
    assert plain_text("\ufeff" + SRT.replace("\n", "\r\n")) == plain_text(SRT)
    assert plain_text(SRT + "\n" + SRT.replace("00:00:", "00:01:")) == plain_text(SRT) * 2


@pytest.mark.parametrize(
    "value",
    [
        "",
        "<html>Rate limited</html>",
        "1\nnot a timestamp\n你好",
        "1\n00:00:00,000 --> 00:00:01,000\n<i></i>",
    ],
)
def test_reject_invalid_or_empty_subtitles(value):
    with pytest.raises(UserError):
        plain_text(value)


@pytest.mark.parametrize("fail_delivery", [False, True])
async def test_download_only_caption_file_and_cleanup(tmp_path, monkeypatch, fail_delivery):
    execute = AsyncMock(return_value=(json.dumps({"subtitles": {"zh": track()}}), ""))
    fetch = AsyncMock(return_value=SRT.encode())
    monkeypatch.setattr("podcast_bot.subtitles.run", execute)
    monkeypatch.setattr("podcast_bot.subtitles.fetch", fetch)
    try:
        async with download_subtitles(URL, "zh", tmp_path) as result:
            assert result.srt.read_text() == SRT
            assert result.text.read_text() == plain_text(SRT)
            assert not result.automatic
            assert len(list(result.srt.parent.iterdir())) == 2
            if fail_delivery:
                raise RuntimeError("upload failure")
    except RuntimeError:
        assert fail_delivery
    assert list((tmp_path / "tmp").iterdir()) == []
    args = execute.call_args.args
    assert "--skip-download" in args and "--dump-single-json" in args
    assert "--write-subs" not in args  # Caption retrieval is size-limited by our HTTP client.
    assert args[-1] == URL
    assert fetch.call_args.args[2] == 2_000_000


async def test_no_track_does_not_download_anything(tmp_path, monkeypatch):
    monkeypatch.setattr("podcast_bot.subtitles.run", AsyncMock(return_value=("{}", "")))
    fetch = AsyncMock()
    monkeypatch.setattr("podcast_bot.subtitles.fetch", fetch)
    with pytest.raises(UserError, match="No downloadable"):
        async with download_subtitles(URL, "zh", tmp_path):
            pytest.fail("must not yield")
    fetch.assert_not_called()


@pytest.mark.parametrize("selected_language", ["zh", "en"])
async def test_subtitle_command_authorized_background_and_no_paid_job(
    config, store, tmp_path, monkeypatch, selected_language
):
    store.set_preference("target_language", selected_language)
    entered, finish = asyncio.Event(), asyncio.Event()
    srt, txt = tmp_path / "video.srt", tmp_path / "video.txt"
    srt.write_text(SRT)
    txt.write_text(plain_text(SRT))

    @asynccontextmanager
    async def download(url, language, data_dir):
        assert url == URL and language == selected_language
        entered.set()
        await finish.wait()
        yield Subtitles(srt, txt, language, True)

    monkeypatch.setattr("podcast_bot.bot.download_subtitles", download)
    handlers = BotHandlers(config, store)
    context = SimpleNamespace(bot=SimpleNamespace(send_document=AsyncMock()))
    await handlers.handle(update(f"/subs {URL}", 99), context)
    assert handlers.subs_task is None
    u = update(f"/subs {URL}")
    await handlers.handle(u, context)
    await entered.wait()
    busy = update(f"/subs {URL}")
    await handlers.handle(busy, context)
    assert "already" in busy.effective_message.reply_text.call_args.args[0]
    status = update("/status")
    await handlers.handle(status, context)
    status.effective_message.reply_text.assert_awaited_once()
    finish.set()
    await handlers.subs_task
    assert [call.kwargs["filename"] for call in context.bot.send_document.call_args_list] == [
        "video.srt",
        "video.txt",
    ]
    assert (
        "automatic captions"
        in u.effective_message.reply_text.return_value.edit_text.call_args.args[0]
    )
    assert store.claim() is None
