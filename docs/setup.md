# Setup and configuration

[Back to the README](../README.md)

## Setup

On macOS:

```sh
brew install python@3.12 ffmpeg
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock -e .
cp .env.example .env
chmod 600 .env
```

Create a bot using the verified [@BotFather](https://t.me/BotFather): send `/newbot` and follow its prompts. Put its token in `.env`. Open your new bot and send `/start`, then run:

```sh
python -m podcast_bot user-id
```

The helper prompts for your bot token without echoing it and displays private message sender IDs using Telegram's `getUpdates` API. Stop any running bot instance before using it. Set your own numeric ID as `TELEGRAM_ALLOWED_USER_ID`.

Create an [OpenAI API key](https://platform.openai.com/api-keys), enable API billing, and set `OPENAI_API_KEY`. Start with:

```sh
python -m podcast_bot
```

On Linux, install Python 3.12+ and ffmpeg using the system package manager. Docker is optional locally; the included Dockerfile/Compose service provides a server deployment:

```sh
mkdir -p data
# If the server user is not UID/GID 1000, set BOT_UID and BOT_GID in .env.
docker compose up -d --build
docker compose ps
```

Run one replica with a persistent `data/` volume. The service has automatic restart, non-root execution, and bounded logs. Its only incoming port is the Reader's, published on loopback for a TLS reverse proxy. Do not use ephemeral storage for SQLite/transcripts. Avoid printing resolved Compose configuration with secrets; `docker compose config --quiet` validates it safely.

## Configuration

Required:

```dotenv
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_ID=
OPENAI_API_KEY=
```

Study defaults:

```dotenv
STUDY_ENABLED=true
TARGET_LANGUAGE=zh
NATIVE_LANGUAGE=ru
LEARNER_LEVEL=HSK3
OPENAI_STUDY_MODEL=gpt-5.4-mini
VOCAB_TARGET_COUNT=20
MANDARIN_MOSAIC_SENTENCE_TARGET=20
STUDY_CHUNK_CHARS=3000
```

Both item counts are preferences, not quotas. Short or low-value episodes may yield fewer items, including none. The model is prompted to prioritize genuinely useful chunks and constructions rather than fill a beginner-word list. Safety ceilings prevent runaway output.

| Variable | Default / behavior |
| --- | --- |
| `OPENAI_TRANSCRIPTION_MODEL` | `gpt-transcribe`; also `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `whisper-1` |
| `DEFAULT_LANGUAGE` | Legacy source-language setting, `zh`; `TARGET_LANGUAGE` takes precedence when set |
| `MAX_EPISODE_DURATION_MINUTES` | `180`, validated before transcription; maximum configurable 1440 |
| `MAX_DOWNLOAD_MB` | `500` decimal MB, hard streaming limit; configurable 1–2000 |
| `CHUNK_SECONDS` | `600`, configurable 30–600; older GPT-4o models are capped at 180 |
| `DATA_DIR` | `data`; use persistent storage on a server |
| `TRANSCRIPTION_HINTS` | Optional, up to 500 characters |
| `ESTIMATED_COST_PER_MINUTE_USD` | Optional informational ASR estimate; blank disables it |
| `HANLY_CSV_COLUMNS` | Comma-separated list of the six default column names, allowing a different order |
| `STUDY_INPUT_USD_PER_MILLION` | Optional study pricing override |
| `STUDY_CACHED_INPUT_USD_PER_MILLION` | Optional cached-input pricing override |
| `STUDY_OUTPUT_USD_PER_MILLION` | Optional output pricing override |
| `READER_ENABLED` | `true`; `false` disables the Reader, its HTTP server and its commands |
| `READER_PUBLIC_URL` | Public HTTPS base URL of the Reader; blank hides every Open Reader button |
| `READER_HOST` | `127.0.0.1`; Compose sets `0.0.0.0` inside the container |
| `READER_PORT` | `8081` |
| `READER_DEV_MODE` | `false`; `true` accepts browser requests without Telegram init data |
| `READER_DICTIONARY` | Path to the generated CC-CEDICT database; defaults to one beside `reader/` |
| `READER_DICTIONARY_RU` | Optional path to a 大БКРС database; blank disables Russian glosses |
| `BKRS_DICTIONARY` | Host path mounted by `docker-compose.bkrs.yml` |

Set all three study price overrides together. Invalid/incomplete or unknown pricing simply disables the estimate; it does not block generation. Environment variables override `.env`. Persisted Telegram preferences override level/language defaults until changed again through commands. Queued jobs retain their study settings snapshot.

`/transcribe auto` uses RSS language, previous successful podcast language, then API detection. An explicit source-language override wins. HSK levels are for Chinese; use an appropriate level, such as B1, when switching languages. The app does not automatically convert HSK into CEFR.
