import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..models import UserError


@dataclass(frozen=True)
class MosaicConfig:
    refresh_token: str = field(repr=False)
    refresh_token_id: int = field(repr=False)
    session_file: Path | None = field(default=None, repr=False)
    seed: str = field(default="", repr=False)

    def save_rotation(self, token: str, identifier: int) -> None:
        """Private configuration only: SQLite/artifacts never contain credentials."""
        if self.session_file is None:
            return
        temporary = None
        try:
            self.session_file.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(
                prefix=".mosaic-session-", dir=self.session_file.parent
            )
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    {"seed": self.seed, "refresh_token": token, "refresh_token_id": identifier},
                    stream,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.session_file)  # mkstemp creates mode 0600.
        except OSError:
            raise UserError(
                "Could not save Mandarin Mosaic rotated credentials. Check server data-directory permissions and use /mosaic again before restarting."
            ) from None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    @classmethod
    def from_env(cls, session_file: Path | None = None) -> "MosaicConfig | None":
        token = os.getenv("MANDARIN_MOSAIC_REFRESH_TOKEN", "")
        identifier = os.getenv("MANDARIN_MOSAIC_REFRESH_TOKEN_ID", "")
        if not token and not identifier:
            return None
        try:
            if not token or not identifier or int(identifier) < 0:
                raise ValueError
            seed = hashlib.sha256(json.dumps([token, int(identifier)]).encode()).hexdigest()
            if session_file and session_file.exists():
                data = json.loads(session_file.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError
                if data.get("seed") == seed:
                    rotated, rotated_id = data["refresh_token"], data["refresh_token_id"]
                    if (
                        not isinstance(rotated, str)
                        or not rotated
                        or type(rotated_id) is not int
                        or rotated_id < 0
                    ):
                        raise ValueError
                    return cls(rotated, rotated_id, session_file, seed)
            return cls(token, int(identifier), session_file, seed)
        except (OSError, KeyError, TypeError, ValueError):
            raise UserError(
                "Set both Mandarin Mosaic refresh credentials in server configuration."
            ) from None
