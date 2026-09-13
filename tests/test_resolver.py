import json

import feedparser
import httpx
import pytest
from conftest import FIXTURES, URL

from podcast_bot.models import UserError
from podcast_bot.resolver.apple import parse_url, resolve
from podcast_bot.resolver.rss import duration_seconds, enclosure, match_episode


@pytest.mark.parametrize(
    "url",
    [
        URL,
        "https://podcasts.apple.com/podcast/id1490732024?i=1000789324203",
        "https://podcasts.apple.com/nl/podcast/%E4%B8%AD/id1490732024?i=1000789324203",
    ],
)
def test_parse_ids(url):
    result = parse_url(url)
    assert result.podcast_id == "1490732024"
    assert result.episode_id == "1000789324203"


@pytest.mark.parametrize(
    "url",
    [
        "https://podcasts.apple.com/nl/podcast/id1490732024",
        "https://podcasts.apple.com.evil.test/podcast/id123?i=456",
        "https://evil.test/podcast/id123?i=456",
        "https://podcasts.apple.com/podcast/id123?i=4&i=5",
        "https://a@podcasts.apple.com/podcast/id123?i=4",
        "http://podcasts.apple.com/podcast/id123?i=4",
        "https://podcasts.apple.com:8080/podcast/id123?i=4",
        "https://podcasts.apple.com/podcast/id123?i=abc",
    ],
)
def test_reject_bad_urls(url):
    with pytest.raises(UserError):
        parse_url(url)


async def test_live_fixture_resolver():
    def handler(request):
        if request.url.host == "itunes.apple.com":
            return httpx.Response(200, content=(FIXTURES / "apple.json").read_bytes())
        assert str(request.url) == "https://rss.buzzsprout.com/688918.rss"
        return httpx.Response(200, content=(FIXTURES / "feed.xml").read_bytes())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ep = await resolve(URL, client)
    assert ep.podcast == "大鹏说中文 - Speak Chinese with Da Peng"
    assert "我们" in ep.title and "咱们" in ep.title
    assert ep.guid == "Buzzsprout-19793450"
    assert ep.guid != ep.episode_id
    assert ep.duration == 889
    assert ep.language == "zh"
    assert ep.audio_url.endswith(".mp3")
    assert ep.mime_type == "audio/mpeg"
    assert ep.strategy == "rss-guid"


@pytest.mark.parametrize("strategy", ["direct", "us"])
async def test_metadata_fallback(strategy):
    fixture = json.loads((FIXTURES / "apple.json").read_text())

    def handler(request):
        if request.url.host != "itunes.apple.com":
            return httpx.Response(200, content=(FIXTURES / "feed.xml").read_bytes())
        direct = request.url.params["id"] == "1000789324203"
        us = request.url.params["country"] == "us"
        if (strategy == "direct" and direct) or (strategy == "us" and us):
            return httpx.Response(200, json=fixture)
        return httpx.Response(200, json={"results": fixture["results"][:1]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (await resolve(URL, client)).guid == "Buzzsprout-19793450"


def test_exact_enclosure_fallback():
    data = (FIXTURES / "feed.xml").read_bytes()
    episode = json.loads((FIXTURES / "apple.json").read_text())["results"][1]
    episode["episodeGuid"] = "changed-guid"
    _, entry, strategy = match_episode(data, episode)
    assert strategy == "rss-exact-enclosure"
    assert enclosure(entry)[1] == "audio/mpeg"


def test_no_title_guess():
    with pytest.raises(UserError, match="GUID or exact"):
        match_episode((FIXTURES / "feed.xml").read_bytes(), {"trackName": "我们 咱们"})


def test_ambiguous_guid():
    data = b"<rss><channel><item><guid>x</guid></item><item><guid>x</guid></item></channel></rss>"
    with pytest.raises(UserError):
        match_episode(data, {"episodeGuid": "x"})


def test_missing_enclosure():
    entry = feedparser.parse(b"<rss><channel><item><guid>x</guid></item></channel></rss>").entries[
        0
    ]
    with pytest.raises(UserError, match="enclosure"):
        enclosure(entry)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("15:23", 923), ("1:02:03", 3723), ("889", 889), ("bad", None), (None, None)],
)
def test_duration_parse(value, expected):
    assert duration_seconds(value) == expected
