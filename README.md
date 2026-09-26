# Apple Podcast Transcriber & Study Bot

A private, single-user Telegram bot. Send it an Apple Podcasts episode link and it returns a faithful transcript. For Mandarin it also builds a validated study pack, and an interactive Reader lets you pick words and sentences from any transcript or Chinese text and send them to two flashcard apps, Hanly and Mandarin Mosaic.

It is built for one person: a Mandarin learner whose native language is Russian. Only the configured Telegram user ID in a private chat is served. Other source languages get a transcript and a vocabulary list, nothing more.

Python 3.12+, SQLite, ffmpeg, the official OpenAI Python SDK, Telegram long polling, and a small static Mini App served by the same process. No external queue, no Node build. CI tests every change and deploys `main` to a single server.

## Pipeline

```text
Apple Podcasts URL
  -> resolve     Apple lookup API -> RSS feed -> exact GUID match, never a guess by title
  -> download    streamed with size, MIME and duration checks; ffmpeg decodes before any paid call
  -> split       near silence, below the speech API's upload ceiling
  -> transcribe  OpenAI speech-to-text, chunk by chunk, each chunk checkpointed in SQLite
  -> transcript  transcript.txt, canonical and never rewritten
  -> study       structured Responses API output, Pydantic-validated, rendered by Python
  -> deliver     Telegram document group + ZIP, then optional Hanly and Mandarin Mosaic uploads

Reader: Telegram Mini App over a stored transcript or pasted Chinese text
```

A Chinese episode produces `transcript.txt`, a reading version with tone-mark pinyin and a Russian translation, curated vocabulary and grammar notes, and CSV files for the two flashcard apps. The full list is in [docs/study-pack.md](docs/study-pack.md).

## Decisions worth reading

**The transcript is the source of truth.** The study stage derives everything from the saved transcript and never edits it. Suspected speech-recognition errors appear as suggestions with a reason, never as silent replacements. Quotations and flashcard sentences are picked by segment ID from a deterministic catalogue of the transcript, so the model cannot paraphrase a quote. See [docs/study-pack.md](docs/study-pack.md).

**Model output is checked, then retried or dropped.** Each study response must cover its source blocks exactly once, in order, keeping every Chinese character. A "translation" that only repeats the Chinese source fails validation and the chunk is retried.

**No blind retries of paid calls.** Jobs and stage checkpoints live in SQLite. After a restart, finished speech chunks and study requests are reused. A request whose outcome is unknown waits for an explicit `/retry`, because it may already have been billed, and automatic SDK retries are disabled. See [docs/pipeline.md](docs/pipeline.md).

**Uploads are idempotent against APIs that are not.** Collection UUIDs and exact payloads are committed to SQLite before the first network call, so a retry re-sends the same identifiers instead of creating duplicates. Firestore writes carry an `updateTime` precondition, retry a bounded number of times on conflict, and report success only after a read-back. An HTTP 200 on its own does not count as success. See [docs/integrations.md](docs/integrations.md).

**Your own edits win.** A note the bot wrote to a flashcard is replaced only while it is byte-identical to what the bot last wrote. Edit it in the app and the bot leaves it alone and says so.

**The browser holds no credentials.** The Mini App talks only to this backend. Every request is authenticated with Telegram's init-data HMAC, the client sends sentence IDs rather than text, and the backend re-derives sentences from the stored source. See [docs/reader.md](docs/reader.md).

**Deploys are the tested commit, with a way back.** CI builds the exact commit that passed, sends it through a restricted SSH command, drains the worker, backs up SQLite, checks readiness, and rolls back an unhealthy image. See [docs/deployment.md](docs/deployment.md).

## Try it

The resolver and the test suite need no credentials and make no paid calls:

```sh
brew install python@3.12 ffmpeg
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock -e '.[test]'
python -m podcast_bot resolve "https://podcasts.apple.com/nl/podcast/id1490732024?i=1000789324203"
python -m pytest -q
node tests/frontend/app.test.js
```

`resolve` fetches public Apple and RSS metadata only, with no audio download, and prints JSON in the shape of [`resolver-example.json`](resolver-example.json). The tests block unexpected network access and mock OpenAI, Telegram, Mandarin Mosaic and Firebase.

Running the bot itself needs a Telegram bot token, your numeric Telegram user ID and an OpenAI API key. Follow [docs/setup.md](docs/setup.md).

## Security and limitations

Only the configured Telegram user ID in a private chat is accepted. Other users are ignored before processing. Environment credentials, token files, local audio, data, and deployment-specific notes are excluded from Git; credentials/audio are excluded from Docker build context. Never commit `.env` or token files.

Public HTTP/redirect checks, bounded downloads, local-only ffmpeg protocols, safe filenames, secret-redacted logging, and a process lock protect this personal bot. This is not a hardened multitenant fetch service; DNS resolution is not pinned against rebinding.

Apple may omit older episodes outside the latest 200, and RSS may remove entries. Subscriber-only/DRM content is unsupported. The default ASR model has no native subtitle output; Whisper offers SRT with an accuracy tradeoff. Pinyin, sandhi, translation quality, sentence completeness, and pedagogical usefulness remain model judgments even though schema/source/order checks are enforced. Possible ASR mistakes are documented, not automatically fixed. Hanly/Mosaic exports implement the requested CSV schemas; their live import UIs have not been tested.

## Documentation

| Topic | File |
| --- | --- |
| Study pack contents, language rules, validation of long transcripts | [docs/study-pack.md](docs/study-pack.md) |
| Every Telegram command | [docs/commands.md](docs/commands.md) |
| Reader Mini App: uploads, security model, dictionaries, translations, pronunciation | [docs/reader.md](docs/reader.md) |
| Original podcast audio for Reader sentences | [READER_AUDIO.md](READER_AUDIO.md) |
| Episode resolution, transcription, caching and billing | [docs/pipeline.md](docs/pipeline.md) |
| Hanly and Mandarin Mosaic integrations | [docs/integrations.md](docs/integrations.md) |
| Installation and configuration | [docs/setup.md](docs/setup.md) |
| Development, CI and documentation sources | [docs/development.md](docs/development.md) |
| Validation record and its boundaries | [VALIDATION.md](VALIDATION.md) |
| Automatic deployment and releases | [docs/deployment.md](docs/deployment.md), [deploy/README.md](deploy/README.md) |
