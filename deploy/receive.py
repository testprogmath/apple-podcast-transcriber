#!/usr/bin/env python3
"""Forced SSH command. Install outside the checkout; receives only a release image."""

import fcntl
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

APP = Path("/home/deploy/apps/podcast-telegram-bot")
STATE = Path("/home/deploy/.local/state/podcast-deploy")
OVERRIDE = APP / "docker-compose.release.yml"
DRAIN = APP / "data/deploy-drain"
CONTAINER = "podcast-telegram-bot-bot-1"
REPOSITORY = "testprogmath/apple-podcast-transcriber"


def run(args, *, timeout=120):
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Command failed: " + " ".join(args[:3]))
    return result.stdout


def current_main():
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/commits/main",
        headers={"User-Agent": "podcast-deployer", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)["sha"]


def inspect_container():
    return json.loads(run(["docker", "inspect", CONTAINER]))[0]


def compose_command(old):
    # Use the actual running service's file set, including optional dictionary mounts.
    files = old["Config"]["Labels"]["com.docker.compose.project.config_files"].split(",")
    command = ["docker", "compose", "--project-directory", str(APP)]
    for filename in files:
        path = Path(filename).resolve()
        if path == OVERRIDE:
            continue
        if path.parent != APP or not path.is_file():
            raise RuntimeError("Unexpected or missing Compose configuration file")
        command.extend(["-f", str(path)])
    return [*command, "-f", str(OVERRIDE)]


def write_image(image):
    temp = OVERRIDE.with_suffix(".tmp")
    temp.write_text("services:\n  bot:\n    image: " + json.dumps(image) + "\n")
    temp.replace(OVERRIDE)


def wait_ready(expected_image, *, allow_legacy=False, timeout=180):
    deadline = time.monotonic() + timeout
    consecutive = 0
    while time.monotonic() < deadline:
        info = inspect_container()
        state = info["State"]
        health = state.get("Health", {}).get("Status")
        if (
            info["Image"] == expected_image
            and state["Running"]
            and (health == "healthy" or (allow_legacy and health is None))
        ):
            consecutive += 1
            if consecutive >= 3:
                return
        else:
            consecutive = 0
        time.sleep(5)
    raise RuntimeError("Readiness check timed out")


def drain(timeout=1800):
    deadline = time.monotonic() + timeout
    db = sqlite3.connect(APP / "data/bot.sqlite3", timeout=30)
    try:
        while True:
            with db:
                db.execute("BEGIN IMMEDIATE")
                DRAIN.touch()
                active = db.execute("SELECT count(*) FROM jobs WHERE state='running'").fetchone()[0]
            if not active:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Active job did not finish within 30 minutes; deployment postponed"
                )
            time.sleep(5)
    finally:
        db.close()


def backup(sha):
    directory = APP / "backups"
    directory.mkdir(exist_ok=True)
    with (
        sqlite3.connect(APP / "data/bot.sqlite3") as db,
        sqlite3.connect(directory / f"before-{sha}.sqlite3") as target,
    ):
        db.backup(target)


class LegacyBusy(RuntimeError):
    pass


def stop_legacy_if_idle(old):
    if old.get("Config", {}).get("Healthcheck"):
        return
    # One-time bootstrap: the old worker cannot observe deploy-drain. Hold its
    # SQLite writer lock until it stops, so it cannot claim another paid job.
    with sqlite3.connect(APP / "data/bot.sqlite3", timeout=30) as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM jobs WHERE state='running'").fetchone():
            raise LegacyBusy("Legacy bot started a job; retry deployment later")
        run(["docker", "stop", "--time", "30", CONTAINER])


def deploy(sha, manual=False):
    image = f"podcast-telegram-bot-release:{sha}"
    # stdin is a gzip-compressed docker-save archive; no paths/commands are accepted.
    with (STATE / "load.log").open("wb") as log:
        result = subprocess.run(
            ["docker", "load"], stdin=sys.stdin.buffer, stdout=log, stderr=log, timeout=600
        )
    if result.returncode:
        raise RuntimeError("Image import failed")
    info = json.loads(run(["docker", "image", "inspect", image]))[0]
    if info["Config"].get("Labels", {}).get("org.opencontainers.image.revision") != sha:
        raise RuntimeError("Image revision mismatch")
    # A dispatched release names its own ref, so the stale check applies to automatic ones.
    if not manual and sha != current_main():
        print("Skipped: a newer main commit exists", flush=True)
        return
    old = inspect_container()
    old_image = old["Image"]
    compose = compose_command(old)
    run(["docker", "tag", old_image, "podcast-telegram-bot-rollback:previous"])
    changed = False
    try:
        drain()
        if not manual and sha != current_main():
            print("Skipped: main advanced while waiting", flush=True)
            return
        backup(sha)
        write_image(image)
        changed = True
        stop_legacy_if_idle(old)
        run([*compose, "up", "-d", "--no-build", "--no-deps", "bot"])
        wait_ready(info["Id"])
        (STATE / "current-sha").write_text(sha + "\n")
        print("Deployed " + sha + (" (dispatched)" if manual else ""), flush=True)
    except LegacyBusy:
        # The old container was not stopped. Do not recreate it over its active job.
        write_image(old_image)
        raise
    except Exception:
        if changed:
            write_image(old_image)
            run([*compose, "up", "-d", "--no-build", "--no-deps", "bot"])
            wait_ready(old_image, allow_legacy=True)
            print("Previous image restored; deployment failed", flush=True)
        raise
    finally:
        DRAIN.unlink(missing_ok=True)


def interrupted(signum, _frame):
    raise RuntimeError("Deployment interrupted by signal " + str(signum))


def main():
    command = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    match = re.fullmatch(r"deploy ([0-9a-f]{40})( manual)?", command)
    if not match:
        raise RuntimeError("Only deploy <40-character commit SHA> [manual] is allowed")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        deploy(match[1], manual=bool(match[2]))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Never include subprocess output, environment values, or API payloads.
        print("Deployment failed (" + type(exc).__name__ + ")", file=sys.stderr)
        raise SystemExit(1) from None
