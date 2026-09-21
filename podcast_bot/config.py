import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .models import UserError

MODELS = {"gpt-transcribe", "gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"}


def language_code(value: str | None) -> str | None:
    if not value or value.lower() == "auto":
        return None
    code = value.lower().split("-")[0].split("_")[0]
    if not re.fullmatch(r"[a-z]{2}", code):
        raise UserError("Use a two-letter language code such as zh, nl, en, or auto.")
    return code


def reader_url_valid(url: str, dev_mode: bool) -> bool:
    if url.startswith("https://"):
        return True
    return dev_mode and url.startswith(("http://localhost", "http://127.0.0.1"))


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    allowed_user_id: int
    api_key: str = field(repr=False)
    model: str = "gpt-transcribe"
    default_language: str | None = "zh"
    max_minutes: float = 180
    max_download_mb: int = 500
    reader_audio_storage_mb: int = 2000
    chunk_seconds: int = 600
    data_dir: Path = Path("data")
    hints: str = ""
    cost_per_minute: float | None = None
    study_enabled: bool = True
    reader_enabled: bool = True
    reader_url: str = ""
    reader_host: str = "127.0.0.1"
    reader_port: int = 8081
    reader_dev_mode: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        required = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID", "OPENAI_API_KEY")
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise UserError("Set required environment variables: " + ", ".join(missing))
        try:
            c = cls(
                study_enabled=os.getenv("STUDY_ENABLED", "true").lower()
                not in {"false", "0", "no"},
                token=os.environ[required[0]],
                allowed_user_id=int(os.environ[required[1]]),
                api_key=os.environ[required[2]],
                model=os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-transcribe"),
                default_language=language_code(
                    os.getenv("TARGET_LANGUAGE", os.getenv("DEFAULT_LANGUAGE", "zh"))
                ),
                max_minutes=float(os.getenv("MAX_EPISODE_DURATION_MINUTES", "180")),
                max_download_mb=int(os.getenv("MAX_DOWNLOAD_MB", "500")),
                reader_audio_storage_mb=int(os.getenv("READER_AUDIO_STORAGE_MB", "2000")),
                chunk_seconds=int(os.getenv("CHUNK_SECONDS", "600")),
                data_dir=Path(os.getenv("DATA_DIR", "data")).expanduser().resolve(),
                hints=os.getenv("TRANSCRIPTION_HINTS", ""),
                cost_per_minute=float(os.environ["ESTIMATED_COST_PER_MINUTE_USD"])
                if os.getenv("ESTIMATED_COST_PER_MINUTE_USD")
                else None,
                reader_enabled=os.getenv("READER_ENABLED", "true").lower()
                not in {"false", "0", "no"},
                reader_url=os.getenv("READER_PUBLIC_URL", "").strip(),
                reader_host=os.getenv("READER_HOST", "127.0.0.1"),
                reader_port=int(os.getenv("READER_PORT", "8081")),
                reader_dev_mode=os.getenv("READER_DEV_MODE", "false").lower()
                in {"true", "1", "yes"},
            )
        except ValueError:
            raise UserError("Invalid numeric environment configuration.") from None
        if c.model not in MODELS:
            raise UserError("Unsupported model. Choose: " + ", ".join(sorted(MODELS)))
        if c.allowed_user_id <= 0 or not 0 < c.max_minutes <= 1440:
            raise UserError("User ID must be positive; maximum duration must be 0–1440 minutes.")
        if not 1 <= c.max_download_mb <= 2000 or not 30 <= c.chunk_seconds <= 600:
            raise UserError("MAX_DOWNLOAD_MB must be 1–2000; CHUNK_SECONDS must be 30–600.")
        if not 1 <= c.reader_audio_storage_mb <= 50000:
            raise UserError("READER_AUDIO_STORAGE_MB must be 1–50000.")
        if len(c.hints) > 500 or (c.cost_per_minute is not None and c.cost_per_minute < 0):
            raise UserError(
                "Hints must be at most 500 characters; estimated price cannot be negative."
            )
        if not 1 <= c.reader_port <= 65535:
            raise UserError("READER_PORT must be 1–65535.")
        if c.reader_url and not reader_url_valid(c.reader_url, c.reader_dev_mode):
            raise UserError(
                "READER_PUBLIC_URL must be an https:// URL; Telegram requires HTTPS for Mini Apps."
            )
        return c
