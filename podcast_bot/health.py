"""Local readiness probe; never calls Telegram or a paid API."""

import asyncio
import json
import time
from pathlib import Path

READY = Path("/tmp/podcast-bot-ready.json")


def healthy(path: Path = READY) -> bool:
    try:
        data = json.loads(path.read_text())
        return data["ready"] is True and 0 <= time.time() - data["time"] < 20
    except (OSError, ValueError, KeyError, TypeError):
        return False


async def monitor(app, worker, reader_ready) -> None:
    try:
        while True:
            ready = bool(
                app.running
                and app.updater
                and app.updater.running
                and worker.task
                and not worker.task.done()
                and reader_ready()
            )
            temp = READY.with_suffix(".tmp")
            temp.write_text(json.dumps({"ready": ready, "time": time.time()}))
            temp.replace(READY)
            await asyncio.sleep(5)
    finally:
        READY.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(0 if healthy() else 1)
