import json
import logging
import re
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from openai import AsyncOpenAI
from telegram import InputMediaDocument, Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Config, language_code
from .models import Job, UserError
from .net import http_client
from .pipeline import Pipeline, cleanup_abandoned
from .queue import Worker
from .resolver.apple import parse_url
from .storage import Storage, request_key
from .study.client import OpenAIStudyClient, StudyRequests
from .study.pipeline import LearningPipeline
from .study.service import StudyService, pack_valid
from .study.settings import StudySettings, learner_level
from .transcription.audio import check_ffmpeg
from .transcription.openai import OpenAITranscriber

log = logging.getLogger(__name__)
HELP = """Send an Apple Podcasts episode link to receive a transcript and study pack.
/level HSK3 — set learner level (also HSK4, A2, B1, etc.)
/language zh — default podcast language (zh, de, en, nl)
/native ru — language for translations and explanations
/regenerate [HSK4] — rebuild the latest study pack without speech-to-text
/zip — download the latest study pack archive
/transcribe nl <URL> — override language (zh, nl, en, auto)
/force <URL> — make a new paid transcription
/retry — resume the latest failed job (may retry an already billed request)
/status — current job and queue
/help — these instructions
Plain links use DEFAULT_LANGUAGE. Only your configured private Telegram account can use this bot."""


def authorized(update: Update, allowed_user_id: int) -> bool:
    return bool(
        update.effective_user
        and update.effective_user.id == allowed_user_id
        and update.effective_chat
        and update.effective_chat.type == "private"
    )


def extract_url(text: str) -> str:
    matches = re.findall(r"https://podcasts\.apple\.com/[^\s<>]+", text)
    if len(matches) != 1:
        raise UserError("Send one Apple Podcasts episode URL at a time.")
    url = matches[0].rstrip(".,!")
    return parse_url(url).url


class BotHandlers:
    def __init__(self, config: Config, storage: Storage):
        self.config, self.storage = config, storage
        self.worker: Worker | None = None
        self.study_defaults = StudySettings.from_env()

    def study_settings(self) -> StudySettings:
        base = self.study_defaults
        return replace(
            base,
            target_language=self.storage.preference("target_language", base.target_language),
            native_language=self.storage.preference("native_language", base.native_language),
            learner_level=self.storage.preference("learner_level", base.learner_level),
        )

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not authorized(update, self.config.allowed_user_id) or not update.effective_message:
            return
        message = update.effective_message
        text = message.text or ""
        command = text.split()[0].split("@")[0] if text.startswith("/") else ""
        if command in {"/start", "/help"}:
            await message.reply_text(HELP)
            return
        if command == "/status":
            settings = self.study_settings()
            await message.reply_text(
                self.storage.status()
                + f"\nLearning: {settings.target_language} → {settings.native_language}; {settings.learner_level}"
            )
            return
        if command == "/retry":
            status = await message.reply_text("Checking failed jobs…")
            identifier = self.storage.retry(update.effective_chat.id, status.message_id)
            await status.edit_text(
                f"Added job #{identifier} to queue."
                if identifier
                else "No failed job available to retry."
            )
            if self.worker:
                self.worker.wake.set()
            return
        try:
            settings = self.study_settings()
            if command in {"/level", "/language", "/native"}:
                fields = text.split(maxsplit=1)
                if len(fields) != 2:
                    raise UserError("Use /level HSK3, /language zh, or /native ru.")
                field = {
                    "/level": "learner_level",
                    "/language": "target_language",
                    "/native": "native_language",
                }[command]
                value = (
                    learner_level(fields[1])
                    if field == "learner_level"
                    else language_code(fields[1])
                )
                if not value:
                    raise UserError("Choose an explicit two-letter language for this setting.")
                replace(settings, **{field: value})
                self.storage.set_preference(field, value)
                await message.reply_text(
                    f"Saved: {field} = {value}. Existing transcripts are unchanged. Use /regenerate for new study materials."
                )
                return
            if command == "/zip":
                pack = self.storage.recent_pack(update.effective_chat.id)
                if not pack or not pack_valid(pack):
                    raise UserError("No complete study pack yet. Send a link or use /regenerate.")
                metadata = json.loads((pack / "metadata.json").read_text(encoding="utf-8"))
                with (pack / metadata["zip_filename"]).open("rb") as document:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=document,
                        filename=metadata["zip_filename"],
                    )
                return
            if command == "/regenerate":
                if not self.config.study_enabled:
                    raise UserError("Study generation is disabled in server configuration.")
                source = self.storage.recent_source(update.effective_chat.id)
                if source is None:
                    raise UserError("No saved transcript yet. Send an episode link first.")
                fields = text.split(maxsplit=1)
                if len(fields) == 2:
                    settings = replace(settings, learner_level=learner_level(fields[1]))
                metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
                settings = replace(
                    settings, target_language=metadata.get("language") or settings.target_language
                )
                status = await message.reply_text(
                    "Preparing study regeneration from the saved transcript…"
                )
                identifier, position, duplicate = self.storage.enqueue_study(
                    source,
                    settings.to_json(),
                    update.effective_chat.id,
                    status.message_id,
                    regenerate=True,
                )
                await status.edit_text(
                    f"Added study job #{identifier} to queue. Position: {position}. No audio transcription."
                )
                if self.worker:
                    self.worker.wake.set()
                return
            language = self.storage.preference(
                "target_language", self.config.default_language or "auto"
            )
            language = language_code(language)
            if command == "/transcribe":
                fields = text.split(maxsplit=2)
                if len(fields) != 3:
                    raise UserError("Use /transcribe nl <URL> or /transcribe auto <URL>.")
                # Explicit auto bypasses the configured default, then uses feed/history/detection.
                language = language_code(fields[1])
            elif command and command != "/force":
                raise UserError("Unknown command. Use /help.")
            url = extract_url(text)
            key = request_key(url, self.config.model, language, self.config.hints)
            if language:
                settings = replace(settings, target_language=language)
            cached = self.storage.cached(key) if command != "/force" else None
            if cached and self.config.study_enabled:
                metadata = json.loads((cached / "metadata.json").read_text(encoding="utf-8"))
                settings = replace(
                    settings, target_language=metadata.get("language") or settings.target_language
                )
                service = StudyService(self.storage, None)
                pack = service.cached(cached, settings)
                if pack:
                    self.storage.remember_pack(update.effective_chat.id, cached, pack)
                    await message.reply_text("Study pack already prepared.")
                    await send_files(context.bot, update.effective_chat.id, pack)
                    return
                status = await message.reply_text("Already transcribed. Preparing study materials…")
                identifier, position, duplicate = self.storage.enqueue_study(
                    cached, settings.to_json(), update.effective_chat.id, status.message_id
                )
                await status.edit_text(
                    f"Added study job #{identifier} to queue. Position: {position}."
                )
                if self.worker:
                    self.worker.wake.set()
                return
            if cached:
                await message.reply_text("Already transcribed.")
                await send_files(context.bot, update.effective_chat.id, cached)
                return
            status = await message.reply_text("Added to queue…")
            identifier, position, duplicate = self.storage.enqueue(
                url,
                language,
                self.config.model,
                self.config.hints,
                command == "/force",
                update.effective_chat.id,
                status.message_id,
                study_settings=settings.to_json() if self.config.study_enabled else None,
            )
            await status.edit_text(
                f"{'Already queued' if duplicate else 'Added to queue'}.\nPosition: {position}\nJob: #{identifier}"
            )
            if self.worker:
                self.worker.wake.set()
        except UserError as exc:
            await message.reply_text(str(exc))


