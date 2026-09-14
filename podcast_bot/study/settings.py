import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass

from ..config import language_code
from ..models import UserError

PROMPT_VERSION = "study-v2-mosaic"
DEFAULT_COLUMNS = ("Chinese", "Pinyin", "Russian", "Example", "ExampleTranslation", "Tags")
COLUMN_ROLES = {
    "Chinese": "term",
    "Pinyin": "pinyin",
    "Russian": "meaning",
    "Example": "example",
    "ExampleTranslation": "example_translation",
    "Tags": "tags",
}


def learner_level(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 +.\-]{0,39}", value):
        raise UserError("Use a short learner level such as HSK3, HSK4, A2, or B1.")
    return value


@dataclass(frozen=True)
class StudySettings:
    target_language: str = "zh"
    native_language: str = "ru"
    learner_level: str = "HSK3"
    model: str = "gpt-5.4-mini"
    vocab_target: int = 20
    mosaic_target: int = 20
    chunk_chars: int = 3000
    csv_columns: tuple[str, ...] = DEFAULT_COLUMNS
    prompt_version: str = PROMPT_VERSION

    def __post_init__(self):
        for code in (self.target_language, self.native_language):
            if not re.fullmatch("[a-z]{2}", code):
                raise UserError("Study languages must be two-letter codes, such as zh or ru.")
        learner_level(self.learner_level)
        if not re.fullmatch(r"[a-zA-Z0-9._-]{1,100}", self.model):
            raise UserError("Invalid OPENAI_STUDY_MODEL name.")
        if not 1 <= self.mosaic_target <= 50:
            raise UserError("MANDARIN_MOSAIC_SENTENCE_TARGET must be 1–50.")
        if not 1 <= self.vocab_target <= 50 or not 1000 <= self.chunk_chars <= 5000:
            raise UserError("VOCAB_TARGET_COUNT must be 1–50; STUDY_CHUNK_CHARS must be 1000–5000.")
        if set(self.csv_columns) != set(DEFAULT_COLUMNS) or len(self.csv_columns) != 6:
            raise UserError("HANLY_CSV_COLUMNS must contain all six default column names once.")

    @classmethod
    def from_env(cls) -> "StudySettings":
        try:
            return cls(
                target_language=language_code(os.getenv("TARGET_LANGUAGE", "zh")) or "zh",
                native_language=language_code(os.getenv("NATIVE_LANGUAGE", "ru")) or "ru",
                learner_level=learner_level(os.getenv("LEARNER_LEVEL", "HSK3")),
                model=os.getenv("OPENAI_STUDY_MODEL", "gpt-5.4-mini"),
                vocab_target=int(os.getenv("VOCAB_TARGET_COUNT", "20")),
                mosaic_target=int(os.getenv("MANDARIN_MOSAIC_SENTENCE_TARGET", "20")),
                chunk_chars=int(os.getenv("STUDY_CHUNK_CHARS", "3000")),
                csv_columns=tuple(
                    os.getenv("HANLY_CSV_COLUMNS", ",".join(DEFAULT_COLUMNS)).split(",")
                ),
            )
        except ValueError:
            raise UserError("Invalid numeric study configuration.") from None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, value: str) -> "StudySettings":
        data = json.loads(value)
        data["csv_columns"] = tuple(data["csv_columns"])
        return cls(**data)


def study_key(transcript: bytes, settings: StudySettings) -> str:
    suffix = b"" if settings.target_language == "zh" else b"\0vocabulary-only-v1"
    return hashlib.sha256(
        transcript + b"\0" + settings.to_json().encode() + suffix + b"\0source-quotes-v1"
    ).hexdigest()


def pricing(model: str) -> tuple[float, float, float] | None:
    """USD / million tokens, checked 2026-09-13. Optional environment overrides."""
    keys = (
        "STUDY_INPUT_USD_PER_MILLION",
        "STUDY_CACHED_INPUT_USD_PER_MILLION",
        "STUDY_OUTPUT_USD_PER_MILLION",
    )
    if any(os.getenv(k) for k in keys):
        try:
            rates = tuple(float(os.environ[k]) for k in keys)
            return rates if all(math.isfinite(x) and x >= 0 for x in rates) else None
        except (KeyError, ValueError):
            return None
    return (0.75, 0.075, 4.5) if model == "gpt-5.4-mini" else None
