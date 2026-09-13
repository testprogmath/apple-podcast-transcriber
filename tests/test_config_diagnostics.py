import logging

import pytest

from podcast_bot.config import Config
from podcast_bot.diagnostics import SecretSafeFormatter, diagnostic
from podcast_bot.models import UserError


def test_required_config_missing(monkeypatch):
    import podcast_bot.config as c

    monkeypatch.setattr(c, "load_dotenv", lambda: None)
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(UserError, match="TELEGRAM_BOT_TOKEN"):
        Config.from_env()


def test_secret_safe_format_and_diagnostics():
    formatter = SecretSafeFormatter(("private-token", "private-key"))
    record = logging.LogRecord("test", logging.ERROR, "", 1, "private-token private-key", (), None)
    assert "private-" not in formatter.format(record)
    try:
        raise RuntimeError("private-token")
    except RuntimeError as error:
        result = diagnostic(error)
    assert "private-token" not in result
    assert "test_secret_safe_format_and_diagnostics" in result
