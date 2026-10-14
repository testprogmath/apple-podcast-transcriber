import importlib.util
import json
import time
import tomllib
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
    monkeypatch.setattr(
        receiver,
        "inspect_container",
        lambda: {"Image": "old-id", "Config": {"Healthcheck": {"Test": ["CMD", "probe"]}}},
    )
    monkeypatch.setattr(receiver, "backup", lambda _: None)
    monkeypatch.setattr(receiver, "compose_command", lambda _: ["docker", "compose"])
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

    def unexpected(keep):
        raise AssertionError("a failed release must keep every image")

    monkeypatch.setattr(receiver, "prune_releases", unexpected)
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


def test_legacy_bootstrap_never_stops_running_job(store, monkeypatch):
    store.enqueue(URL, "zh", "model", "", False, 42, 1)
    store.claim()
    monkeypatch.setattr(receiver, "APP", store.root.parent)

    def unexpected(_):
        raise AssertionError("must not stop active legacy worker")

    monkeypatch.setattr(receiver, "run", unexpected)
    with pytest.raises(receiver.LegacyBusy):
        receiver.stop_legacy_if_idle({"Config": {}})


def test_legacy_stop_holds_sqlite_writer_lock(store, monkeypatch):
    monkeypatch.setattr(receiver, "APP", store.root.parent)
    called = []

    def stop(args):
        called.append(args)
        with (
            receiver.sqlite3.connect(store.root / "bot.sqlite3", timeout=0) as db,
            pytest.raises(receiver.sqlite3.OperationalError, match="locked"),
        ):
            db.execute("BEGIN IMMEDIATE")

    monkeypatch.setattr(receiver, "run", stop)
    receiver.stop_legacy_if_idle({"Config": {}})
    assert called == [["docker", "stop", "--time", "30", receiver.CONTAINER]]


def test_compose_preserves_all_running_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(receiver, "APP", tmp_path)
    override = tmp_path / "docker-compose.release.yml"
    monkeypatch.setattr(receiver, "OVERRIDE", override)
    paths = [
        tmp_path / name
        for name in ("docker-compose.yml", "docker-compose.hanly.yml", "docker-compose.bkrs.yml")
    ]
    for path in paths:
        path.touch()
    old = {
        "Config": {
            "Labels": {
                "com.docker.compose.project.config_files": ",".join(map(str, [*paths, override]))
            }
        }
    }
    result = receiver.compose_command(old)
    assert all(str(path) in result for path in paths)
    assert result.count(str(override)) == 1


def test_image_copies_the_files_that_declare_dependencies():
    """setuptools silently builds a package with no dependencies if this file is missing."""
    root = Path(__file__).parent.parent
    project = tomllib.loads((root / "pyproject.toml").read_text())
    files = project["tool"]["setuptools"]["dynamic"]["dependencies"]["file"]
    copied = {
        name
        for line in (root / "Dockerfile").read_text().splitlines()
        if line.startswith("COPY ") and "--from" not in line
        for name in line.split()[1:-1]
    }
    assert files and set(files) <= copied


def test_prune_keeps_live_and_previous_releases_and_survives_refusals(monkeypatch):
    calls = []
    listing = (
        "podcast-telegram-bot-release:new sha256:new\n"
        "podcast-telegram-bot-release:old sha256:old\n"
        "podcast-telegram-bot-release:stale sha256:stale\n"
        "podcast-telegram-bot-release:busy sha256:busy\n"
    )

    def run(args):
        calls.append(args)
        if args[:3] == ["docker", "image", "ls"]:
            assert args[-1] == receiver.RELEASES
            return listing.encode()
        if args[-1].endswith(":busy"):
            raise RuntimeError("image is in use")
        return b""

    monkeypatch.setattr(receiver, "run", run)
    assert receiver.prune_releases({"sha256:new", "sha256:old"}) == 1
    removed = [args[-1] for args in calls if args[:3] == ["docker", "image", "rm"]]
    assert removed == ["podcast-telegram-bot-release:stale", "podcast-telegram-bot-release:busy"]


def successful_deploy(tmp_path, monkeypatch, prune):
    sha = "a" * 40
    monkeypatch.setattr(receiver, "STATE", tmp_path)
    monkeypatch.setattr(receiver, "DRAIN", tmp_path / "drain")
    monkeypatch.setattr(receiver.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(receiver, "current_main", lambda: sha)
    monkeypatch.setattr(
        receiver,
        "inspect_container",
        lambda: {"Image": "old-id", "Config": {"Healthcheck": {"Test": ["CMD", "probe"]}}},
    )
    monkeypatch.setattr(receiver, "backup", lambda _: None)
    monkeypatch.setattr(receiver, "compose_command", lambda _: ["docker", "compose"])
    monkeypatch.setattr(receiver, "drain", lambda: None)
    monkeypatch.setattr(
        receiver,
        "run",
        lambda args: json.dumps(
            [{"Id": "new-id", "Config": {"Labels": {"org.opencontainers.image.revision": sha}}}]
        ).encode(),
    )
    written = []
    monkeypatch.setattr(receiver, "write_image", written.append)
    monkeypatch.setattr(receiver, "wait_ready", lambda image, **kwargs: None)
    monkeypatch.setattr(receiver, "prune_releases", prune)
    monkeypatch.setattr(receiver.sys, "stdin", SimpleNamespace(buffer=None))
    receiver.deploy(sha)
    return sha, written


def test_successful_deploy_prunes_all_but_live_and_previous(tmp_path, monkeypatch, capsys):
    kept = []
    sha, written = successful_deploy(tmp_path, monkeypatch, lambda keep: kept.append(keep) or 3)
    assert kept == [{"new-id", "old-id"}]
    assert written == [f"podcast-telegram-bot-release:{sha}"]
    assert "Removed 3 old release images" in capsys.readouterr().out


def test_failed_pruning_never_rolls_back_a_live_release(tmp_path, monkeypatch, capsys):
    def broken(keep):
        raise RuntimeError("docker unavailable")

    sha, written = successful_deploy(tmp_path, monkeypatch, broken)
    assert written == [f"podcast-telegram-bot-release:{sha}"]
    assert (tmp_path / "current-sha").read_text() == sha + "\n"
    assert "Old release images were not pruned" in capsys.readouterr().out
