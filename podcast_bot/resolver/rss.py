import feedparser

from ..models import UserError


def duration_seconds(value: str | int | float | None) -> float | None:
    if value is None:
        return None
    try:
        result = 0.0
        for part in str(value).split(":"):
            result = result * 60 + float(part)
        return result if result > 0 else None
    except ValueError:
        return None


def enclosure(entry) -> tuple[str, str]:
    choices = [
        x
        for x in entry.get("enclosures", [])
        if x.get("href")
        and (
            x.get("type", "").startswith("audio/")
            or x.get("type", "") in {"", "application/octet-stream"}
        )
    ]
    if len(choices) != 1:
        raise UserError("I found the episode but couldn't find one unambiguous audio enclosure.")
    return choices[0]["href"], choices[0].get("type", "")


def match_episode(data: bytes, apple: dict):
    feed = feedparser.parse(data)
    if not feed.entries:
        raise UserError("The podcast RSS feed has no readable episodes.")
    guid = apple.get("episodeGuid")
    matches = [e for e in feed.entries if guid and e.get("id") == guid]
    strategy = "rss-guid"
    if not matches:
        audio = apple.get("episodeUrl")
        matches = [
            e
            for e in feed.entries
            if audio and any(x.get("href") == audio for x in e.get("enclosures", []))
        ]
        strategy = "rss-exact-enclosure"
    if len(matches) != 1:
        raise UserError(
            "I couldn't match this Apple episode to one RSS entry by GUID or exact audio URL."
        )
    return feed.feed, matches[0], strategy
