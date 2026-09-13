import json
import re
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from ..config import language_code
from ..models import AppleLink, Episode, UserError
from ..net import fetch
from .rss import duration_seconds, enclosure, match_episode


def parse_url(url: str) -> AppleLink:
    try:
        p = urlsplit(url.strip())
        ids = re.findall(r"(?:^|/)id([0-9]+)(?:/|$)", p.path)
        query = parse_qs(p.query)
        episode = query.get("i", [])
        if (
            p.scheme != "https"
            or p.hostname != "podcasts.apple.com"
            or p.username
            or p.password
            or p.port not in {None, 443}
            or len(ids) != 1
            or len(episode) != 1
            or not re.fullmatch(r"[0-9]+", episode[0])
        ):
            raise ValueError
        country = p.path.strip("/").split("/")[0]
        if not re.fullmatch(r"[a-z]{2}", country):
            country = "us"
        return AppleLink(ids[0], episode[0], country, url.strip())
    except ValueError:
        raise UserError(
            "Send an https://podcasts.apple.com episode URL containing id… and ?i=…."
        ) from None


async def lookup(client: httpx.AsyncClient, identifier: str, country: str) -> list[dict]:
    query = urlencode(
        {"id": identifier, "entity": "podcastEpisode", "limit": 200, "country": country}
    )
    raw = await fetch(client, "https://itunes.apple.com/lookup?" + query)
    return json.loads(raw).get("results", [])


async def resolve(url: str, client: httpx.AsyncClient) -> Episode:
    link = parse_url(url)
    try:
        records = await lookup(client, link.podcast_id, link.country)
        show = next((r for r in records if str(r.get("trackId")) == link.podcast_id), {})
        episode = next(
            (
                r
                for r in records
                if str(r.get("trackId")) == link.episode_id
                and str(r.get("collectionId")) == link.podcast_id
            ),
            None,
        )
        if episode is None:
            # Some storefronts support direct episode lookup; many return an empty result.
            direct = await lookup(client, link.episode_id, link.country)
            episode = next(
                (
                    r
                    for r in direct
                    if str(r.get("trackId")) == link.episode_id
                    and str(r.get("collectionId")) == link.podcast_id
                ),
                None,
            )
        if episode is None and link.country != "us":
            records = await lookup(client, link.podcast_id, "us")
            episode = next(
                (
                    r
                    for r in records
                    if str(r.get("trackId")) == link.episode_id
                    and str(r.get("collectionId")) == link.podcast_id
                ),
                None,
            )
        if episode is None:
            raise UserError(
                "I couldn't resolve this Apple Podcasts episode. It may be outside Apple's latest 200 episodes or unavailable in this storefront."
            )
        feed_url = episode.get("feedUrl") or show.get("feedUrl")
        if not feed_url:
            raise UserError("I couldn't discover a public RSS feed for this podcast.")
        feed, entry, strategy = match_episode(await fetch(client, feed_url), episode)
        audio, mime = enclosure(entry)
        try:
            language = language_code(feed.get("language"))
        except UserError:
            language = None
        return Episode(
            podcast_id=link.podcast_id,
            episode_id=link.episode_id,
            podcast=show.get("collectionName")
            or episode.get("collectionName")
            or feed.get("title", "Podcast"),
            title=entry.get("title") or episode.get("trackName", "Episode"),
            published=entry.get("published") or episode.get("releaseDate", ""),
            apple_url=link.url,
            feed_url=feed_url,
            guid=entry.get("id", ""),
            audio_url=audio,
            mime_type=mime,
            duration=duration_seconds(entry.get("itunes_duration"))
            or duration_seconds((episode.get("trackTimeMillis") or 0) / 1000),
            language=language,
            strategy=strategy,
        )
    except (httpx.HTTPError, ValueError, KeyError):
        raise UserError(
            "I couldn't resolve this Apple Podcasts episode. Metadata download failed; try again later."
        ) from None
