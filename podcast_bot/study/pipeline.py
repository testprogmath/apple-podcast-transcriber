import json
from dataclasses import replace
from pathlib import Path

from ..models import Job, UserError
from ..pipeline import Pipeline, Status
from ..storage import Storage
from .service import StudyService
from .settings import StudySettings


class StudyFailure(UserError):
    def __init__(self, source: Path, message: str):
        super().__init__("Transcript saved. " + message)
        self.source = source


class LearningPipeline:
    def __init__(
        self,
        transcription: Pipeline,
        study: StudyService,
        storage: Storage,
        defaults: StudySettings,
    ):
        self.transcription, self.study, self.storage, self.defaults = (
            transcription,
            study,
            storage,
            defaults,
        )

    async def process(self, job: Job, status: Status) -> Path:
        if job.kind == "study":
            if not job.source_path or not (Path(job.source_path) / "transcript.txt").is_file():
                raise UserError(
                    "The saved transcript is missing. Study regeneration will not call speech-to-text."
                )
            source = Path(job.source_path)
        else:
            source = await self.transcription.process(job, status)
        self.storage.remember_source(job.chat_id, source)
        with self.storage.db:
            self.storage.db.execute(
                "UPDATE jobs SET output_path=? WHERE id=?", (str(source), job.id)
            )
        settings = (
            StudySettings.from_json(job.study_settings) if job.study_settings else self.defaults
        )
        metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
        settings = replace(
            settings, target_language=metadata.get("language") or settings.target_language
        )
        try:
            pack = await self.study.generate(
                source, settings, job, status, regenerate=job.kind == "study" and job.force
            )
        except Exception as error:
            message = (
                str(error)
                if isinstance(error, UserError)
                else "Study generation failed. Use /retry to resume without transcribing again."
            )
            raise StudyFailure(source, message) from error
        self.storage.remember_pack(job.chat_id, source, pack)
        return pack
