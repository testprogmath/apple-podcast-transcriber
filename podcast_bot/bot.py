import asyncio
import json
import logging
import os
import re
from contextlib import ExitStack, suppress
from dataclasses import replace
from pathlib import Path

from openai import AsyncOpenAI
from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    MenuButtonCommands,
    Update,
    WebAppInfo,
)
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Config, language_code
from .hanly import manual
from .hanly.auth import HanlyAuth, auth_path
from .hanly.client import HanlyClient
from .hanly.service import HanlyUploadService
from .integrations import StudyUploads
from .models import Job, UserError
from .mosaic.client import MandarinMosaicClient
from .mosaic.config import MosaicConfig
from .mosaic.service import MosaicUploadService
from .net import http_client
from .pipeline import Pipeline, cleanup_abandoned
from .queue import Worker
from .reader.api import ReaderApi
from .reader.documents import from_text, from_transcript
from .reader.server import ReaderServer
from .resolver.apple import parse_url
from .storage import Storage, request_key
from .study.client import OpenAIStudyClient, StudyRequests
from .study.pipeline import LearningPipeline
from .study.service import StudyService, pack_valid
from .study.settings import StudySettings, learner_level
from .transcription.audio import check_ffmpeg
from .transcription.openai import OpenAITranscriber
from .version import release

log = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 2_000_000
HELP = """Send an Apple Podcasts episode link. zh: full study pack and configured uploads.
Other languages: transcript and vocabulary/expressions only; no external uploads.
/level HSK3 — set learner level (also HSK4, A2, B1, etc.)
/language zh — default podcast language (zh, de, en, nl)
/native ru — language for translations and explanations
/regenerate [HSK4] — rebuild the latest study pack without speech-to-text
/reader — open the latest transcript in the interactive Reader
/hanly — merge selected vocabulary into the latest episode collection
/add_hanly 不知不觉 — add any Chinese word, phrase or sentence to Hanly as one card
/mosaic — upload the latest Chinese study sentences to Mandarin Mosaic
/zip — download the latest study pack archive
/transcribe nl <URL> — override language (zh, nl, en, auto)
/force <URL> — make a new paid transcription
/retry — resume the latest failed job (may retry an already billed request)
/status — current job and queue
/help — these instructions
Send Chinese text or a .txt/.md file to open it directly in the Reader.
Plain links use DEFAULT_LANGUAGE. Only your configured private Telegram account can use this bot."""


async def setup_command_menu(bot, chat_id: int) -> None:
    commands = [
        ("help", "Как пользоваться ботом"),
        ("status", "Текущая задача и очередь"),
        ("transcribe", "Транскрипция: /transcribe en ссылка"),
        ("language", "Язык подкаста: /language zh, en, de, nl"),
        ("level", "Уровень: /level HSK3 или B1"),
        ("native", "Язык объяснений: /native ru"),
        ("regenerate", "Обновить материалы из сохранённого текста"),
        ("zip", "Скачать последние материалы архивом"),
        ("reader", "Открыть интерактивную читалку"),
        ("hanly", "Слова в Hanly — только zh"),
        ("add_hanly", "Добавить своё слово или фразу в Hanly"),
        ("mosaic", "Предложения в Mandarin Mosaic — только zh"),
        ("retry", "Повторить неудавшуюся задачу"),
        ("force", "Новая платная транскрипция: /force ссылка"),
        ("start", "Начать и показать справку"),
    ]
    await bot.set_my_commands(
        [BotCommand(*c) for c in commands], scope=BotCommandScopeChat(chat_id)
    )
    await bot.set_chat_menu_button(chat_id=chat_id, menu_button=MenuButtonCommands())


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


def reader_markup(config: Config, identifier: str) -> InlineKeyboardMarkup | None:
    if not config.reader_enabled or not config.reader_url:
        return None
    url = f"{config.reader_url.rstrip('/')}/reader/?doc={identifier}"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📖 Open Reader", web_app=WebAppInfo(url=url))]]
    )


