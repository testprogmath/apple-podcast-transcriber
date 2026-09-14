"""Optional post-study uploads; failures never invalidate expensive local results."""

import json
import logging
from pathlib import Path

from .models import UserError

log = logging.getLogger(__name__)


class StudyUploads:
    def __init__(self, hanly=None, mosaic=None, errors=()):
        self.hanly, self.mosaic, self.errors = hanly, mosaic, errors

    async def run(self, path: Path) -> str:
        metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        if (
            not metadata.get("study_complete")
            or metadata["study_settings"]["target_language"] != "zh"
        ):
            return ""
        messages = list(self.errors)
        for name, service, method, command in (
            ("Hanly", self.hanly, "upload", "/hanly"),
            ("Mandarin Mosaic", self.mosaic, "create_study_pack", "/mosaic"),
        ):
            if service is None:
                continue
            try:
                result = await getattr(service, method)(path)
                messages.append(result.message())
            except Exception as exc:
                log.error(
                    "operation=study-upload service=%s exception_type=%s", name, type(exc).__name__
                )
                detail = (
                    str(exc)
                    if isinstance(exc, UserError)
                    else f"Upload failed. Use {command} to retry."
                )
                messages.append(f"{name}: ⚠ {detail}\nTranscript and study files are saved.")
        return "\n\n".join(messages)
