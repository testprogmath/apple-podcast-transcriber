# Apple Podcast Transcriber & Study Bot

A private, single-user Telegram bot: send an Apple Podcasts episode URL to get a faithful transcript and a curated language-learning pack. Python 3.12+, SQLite, ffmpeg, the official OpenAI Python SDK, and Telegram long polling. No web frontend or external queue.

## Study pack

For Mandarin with Russian as the learner's native language, the bot sends these individual files as one Telegram document group:

| File | Contents |
| --- | --- |
| `transcript.txt` | Canonical ASR transcript, unchanged by study generation |
| `reader.md` | Chinese + word-grouped tone-mark pinyin + Russian paragraph translation |
| `transcript_pinyin.md` | Separate Chinese/pinyin reading version |
| `translation_ru.md` | Separate natural, learning-friendly Russian translation |
| `study.md` | Curated vocabulary, expressions, patterns, pragmatics, cultural notes, possible ASR issues |
| `hanly.csv` | Selected words and expressions; default columns `Chinese,Pinyin,Russian,Example,ExampleTranslation,Tags` |
| `mandarin_mosaic.csv` | Selected verbatim Chinese sentences with English translations; exactly `Chinese,English` |
| `metadata.json` | Source, models, settings, counts, transcript hash, and documented ASR suggestions |

A ZIP containing these files plus validated `study.json` is created automatically. `/zip` sends the most recently completed archive. Whisper SRT, when available, is also preserved and delivered.

Chinese speech recognition does not translate. The text-processing stage derives all learning files from the saved transcript. It never rewrites that source, even when it suspects an ASR error. The current correction policy is deliberately **suggestions only**; confidence and reasons appear in study notes and metadata, with no silent replacement in quotations or Mosaic sentences.

For other source languages, reading and translation/study files are produced without Chinese pinyin or Mandarin Mosaic. Translation filenames follow the native-language setting, e.g. `translation_en.md`. Hanly keeps its configured column labels for importer compatibility; the meaning and example-translation values follow `NATIVE_LANGUAGE`.

## Telegram commands

```text
<Apple Podcasts episode URL>
/transcribe de <URL>
/transcribe en <URL>
/transcribe nl <URL>
/transcribe auto <URL>
/level HSK3
/level HSK4
/language zh
/native ru
/regenerate
/regenerate HSK4
/zip
/status
/retry
/force <URL>
/help
/start
```

- A plain URL uses the configured source language and current study preferences.
- `/level`, `/language`, and `/native` persist preferences in SQLite across restarts. Levels can be HSK3, HSK4, A2, B1, etc.; they are not hardcoded to Mandarin.
- `/regenerate` makes a **new text-model run** from the most recent canonical transcript. It never calls speech recognition or downloads audio. The optional level applies to that run; `/level` changes the lasting preference.
- Re-sending a URL reuses the transcript and matching study pack. After a level/native-language change it creates only new study materials.
- `/force <URL>` intentionally makes a new paid audio transcription. `/retry` resumes a failed job, reusing completed stage checkpoints.
- `/zip` retrieves the latest completed pack, even if a newer job is still processing.
- Long jobs update one status message. Study failures leave the transcript usable and deliver it on its own, with an explanation and `/retry` guidance.

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

Run one replica with a persistent `data/` volume. The service has automatic restart, non-root execution, bounded logs, and no incoming port. Do not use ephemeral storage for SQLite/transcripts. Avoid printing resolved Compose configuration with secrets; `docker compose config --quiet` validates it safely.

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

Set all three study price overrides together. Invalid/incomplete or unknown pricing simply disables the estimate; it does not block generation. Environment variables override `.env`. Persisted Telegram preferences override level/language defaults until changed again through commands. Queued jobs retain their study settings snapshot.

`/transcribe auto` uses RSS language, previous successful podcast language, then API detection. An explicit source-language override wins. HSK levels are for Chinese; use an appropriate level, such as B1, when switching languages. The app does not automatically convert HSK into CEFR.

## How resolution and transcription work

The resolver parses the Apple show ID in the path and the separate episode ID in `i=`. It queries Apple's public show lookup with `entity=podcastEpisode&limit=200`, verifies both episode and collection IDs, fetches `feedUrl`, and matches the exact RSS GUID. Exact enclosure URL matching is the fallback. If the episode is missing, direct episode lookup and then the US storefront are attempted. It never guesses by title or assumes the Apple ID equals the RSS GUID.

The example resolves to **大鹏说中文 - Speak Chinese with Da Peng**, GUID `Buzzsprout-19793450`, reported duration **889 seconds (14:49)**, MIME `audio/mpeg`. See `resolver-example.json`.

```sh
python -m podcast_bot resolve "https://podcasts.apple.com/nl/podcast/id1490732024?i=1000789324203"
```

This CLI fetches only public metadata, with no credentials, audio download, or paid call.

Audio downloads stream in 64 KiB blocks, checking status, MIME, size, empty/truncated bodies, and duration. ffmpeg fully decodes before charging. Inputs must be below a conservative 24 MB upload ceiling; larger/longer audio is split near silence. Suitable MP3s are stream-copied; conversion uses mono 24 kHz, 64 kbps MP3 only when needed. Chunks are sequential, text is joined without removing deliberate repetition, and SRT times are offset.

Temporary directories delete originals/chunks on success, failure, or cancellation. On the next startup, abandoned job directories from an uncatchable kill are removed under the single-process lock. No original audio is retained. Cleanup happens as soon as ASR completes, before the text-only study stage.

