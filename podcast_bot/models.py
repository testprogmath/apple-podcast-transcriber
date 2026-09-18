from dataclasses import dataclass, field
from pathlib import Path


class UserError(Exception):
    """A safe, actionable message, never containing raw remote exceptions."""


@dataclass(frozen=True)
class AppleLink:
    podcast_id: str
    episode_id: str
    country: str
    url: str


@dataclass(frozen=True)
class Episode:
    podcast_id: str
    episode_id: str
    podcast: str
    title: str
    published: str
    apple_url: str
    feed_url: str
    guid: str
    audio_url: str
    mime_type: str
    duration: float | None
    language: str | None
    strategy: str


@dataclass(frozen=True)
class Chunk:
    path: Path
    offset: float
    duration: float


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    text: str
    segments: list[Segment] = field(default_factory=list)
    language: str | None = None
    usage: dict = field(default_factory=dict)
    words: list[Segment] = field(default_factory=list)


@dataclass(frozen=True)
class Job:
    id: int
    url: str
    language: str | None
    model: str
    hints: str
    force: bool
    chat_id: int
    message_id: int
    state: str
    cache_key: str | None

    kind: str = "transcribe"
    source_path: str | None = None
    study_settings: str | None = None
