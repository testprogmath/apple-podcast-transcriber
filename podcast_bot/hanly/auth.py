import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..models import UserError


class HanlyError(UserError):
    """Fixed, safe user-facing text. Never wrap remote response bodies."""


class AuthConfigError(HanlyError):
    pass


class RefreshError(HanlyError):
    pass


@dataclass
class HanlyAuth:
    path: Path
    api_key: str = field(repr=False)
    refresh_token: str = field(repr=False)
    extra: dict = field(repr=False)

    @classmethod
    def load(cls, path: Path) -> "HanlyAuth":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or data.get("project_id") != "hanzo-282fc"
                or any(
                    not isinstance(data.get(k), str) or not data[k].strip()
                    for k in ("api_key", "refresh_token")
                )
            ):
                raise ValueError
            path.chmod(0o600)
            return cls(path, data["api_key"], data["refresh_token"], data)
        except FileNotFoundError:
            raise AuthConfigError(
                "Hanly auth config is missing. Configure HANLY_AUTH_FILE on the server."
            ) from None
        except (OSError, ValueError, TypeError):
            raise AuthConfigError(
                "Hanly auth config is malformed or inaccessible. Check its fields and permissions."
            ) from None

    def persist(self) -> None:
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=".hanly-auth-", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({**self.extra, "refresh_token": self.refresh_token}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError:
            raise AuthConfigError(
                "Hanly could not save rotated credentials. Check auth-directory permissions; retry before restarting."
            ) from None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


def auth_path() -> Path:
    return Path(
        os.getenv("HANLY_AUTH_FILE") or "~/.config/podcast-telegram-bot/hanly-auth.json"
    ).expanduser()