class BotHandlers:
    def __init__(self, config: Config, storage: Storage):
        self.config, self.storage = config, storage
        self.worker: Worker | None = None
        self.mosaic: MosaicUploadService | None = None
        self.mosaic_error: str | None = None
        self.hanly: HanlyUploadService | None = None
        self.hanly_error: str | None = None
        self.uploads = StudyUploads()
        self.reader: ReaderServer | None = None
        self.study_defaults = StudySettings.from_env()

    def study_settings(self) -> StudySettings:
        base = self.study_defaults
        return replace(
            base,
            target_language=self.storage.preference("target_language", base.target_language),
            native_language=self.storage.preference("native_language", base.native_language),
            learner_level=self.storage.preference("learner_level", base.learner_level),
        )

    def require_reader(self) -> None:
        if not self.config.reader_enabled:
            raise UserError("Reader is disabled in server configuration.")
        if not self.config.reader_url:
            raise UserError("Reader has no public URL. Set READER_PUBLIC_URL on the server.")

    async def open_reader(self, message, document) -> None:
        self.require_reader()
        self.storage.save_reader_document(document)
        count = len(document.sentences())
        await message.reply_text(
            f"📖 {document.title}\n{count} sentences ready. Tap words for Hanly, sentences for Mandarin Mosaic.",
            reply_markup=reader_markup(self.config, document.id),
        )

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not authorized(update, self.config.allowed_user_id) or not update.effective_message:
            return
        message = update.effective_message
        attachment = message.document
        try:
            self.require_reader()
            if not attachment or not (attachment.file_name or "").lower().endswith((".txt", ".md")):
                raise UserError("Reader accepts UTF-8 .txt or .md files.")
            if (attachment.file_size or 0) > MAX_UPLOAD_BYTES:
                raise UserError("That file is too large for Reader.")
            handle = await attachment.get_file()
            raw = bytes(await handle.download_as_bytearray())
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise UserError("Reader needs a UTF-8 encoded file.") from None
            document = from_text(
                update.effective_chat.id, text, title=attachment.file_name, source_type="file"
            )
            await self.open_reader(message, document)
        except UserError as exc:
            await message.reply_text(str(exc))

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
                + f"\nVersion: {release()}"
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
            if command == "/hanly":
                if self.hanly is None:
                    raise UserError(
                        self.hanly_error
                        or "Hanly auth config is missing. Set HANLY_AUTH_FILE on the server."
                    )
                pack = self.storage.recent_pack(update.effective_chat.id)
                if not pack:
                    raise UserError("No complete study pack yet. Send a link or use /regenerate.")
                status = await message.reply_text("Updating Hanly collection…")
                try:
                    result = await self.hanly.upload(pack)
                    await status.edit_text(result.message())
                except UserError as exc:
                    await status.edit_text(str(exc))
                return
            if command == "/add_hanly":
                glyph = manual.parse_glyph(text)
                if self.hanly is None:
                    raise UserError(
                        self.hanly_error
                        or "Hanly auth config is missing. Set HANLY_AUTH_FILE on the server."
                    )
                status = await message.reply_text("Adding to Hanly…")
                try:
                    result = await self.hanly.add_manual_glyph(glyph)
                    await status.edit_text(result.message())
                except UserError as exc:
                    await status.edit_text(str(exc))
                except Exception as exc:
                    log.error("operation=add-hanly exception_type=%s", type(exc).__name__)
                    await status.edit_text("Could not add this to Hanly. Please try again.")
                return
            if command == "/mosaic":
                if self.mosaic is None:
                    raise UserError(
                        self.mosaic_error
                        or "Mandarin Mosaic upload is not configured. Set both refresh credentials on the server."
                    )
                pack = self.storage.recent_pack(update.effective_chat.id)
                if not pack:
                    raise UserError("No complete study pack yet. Send a link or use /regenerate.")
                status = await message.reply_text(
                    "Uploading selected sentences to Mandarin Mosaic…"
                )
                try:
                    result = await self.mosaic.create_study_pack(pack)
                    await status.edit_text(result.message())
                except UserError as exc:
                    await status.edit_text(str(exc))
                return
            if command == "/zip":
                pack = self.storage.recent_pack(update.effective_chat.id)
                if not pack or not pack_valid(pack):
                    raise UserError("No complete study pack yet. Send a link or use /regenerate.")
                metadata = json.loads((pack / "metadata.json").read_text(encoding="utf-8"))
                if (
                    metadata["study_settings"]["target_language"] != "zh"
                    and metadata.get("pack_format") != "vocabulary-only-v1"
                ):
                    raise UserError(
                        "Use /regenerate to prepare the new vocabulary-only materials first."
                    )
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
            if command == "/reader":
                self.require_reader()
                source = self.storage.recent_pack(
                    update.effective_chat.id
                ) or self.storage.recent_source(update.effective_chat.id)
                if source is None:
                    raise UserError(
                        "No saved transcript yet. Send an episode link, Chinese text, or a .txt file."
                    )
                await self.open_reader(message, from_transcript(update.effective_chat.id, source))
                return
            if not command and not re.search(r"https://podcasts\.apple\.com/", text):
                await self.open_reader(message, from_text(update.effective_chat.id, text.strip()))
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
                    upload_status = await self.uploads.run(pack)
                    await message.reply_text(
                        "Study pack already prepared."
                        + ("\n\n" + upload_status if upload_status else "")
                    )
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
        if metadata["study_settings"]["target_language"] == "zh":
            names += [
                f"translation_{metadata['study_settings']['native_language']}.md",
                "reader.md",
                "study.md",
                "hanly.csv",
                "metadata.json",
            ]
        else:
            names.append("vocabulary.md")
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
        if not config.reader_enabled or not config.reader_url:
            return
        try:
            document = from_transcript(job.chat_id, path)
            storage.save_reader_document(document)
            await app.bot.send_message(
                chat_id=job.chat_id,
                text=f"📖 {document.title}",
                reply_markup=reader_markup(config, document.id),
            )
        except (UserError, TelegramError):
            log.warning("job=%s stage=reader-document-unavailable", job.id)

    async def start(app: Application) -> None:
        try:
            await setup_command_menu(app.bot, config.allowed_user_id)
        except TelegramError:
            log.warning("stage=command-menu-setup-failed")
        check_ffmpeg()
        cleanup_abandoned(storage)
        storage.recover()
        client = http_client()
        # Automatic SDK retries are disabled to avoid blind duplicate paid requests.
        openai = AsyncOpenAI(api_key=config.api_key, max_retries=0, timeout=900)
        app.bot_data.update(http=client, openai=openai)
        try:
            mosaic_config = MosaicConfig.from_env(config.data_dir / "mosaic-session.json")
            if mosaic_config:
                handlers.mosaic = MosaicUploadService(
                    storage, MandarinMosaicClient(client, mosaic_config)
                )
        except UserError as exc:
            # Optional upload configuration must not disable transcription or study jobs.
            handlers.mosaic_error = str(exc)
        try:
            path = auth_path()
            if path.exists() or os.getenv("HANLY_AUTH_FILE"):
                from .hanly.cards import ManualCards
                from .reader.bkrs import RussianDictionary

                cards = ManualCards(
                    storage,
                    OpenAIStudyClient(openai) if config.study_enabled else None,
                    RussianDictionary(),
                    handlers.study_settings().model,
                )
                handlers.hanly = HanlyUploadService(
                    storage, HanlyClient(client, HanlyAuth.load(path)), cards
                )
        except UserError as exc:
            handlers.hanly_error = str(exc)
        if os.getenv("STUDY_AUTO_UPLOAD", "true").lower() not in {"false", "0", "no"}:
            handlers.uploads = StudyUploads(
                handlers.hanly,
                handlers.mosaic,
                [e for e in (handlers.hanly_error, handlers.mosaic_error) if e],
            )
        pipeline = Pipeline(config, storage, client, OpenAITranscriber(openai))
        if config.study_enabled:
            pipeline = LearningPipeline(
                pipeline,
                StudyService(storage, StudyRequests(storage, OpenAIStudyClient(openai))),
                storage,
                handlers.study_settings(),
            )
        handlers.worker = Worker(storage, pipeline, status, deliver, upload=handlers.uploads.run)
        handlers.worker.start()
        if config.reader_enabled and config.reader_url:
            from .reader.translations import SentenceTranslations

            api = ReaderApi(
                config,
                storage,
                handlers,
                dev_mode=config.reader_dev_mode,
                translations=SentenceTranslations(storage, OpenAIStudyClient(openai)),
            )
            handlers.reader = ReaderServer(api, config.reader_host, config.reader_port)
            try:
                await handlers.reader.start()
            except OSError:
                handlers.reader = None
                log.error("stage=reader-server-bind-failed port=%s", config.reader_port)

        from .health import monitor

        app.bot_data["health_task"] = asyncio.create_task(
            monitor(
                app,
                handlers.worker,
                lambda: (
                    not (config.reader_enabled and config.reader_url) or handlers.reader is not None
                ),
            ),
            name="readiness-monitor",
        )

    async def stop(app: Application) -> None:
        if task := app.bot_data.pop("health_task", None):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if handlers.reader:
            await handlers.reader.stop()
            handlers.reader = None
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
        "mosaic",
        "hanly",
        "add_hanly",
        "reader",
    ):
        app.add_handler(CommandHandler(command, handlers.handle))
    app.add_handler(MessageHandler(filters.TEXT, handlers.handle))
    app.add_handler(MessageHandler(filters.Document.ALL, handlers.handle_document))
    app.add_error_handler(error)
    return app
