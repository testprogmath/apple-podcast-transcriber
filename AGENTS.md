# Repository Guidelines

## Project Structure & Module Organization

`podcast_bot/` contains the Python application. `bot.py` handles Telegram commands; `queue.py` and `storage.py` manage persistent jobs and SQLite checkpoints. `resolver/` resolves podcast episodes, `transcription/` handles speech recognition, and `study/` generates learning materials. `hanly/` and `mosaic/` implement external integrations. Tests live in `tests/`; CI lives in `.github/workflows/`. Runtime transcripts, databases, and generated exports belong in ignored `data/`, not source control.

## Build, Test, and Development Commands

Use Python 3.12+ and install `ffmpeg` before running locally.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock -e '.[test]'
python -m podcast_bot          # Run the configured bot
python -m pytest -q            # Run the test suite
ruff check .                  # Lint, including import ordering
ruff format --check .         # Verify formatting
ruff format .                 # Apply formatting
```

`docker compose build` builds the container; `docker compose up -d --build` builds and starts the service. `requirements.txt` holds production pins; `requirements.lock` includes it as a compatibility wrapper.

## Coding Style & Naming Conventions

Use four-space indentation, type annotations, `snake_case` functions/modules, and `PascalCase` classes. Follow Ruff’s Python 3.12 configuration and 100-character formatting target. Keep Telegram handlers, generation logic, persistence, and integration clients separated. Return concise user-facing errors without exposing credentials or raw API responses.

## Testing Guidelines

Use pytest and pytest-asyncio; async tests run in automatic mode. Name files `test_*.py` and functions `test_*`. Mock HTTP, Telegram, and OpenAI calls; do not incur paid API usage in tests. Add regression tests for changed behavior, especially source fidelity, checkpoint reuse, retry handling, and upload idempotency. No numerical coverage threshold is configured. The required `Python quality and tests` CI check runs lint, formatting, and tests.

## Commit & Pull Request Guidelines

Use short imperative commit subjects, such as `Resolve study citations from canonical source segment IDs`. Open a PR against `main`; explain the problem, resulting behavior, and validation. Link related issues when applicable. Pass required checks before merging; do not bypass branch protection.

## Security & Architecture Constraints

Keep `.env`, tokens, authentication files, and runtime data untracked. Preserve canonical transcripts: citations come from source segment IDs, and ASR corrections remain suggestions. Only `zh` receives full study materials and external uploads. Other languages receive transcription and vocabulary. Deploy one replica with persistent storage; preserve configured Hanly mounts and avoid restarting during active jobs.
