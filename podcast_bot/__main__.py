import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict
from getpass import getpass

from filelock import FileLock, Timeout

from .config import Config
from .diagnostics import configure_file_logging
from .models import UserError
from .net import http_client
from .resolver import resolve
from .storage import Storage


async def resolve_command(url: str) -> None:
    async with http_client() as client:
        episode = await resolve(url, client)
        print(json.dumps(asdict(episode), ensure_ascii=False, indent=2))


async def user_id_command() -> None:
    from telegram import Bot

    token = getpass("Bot token (hidden; not saved): ")
    print("Send a private message to your bot now. Stop any other bot process first.")
    async with Bot(token) as bot:
        updates = await bot.get_updates(timeout=30, allowed_updates=["message"])
        users = {
            (u.message.from_user.id, u.message.from_user.first_name)
            for u in updates
            if u.message and u.message.chat.type == "private"
        }
        if not users:
            print("No private messages found. Send /start to your bot and rerun this command.")
        for identifier, name in sorted(users):
            print(f"{name}: TELEGRAM_ALLOWED_USER_ID={identifier}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Private Apple Podcasts transcription bot")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("resolve", help="Resolve only; no audio download or paid API call").add_argument(
        "url"
    )
    sub.add_parser("user-id", help="Read your private Telegram user ID using a hidden token prompt")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # Telegram tokens appear in HTTP paths; never emit third-party HTTP diagnostics.
    for name in ("httpx", "httpcore", "openai", "telegram"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        if args.command == "resolve":
            asyncio.run(resolve_command(args.url))
        elif args.command == "user-id":
            asyncio.run(user_id_command())
        else:
            from .bot import build_application

            config = Config.from_env()
            config.data_dir.mkdir(parents=True, exist_ok=True)
            with FileLock(config.data_dir / "bot.lock", timeout=0):
                configure_file_logging(config.data_dir, (config.token, config.api_key))
                storage = Storage(config.data_dir)
                try:
                    build_application(config, storage).run_polling(allowed_updates=["message"])
                finally:
                    storage.close()
    except UserError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Timeout:
        print("Another bot process is already using this DATA_DIR.", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(
            f"Operation failed ({type(exc).__name__}). Check connectivity and configuration.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
