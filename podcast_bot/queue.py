import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path

from .diagnostics import diagnostic
from .models import Job, UserError
from .pipeline import Pipeline, completion
from .storage import Storage
from .study.pipeline import StudyFailure

log = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        storage: Storage,
        pipeline: Pipeline,
        status: Callable[[Job, str], Awaitable[None]],
        deliver: Callable[[Job, Path], Awaitable[None]],
        upload: Callable[[Path], Awaitable[str]] | None = None,
    ):
        self.storage, self.pipeline = storage, pipeline
        self.status, self.deliver = status, deliver
        self.upload = upload
        self.wake = asyncio.Event()
        self.task: asyncio.Task | None = None

    async def once(self) -> bool:
        job = self.storage.claim()
        if job is None:
            return False
        try:
            output = await self.pipeline.process(job, lambda text: self.status(job, text))
            summary = completion(output)
            if self.upload:
                try:
                    upload_status = await self.upload(output)
                except Exception:
                    log.error("job=%s stage=optional-upload-failed", job.id)
                    upload_status = (
                        "Optional upload failed. Files are saved; use /hanly or /mosaic to retry."
                    )
                if upload_status:
                    summary += "\n\n" + upload_status
            if (output / "manifest.json").is_file():
                await self.status(job, summary)
            await self.deliver(job, output)
            self.storage.finish(job.id, "completed")
            await self.status(job, summary)
            log.info("job=%s stage=completed", job.id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = (
                str(exc)
                if isinstance(exc, UserError)
                else "The job failed. Use /retry to try again; any completed transcript is cached."
            )
            self.storage.finish(job.id, "failed", message)
            if isinstance(exc, StudyFailure):
                try:
                    await self.deliver(job, exc.source)
                except Exception as delivery_error:
                    log.error(
                        "job=%s stage=canonical-delivery %s", job.id, diagnostic(delivery_error)
                    )
            # Raw third-party exception text can contain tokens or signed URLs.
            log.error("job=%s stage=failed %s", job.id, diagnostic(exc))
            try:
                await self.status(job, message)
            except Exception as notification_error:
                log.error(
                    "job=%s stage=notification exception_type=%s",
                    job.id,
                    type(notification_error).__name__,
                )
        return True

    async def run(self) -> None:
        while True:
            self.wake.clear()
            if not await self.once():
                with suppress(TimeoutError):
                    await asyncio.wait_for(self.wake.wait(), 5)

    def start(self) -> None:
        self.task = asyncio.create_task(self.run(), name="podcast-worker")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
