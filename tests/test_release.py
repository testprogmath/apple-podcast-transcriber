import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from podcast_bot.bot import BotHandlers
from podcast_bot.version import release

ROOT = Path(__file__).parents[1]
CI = (ROOT / ".github/workflows/ci.yml").read_text()
RELEASE = (ROOT / ".github/workflows/release.yml").read_text()

spec = importlib.util.spec_from_file_location("bumper", ROOT / "tools/bump_version.py")
bumper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bumper)

spec = importlib.util.spec_from_file_location("receiver", ROOT / "deploy/receive.py")
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)

PYPROJECT = '[project]\nname = "x"\nversion = "1.4.9"\ndescription = "y"\n'


@pytest.mark.parametrize(
    "level,expected",
    [("patch", "1.4.10"), ("minor", "1.5.0"), ("major", "2.0.0")],
)
def test_each_level_raises_exactly_one_part(level, expected):
    updated, version = bumper.bumped(PYPROJECT, level)
    assert version == expected
    assert bumper.current(updated) == expected
    assert updated.replace(expected, "1.4.9") == PYPROJECT


def test_only_the_project_version_line_is_touched():
    text = PYPROJECT + '\n[tool.ruff]\ntarget-version = "py312"\nversion = "9.9.9"\n'
    updated, _ = bumper.bumped(text, "patch")
    assert 'target-version = "py312"' in updated
    assert updated.count('version = "9.9.9"') == 1


@pytest.mark.parametrize("level", ["", "build", "PATCH", "1"])
def test_an_unknown_level_changes_nothing(level):
    with pytest.raises(ValueError, match="level must be"):
        bumper.bumped(PYPROJECT, level)


def test_a_file_without_a_semver_line_is_refused():
    with pytest.raises(ValueError, match="semantic version"):
        bumper.current('[project]\nversion = "1.4"\n')


def test_the_repository_version_is_readable_and_is_what_the_package_reports():
    assert bumper.current((ROOT / "pyproject.toml").read_text()) == release()


async def test_status_names_the_running_release(store, config):
    handlers = BotHandlers(config, store)
    message = SimpleNamespace(text="/status", reply_text=AsyncMock())
    await handlers.handle(
        SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=42),
            effective_chat=SimpleNamespace(id=42, type="private"),
        ),
        SimpleNamespace(),
    )
    assert f"Version: {release()}" in message.reply_text.call_args.args[0]


def test_a_dispatched_run_only_deploys_when_the_box_is_ticked():
    assert (
        """    if: >-
      (github.event_name == 'push' && github.ref == 'refs/heads/main')
      || (github.event_name == 'workflow_dispatch' && inputs.deploy)
"""
        in CI
    )
    assert (
        """      deploy:
        description: Deploy this ref to production once the checks pass
        type: boolean
        default: false
"""
        in CI
    )
    assert "    needs: quality\n" in CI
    assert "    environment: production\n" in CI


def test_a_dispatched_release_tells_the_server_it_chose_the_ref():
    assert "DISPATCHED: ${{ github.event_name == 'workflow_dispatch' }}\n" in CI
    assert 'if [ "$DISPATCHED" = true ]; then command="$command manual"; fi\n' in CI
    assert '"$command"' in CI


def test_the_release_workflow_tags_main_and_never_deploys():
    assert "    if: github.ref == 'refs/heads/main'\n" in RELEASE
    assert "permissions:\n  contents: write\n" in RELEASE
    assert "git tag -a" in RELEASE and "bump_version.py" in RELEASE
    assert "docker" not in RELEASE and "ssh" not in RELEASE


def test_the_release_notes_describe_the_tag_that_was_just_pushed():
    assert 'gh release create "v$version" --verify-tag --generate-notes\n' in RELEASE
    assert "GH_TOKEN: ${{ github.token }}\n" in RELEASE
    push = RELEASE.index('git push origin HEAD:main "refs/tags/v$version"')
    assert push < RELEASE.index("gh release create")


def test_the_release_secret_stays_out_of_pull_requests():
    assert "PODCAST_DEPLOY_SSH_KEY" in CI
    assert "pull_request_target" not in CI
    assert "issue_comment" not in CI
    assert "pull_request_target" not in RELEASE and "issue_comment" not in RELEASE


@pytest.mark.parametrize(
    "command,sha,manual",
    [
        ("deploy " + "a" * 40, "a" * 40, False),
        ("deploy " + "b" * 40 + " manual", "b" * 40, True),
    ],
)
def test_the_forced_command_carries_the_deployment_mode(
    monkeypatch, tmp_path, command, sha, manual
):
    seen = []
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", command)
    monkeypatch.setattr(
        receiver, "deploy", lambda value, manual=False: seen.append((value, manual))
    )
    monkeypatch.setattr(receiver, "STATE", tmp_path / "state")
    receiver.main()
    assert seen == [(sha, manual)]


@pytest.mark.parametrize(
    "command",
    ["deploy " + "a" * 40 + " Manual", "deploy " + "a" * 40 + " manual extra", "deploy manual"],
)
def test_the_forced_command_still_refuses_anything_else(monkeypatch, command):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", command)
    with pytest.raises(RuntimeError):
        receiver.main()


def test_a_dispatched_release_ignores_the_main_check(tmp_path, monkeypatch):
    sha = "a" * 40
    monkeypatch.setattr(receiver, "STATE", tmp_path)
    monkeypatch.setattr(receiver, "DRAIN", tmp_path / "drain")
    monkeypatch.setattr(receiver.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(
        receiver, "current_main", lambda: pytest.fail("a dispatched release must not ask for main")
    )
    monkeypatch.setattr(
        receiver,
        "inspect_container",
        lambda: {"Image": "old-id", "Config": {"Healthcheck": {"Test": ["CMD", "probe"]}}},
    )
    monkeypatch.setattr(receiver, "backup", lambda _: None)
    monkeypatch.setattr(receiver, "compose_command", lambda _: ["docker", "compose"])
    monkeypatch.setattr(receiver, "drain", lambda: None)
    monkeypatch.setattr(receiver, "write_image", lambda _: None)
    monkeypatch.setattr(receiver, "wait_ready", lambda *a, **k: None)
    monkeypatch.setattr(
        receiver,
        "run",
        lambda args: (
            __import__("json")
            .dumps(
                [{"Id": "new-id", "Config": {"Labels": {"org.opencontainers.image.revision": sha}}}]
            )
            .encode()
        ),
    )
    monkeypatch.setattr(receiver.sys, "stdin", SimpleNamespace(buffer=None))
    receiver.deploy(sha, manual=True)
    assert (tmp_path / "current-sha").read_text() == sha + "\n"