## Study validation and long transcripts

The official Responses API returns structured Pydantic-validated JSON; Python renders Markdown/CSV/ZIP deterministically. Model output is never interpreted as an arbitrary CSV or Markdown document.

Transcripts are split at paragraph/sentence boundaries into blocks of at most 1200 characters and bounded request batches (default 3000 characters). A hard 300,000-character transcript safety limit prevents an uncontrolled request count. Extremely long unpunctuated spans may need a hard character boundary.

Each study response must cover source blocks exactly once, in order, preserving every Chinese source character apart from reading-line whitespace. Source examples must occur in the canonical transcript. Global ranking over bounded candidate batches removes duplicate/overlapping vocabulary and picks the strongest episode-wide teaching points. It treats Mosaic sentences as a separate candidate category from vocabulary.

For Mandarin Mosaic:

- Every Chinese sentence must be a verbatim source substring. No paraphrase, simplification, correction, fabricated example, or joining of separated source spans is accepted.
- The model selects self-contained useful utterances and natural constructions; 8–35 characters is guidance, not a truncation rule.
- English is used regardless of `NATIVE_LANGUAGE`. No pinyin, Russian, reasons, tags, or timestamps enter the CSV.
- Deterministic filtering removes obvious support/advertising material and repeated listening sentences. Punctuation-only repetitions and harmless interjection variants are deduplicated; global model ranking handles semantic overlap.
- Reasons are retained in `study.json`. Timestamp fields remain null because the study stage does not invent audio alignment.

## Caching, persistence, and billing

SQLite holds the queue, preferences, canonical-cache pointers, independent study-cache pointers, both stages' usage, and validated step checkpoints.

The ASR cache key uses stable Apple show/episode IDs, speech model, requested language, hints, and pipeline version. Study keys use the canonical transcript hash, text model, target/native languages, learner level, item targets, rendering settings, chunk budget, and prompt version. Changing HSK3 to HSK4 or ru to en does not invalidate ASR. Forced study generations use a new run namespace and replace the latest study-cache pointer only after success.

Completed files are published atomically with a hash manifest. A missing/corrupted derived file is rebuilt from validated checkpoints without another text-model call when possible. Study packs have their own immutable transcript copy; the original canonical file is never rewritten. Old packs remain available on disk after regeneration.

On restart, running jobs return to the queue. Completed ASR chunks and study requests are reused. An in-flight request with an unknown outcome requires explicit `/retry`, because it may already have been billed. Automatic SDK retries are disabled. Exactly-once billing cannot be guaranteed after a lost response or a crash. Failed delivery retries reuse the finished files.

Usage is separated in SQLite:

- `usage`: ASR duration, model, timestamp, outcome, optional token counts and estimated cost.
- `study_usage`: stage step, model, input characters/tokens, cached input tokens, output tokens, timestamp, outcome, estimated cost when known.

Prices checked on 2026-09-13: `gpt-transcribe` is listed at $0.0045/audio minute; 15/30/60 minutes cost approximately $0.0675/$0.135/$0.27 for one successful pass. The selected configurable study model, `gpt-5.4-mini`, is listed at $0.75/million input tokens, $0.075/million cached input tokens, and $4.50/million output tokens. Study costs depend on transcript and output length; no fixed episode price is promised. Estimates exclude taxes, regional uplift, hosting, and additional failed/repeated requests.

## Security and limitations

Only the configured Telegram user ID in a private chat is accepted. Other users are ignored before processing. Environment credentials, token files, local audio, data, and deployment-specific notes are excluded from Git; credentials/audio are excluded from Docker build context. Never commit `.env` or token files.

Public HTTP/redirect checks, bounded downloads, local-only ffmpeg protocols, safe filenames, secret-redacted logging, and a process lock protect this personal bot. This is not a hardened multitenant fetch service; DNS resolution is not pinned against rebinding.

Apple may omit older episodes outside the latest 200, and RSS may remove entries. Subscriber-only/DRM content is unsupported. The default ASR model has no native subtitle output; Whisper offers SRT with an accuracy tradeoff. Pinyin, sandhi, translation quality, sentence completeness, and pedagogical usefulness remain model judgments even though schema/source/order checks are enforced. Possible ASR mistakes are documented, not automatically fixed. Hanly/Mosaic exports implement the requested CSV schemas; their live import UIs have not been tested.

## Development and validation

```sh
python -m pip install -r requirements.lock -e '.[test]'
python -m pytest -q
ruff check .
python -m compileall -q podcast_bot
```

Tests block unexpected networking and mock OpenAI/Telegram APIs. Real ffmpeg tests use generated local audio. See `VALIDATION.md` for results and validation boundaries. No paid call is required to run tests.

Modules: `resolver/`, `transcription/`, `study/` (typed schemas, source validation/chunking, prompts, API adapter/checkpoints, selection, renderers), plus `pipeline.py`, `storage.py`, `queue.py`, and `bot.py`. Additional derived outputs can be added within `study/` without changing speech recognition.

## Documentation sources

- [Apple public Search API](https://performance-partners.apple.com/search-api)
- [OpenAI file transcription](https://developers.openai.com/api/docs/guides/speech-to-text)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [GPT-5.4 mini model and pricing](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
- [OpenAI pricing](https://developers.openai.com/api/docs/pricing)
- [Telegram BotFather](https://core.telegram.org/bots/features#botfather)
