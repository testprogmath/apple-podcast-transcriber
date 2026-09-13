"""Local diagnostics without remote exception bodies, URLs, or credentials."""

import logging
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path


class SecretSafeFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...]):
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")
        self.secrets = tuple(s for s in secrets if s)

    def format(self, record: logging.LogRecord) -> str:
        result = super().format(record)
        for secret in self.secrets:
            result = result.replace(secret, "[REDACTED]")
        return result


def configure_file_logging(directory: Path, secrets: tuple[str, ...]) -> None:
    handler = RotatingFileHandler(
        directory / "bot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    formatter = SecretSafeFormatter(secrets)
    root = logging.getLogger()
    for existing in root.handlers:
        existing.setFormatter(formatter)
    handler.setFormatter(formatter)
    root.addHandler(handler)


def diagnostic(exc: BaseException) -> str:
    frames = traceback.extract_tb(exc.__traceback__)
    location = " -> ".join(f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in frames)
    status = getattr(exc, "status_code", None)
    return f"type={type(exc).__name__} status={status if isinstance(status, int) else 'n/a'} frames={location}"
