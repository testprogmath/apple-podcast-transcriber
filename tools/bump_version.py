#!/usr/bin/env python3
"""Read or bump the single source of truth for the project version.

`pyproject.toml` carries the version; the git tag and the released image label follow it.
Standalone on purpose: no third-party packages and no imports from this project, so the
release workflow can run it before anything is installed.

Usage:  bump_version.py --show
        bump_version.py major|minor|patch
Prints the resulting version.
"""

import re
import sys
from pathlib import Path

PATTERN = re.compile(r'^version = "(\d+)\.(\d+)\.(\d+)"$', re.M)
LEVELS = ("major", "minor", "patch")


def current(text: str) -> str:
    match = PATTERN.search(text)
    if match is None:
        raise ValueError("pyproject.toml has no semantic version line")
    return ".".join(match.groups())


def bumped(text: str, level: str) -> tuple[str, str]:
    if level not in LEVELS:
        raise ValueError("level must be one of " + ", ".join(LEVELS))
    major, minor, patch = (int(part) for part in current(text).split("."))
    if level == "major":
        major, minor, patch = major + 1, 0, 0
    elif level == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    version = f"{major}.{minor}.{patch}"
    return PATTERN.sub(f'version = "{version}"', text, count=1), version


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    path = Path("pyproject.toml")
    text = path.read_text(encoding="utf-8")
    try:
        if argv[1] == "--show":
            print(current(text))
            return 0
        updated, version = bumped(text, argv[1])
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    path.write_text(updated, encoding="utf-8")
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
