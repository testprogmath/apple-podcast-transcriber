"""The running release, so a deployed container can say what it is."""

from importlib.metadata import PackageNotFoundError, version

DISTRIBUTION = "personal-podcast-telegram-bot"


def release() -> str:
    try:
        return version(DISTRIBUTION)
    except PackageNotFoundError:
        return "unknown"