async def send_files(bot, chat_id: int, path: Path) -> None:
    metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("study_complete"):
        names = ["transcript.txt"]
        if metadata["study_settings"]["target_language"] == "zh":
            names.extend(["transcript_pinyin.md", "mandarin_mosaic.csv"])
        names += [
            f"translation_{metadata['study_settings']['native_language']}.md",
            "reader.md",
            "study.md",
            "hanly.csv",
            "metadata.json",
        ]
        if (path / "transcript.srt").is_file():
            names.append("transcript.srt")
    else:
        names = [name for name in ("transcript.txt", "transcript.srt") if (path / name).is_file()]
    if metadata.get("study_complete"):
        with ExitStack() as stack:
            documents = [
                InputMediaDocument(
                    media=stack.enter_context((path / name).open("rb")), filename=name
                )
                for name in names
            ]
            await bot.send_media_group(chat_id=chat_id, media=documents)
        return
    for name in names:
        with (path / name).open("rb") as document:
            await bot.send_document(chat_id=chat_id, document=document, filename=name)


def build_application(config: Config, storage: Storage) -> Application:
    handlers = BotHandlers(config, storage)

    async def status(job: Job, text: str) -> None:
        try:
            await app.bot.edit_message_text(
                chat_id=job.chat_id, message_id=job.message_id, text=text[:4000]
            )
        except TelegramError:
            log.warning("job=%s stage=status-update-failed", job.id)

    async def deliver(job: Job, path: Path) -> None:
        await send_files(app.bot, job.chat_id, path)

    async def start(app: Application) -> None:
        check_ffmpeg()
        cleanup_abandoned(storage)
        storage.recover()
        client = http_client()
        # Automatic SDK retries are disabled to avoid blind duplicate paid requests.
        openai = AsyncOpenAI(api_key=config.api_key, max_retries=0, timeout=900)
        app.bot_data.update(http=client, openai=openai)
        pipeline = Pipeline(config, storage, client, OpenAITranscriber(openai))
        if config.study_enabled:
            pipeline = LearningPipeline(
                pipeline,
                StudyService(storage, StudyRequests(storage, OpenAIStudyClient(openai))),
                storage,
                handlers.study_settings(),
            )
        handlers.worker = Worker(storage, pipeline, status, deliver)
        handlers.worker.start()

    async def stop(app: Application) -> None:
        if handlers.worker:
            await handlers.worker.stop()
        if client := app.bot_data.get("http"):
            await client.aclose()
        if client := app.bot_data.get("openai"):
            await client.close()

    async def error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("stage=telegram-handler exception_type=%s", type(context.error).__name__)

    app = (
        Application.builder()
        .token(config.token)
        .concurrent_updates(False)
        .post_init(start)
        .post_stop(stop)
        .post_shutdown(stop)
        .build()
    )
    for command in (
        "start",
        "help",
        "status",
        "retry",
        "force",
        "transcribe",
        "level",
        "language",
        "native",
        "regenerate",
        "zip",
    ):
        app.add_handler(CommandHandler(command, handlers.handle))
    app.add_handler(MessageHandler(filters.TEXT, handlers.handle))
    app.add_error_handler(error)
    return app
