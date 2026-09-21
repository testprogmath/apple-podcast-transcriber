"""Stable external identities shared by Hanly, Mandarin Mosaic, and Reader uploads."""

import hashlib
import json
import re
from pathlib import Path

from .models import UserError


def hanly_identity(metadata: dict) -> str:
    if metadata.get("source_type") == "subtitles" and re.fullmatch(
        r"[0-9a-f]{64}", str(metadata.get("subtitle_id", ""))
    ):
        return "subtitles:" + metadata["subtitle_id"]
    if all(str(metadata.get(k, "")).isdigit() for k in ("podcast_id", "episode_id")):
        return f"apple:{metadata['podcast_id']}:{metadata['episode_id']}"
    if metadata.get("feed_url") and metadata.get("guid"):
        digest = hashlib.sha256(
            json.dumps([metadata["feed_url"], metadata["guid"]]).encode()
        ).hexdigest()
        return "rss:" + digest
    raise UserError("Hanly upload requires a stable episode identity in metadata.")


def mosaic_identity(metadata: dict, path: Path) -> str:
    if metadata.get("source_type") == "subtitles":
        return hanly_identity(metadata)
    if metadata.get("podcast_id") and metadata.get("episode_id"):
        return f"apple:{metadata['podcast_id']}:{metadata['episode_id']}"
    return "file:" + str(metadata.get("canonical_source", path.name))


def collection_name(metadata: dict) -> str:
    return f"{metadata.get('podcast', 'Podcast')[:40]}｜{metadata.get('title', 'Episode')[:80]}"


def pack_name(metadata: dict) -> str:
    return f"{metadata.get('podcast', 'Podcast')} — {metadata.get('title', 'Episode')}"[:250]
