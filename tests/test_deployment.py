import importlib.util
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import URL

from podcast_bot.health import healthy

spec = importlib.util.spec_from_file_location(
    "receiver", Path(__file__).parents[1] / "deploy/receive.py"
)
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)


def test_drain_preserves_queued_jobs(store):
    identifier, _, _ = store.enqueue(URL, "zh", "model", "", False, 42, 1)
    marker = store.root / "deploy-drain"
    marker.touch()
    assert store.claim() is None
    assert (
        store.db.execute("select state from jobs where id=?", (identifier,)).fetchone()[0]
        == "queued"
    )
    marker.unlink()
    assert store.claim().id == identifier


@pytest.mark.parametrize(
    "data", [None, {}, {"ready": False, "time": time.time()}, {"ready": True, "time": 0}]
)
def test_readiness_fails_closed(tmp_path, data):
    path = tmp_path / "ready"
    if data is not None:
        path.write_text(json.dumps(data))
    assert not healthy(path)


def test_readiness_requires_fresh_heartbeat(tmp_path):
    path = tmp_path / "ready"
    path.write_text(json.dumps({"ready": True, "time": time.time()}))
    assert healthy(path)


@pytest.mark.parametrize("command", ["", "bash", "deploy main", "deploy " + "a" * 40 + "; id"])
def test_forced_command_rejects_shell_and_invalid_sha(monkeypatch, command):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", command)
    with pytest.raises(RuntimeError):
        receiver.main()


def test_drain_waits_for_running_job_and_leaves_queue(store, monkeypatch):
    store.enqueue(URL, "zh", "model", "", False, 42, 1)
    job = store.claim()
    monkeypatch.setattr(receiver, "APP", store.root.parent)
    # Storage fixture directory name is not necessarily data.
    monkeypatch.setattr(receiver, "DRAIN", store.root / "deploy-drain")
    original_connect = receiver.sqlite3.connect
    monkeypatch.setattr(
        receiver.sqlite3,
        "connect",
        lambda *_args, **kwargs: original_connect(store.root / "bot.sqlite3", **kwargs),
    )
    sleeps = []

    def finish(_):
        sleeps.append(True)
        store.finish(job.id, "completed")

    monkeypatch.setattr(receiver.time, "sleep", finish)
    receiver.drain(timeout=5)
    assert sleeps == [True]
    assert receiver.DRAIN.exists()


def test_failed_release_rolls_back_and_removes_drain(tmp_path, monkeypatch):
    sha = "a" * 40
    monkeypatch.setattr(receiver, "STATE", tmp_path)
    monkeypatch.setattr(receiver, "DRAIN", tmp_path / "drain")
    monkeypatch.setattr(receiver.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(receiver, "current_main", lambda: sha)
    monkeypatch.setattr(receiver, "inspect_container", lambda: {"Image": "old-id"})
    monkeypatch.setattr(receiver, "backup", lambda _: None)
    monkeypatch.setattr(receiver, "drain", lambda: receiver.DRAIN.touch())
    monkeypatch.setattr(
        receiver,
        "run",
        lambda args: json.dumps(
            [{"Id": "new-id", "Config": {"Labels": {"org.opencontainers.image.revision": sha}}}]
        ).encode(),
    )
    written = []
    monkeypatch.setattr(receiver, "write_image", written.append)

    def ready(image, **kwargs):
        if image == "new-id":
            raise RuntimeError("unhealthy")

    monkeypatch.setattr(receiver, "wait_ready", ready)
    monkeypatch.setattr(receiver.sys, "stdin", SimpleNamespace(buffer=None))
    with pytest.raises(RuntimeError):
        receiver.deploy(sha)
    assert written == [f"podcast-telegram-bot-release:{sha}", "old-id"]
    assert not receiver.DRAIN.exists()
    assert not (tmp_path / "current-sha").exists()


def test_stale_release_does_not_touch_running_bot(tmp_path, monkeypatch):
    sha = "a" * 40
    monkeypatch.setattr(receiver, "STATE", tmp_path)
    monkeypatch.setattr(receiver.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(receiver, "current_main", lambda: "b" * 40)
    monkeypatch.setattr(
        receiver,
        "run",
        lambda args: json.dumps(
            [{"Id": "new-id", "Config": {"Labels": {"org.opencontainers.image.revision": sha}}}]
        ).encode(),
    )
    monkeypatch.setattr(receiver.sys, "stdin", SimpleNamespace(buffer=None))

    def unexpected():
        raise AssertionError("Stale deployment must not inspect or stop the bot")

    monkeypatch.setattr(receiver, "inspect_container", unexpected)
    receiver.deploy(sha)


def test_draining_timeout_keeps_running_job(store, monkeypatch):
    store.enqueue(URL, "zh", "model", "", False, 42, 1)
    job = store.claim()
    monkeypatch.setattr(receiver, "APP", store.root.parent)
    monkeypatch.setattr(receiver, "DRAIN", store.root / "deploy-drain")
    with pytest.raises(RuntimeError, match="postponed"):
        receiver.drain(timeout=0)
    assert (
        store.db.execute("select state from jobs where id=?", (job.id,)).fetchone()[0] == "running"
    )
