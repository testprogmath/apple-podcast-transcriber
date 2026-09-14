import socket
from pathlib import Path

import pytest

from podcast_bot.config import Config
from podcast_bot.storage import Storage

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://podcasts.apple.com/nl/podcast/id1490732024?l=en-GB&i=1000789324203"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    # All external HTTP is mocked. Unexpected networking fails immediately.
    def blocked(*args, **kwargs):
        raise AssertionError("Unexpected real network call in test")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )


@pytest.fixture(autouse=True)
def no_default_dictionary(monkeypatch, tmp_path):
    """Results must not depend on whether a CC-CEDICT database was built locally."""
    monkeypatch.setenv("READER_DICTIONARY", str(tmp_path / "no-dictionary.sqlite3"))


@pytest.fixture
def store(tmp_path):
    storage = Storage(tmp_path / "data")
    yield storage
    storage.close()


@pytest.fixture
def config(tmp_path):
    return Config(
        token="123:test-only",
        allowed_user_id=42,
        api_key="test-only",
        data_dir=tmp_path / "data",
        study_enabled=False,
    )
