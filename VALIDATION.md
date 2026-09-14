# Validation record — 2026-09-13

## Results

- **193 tests passed** after the Hanly integration (mocked network).
- `ruff check`: passed.
- Python bytecode compilation: passed.
- Real Apple/RSS integration: passed for the full supplied 大鹏 episode URL. The result is saved in `resolver-example.json`.
- No real OpenAI transcription request and no live Telegram message was sent.

## What was exercised

The live resolver fetched Apple lookup metadata and Buzzsprout RSS, matched the exact GUID `Buzzsprout-19793450`, and extracted the expected “我们”和“咱们” episode. Metadata reported 889 seconds, Chinese, and an `audio/mpeg` enclosure. No episode audio was downloaded during this live resolver check.

Offline tests use `httpx.MockTransport` and mocked Telegram methods, and block unexpected socket connections. The OpenAI SDK itself serializes multipart requests into a mock HTTP transport; tests verify the current model's plural `languages[]` field, older models' singular `language` field, and Whisper timestamp options.

An end-to-end test accepts a Telegram URL, enqueues it in SQLite, resolves captured metadata, downloads synthetic audio through a mock HTTP response, runs real ffprobe/ffmpeg, calls the OpenAI SDK against a mock endpoint, persists a Chinese fixture transcript, delivers it through a mocked Telegram method, verifies cleanup, and confirms that repeating the URL causes no second API request.

Other tests cover restart recovery, completed chunk reuse after a partial failure, uncertain in-flight requests, explicit retry, forced runs, delivery failure with cache reuse, private-user authorization, configuration errors, safe logging, maximum duration, bad and oversized downloads, metadata size limits, public-address checks, timestamp offsets, Unicode filename limits, MP3 stream-copy cuts near silence, corrupt audio, missing ffmpeg, and cleanup after success/failure/cancellation or an abandoned job.

## Reproduce

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock -e '.[test]'
python -m pytest -q
ruff check .
python -m compileall -q podcast_bot
python -m podcast_bot resolve "https://podcasts.apple.com/nl/podcast/id1490732024?i=1000789324203"
```

ffmpeg and ffprobe must be on PATH. Unit/workflow tests need no credentials and make no external HTTP calls. The last command intentionally fetches live public metadata.

Tested on macOS with Python 3.12.13, OpenAI SDK 2.54.0, python-telegram-bot 22.8, httpx 0.28.1, feedparser 6.0.14, and pytest 9.1.1. Production dependencies are pinned in `requirements.lock`.

## Study pipeline validation

Mocked tests cover structured Responses API parsing and refusals, verbatim source preservation, tone-mark pinyin validation, sentence chunking, separate study caching, regeneration without ASR, interrupted-request recovery, usage accounting, corrupted pack rebuilding, CSV escaping, Mandarin Mosaic source fidelity and deduplication, document groups, and ZIP delivery. No paid study request was made.

## Unvalidated boundaries

Live study-material quality and Telegram study document delivery have not been exercised. Model access was checked with a read-only API request. The original transcription bot is deployed; this record describes local validation of the study extension. The README documents resolver limits and uncertain-request billing behavior.

## Mandarin Mosaic direct-upload validation — 2026-09-14

32 mocked tests cover successful refresh/pack/sentence operations, expiry and rotated credentials, concurrent refresh and concurrent 401 recovery, one retry after 401, rejected refresh, rejected/missing/conflicting pack acknowledgements, partial sentence rejection and missing acknowledgements, network failures in both upload stages, restart/cancellation recovery with identical UUIDs, secret-safe logs/errors/configuration, private rotated-session persistence, failed local persistence recovery, source validation, local segmentation, malformed responses, regenerated-episode snapshot reuse, and Telegram authorization/command integration.

No real Mandarin Mosaic API call, OpenAI request, or Telegram test message was made for this extension. The API contract is user-supplied; live account access, official-app visibility, and exact official segmenter equivalence remain unvalidated.

## Hanly implementation — 2026-09-14

44 tests cover Firebase refresh/rotation/0600 atomic config replacement, recovery from failed persistence, expiry and concurrent refresh, GET/schema parsing, conditional PATCH of exactly two fields, timestamp monotonicity, collection creation/merging, preservation of unrelated collections and document fields, 409/412/FAILED_PRECONDITION conflict remerge with a three-attempt bound, verification failures, GET/PATCH 401 recovery, missing/denied documents, safe diagnostics, durable UUID reuse after lost write responses and restart, source-based selected vocabulary, Telegram authorization, and optional auto/manual upload wiring.

The full existing transcription/study/Mosaic suite also passes. Ruff lint/format and Python compilation pass; this repository has no separate configured type checker. Tests use mocked HTTP and block unexpected sockets. No real Firebase/Hanly call, real collection mutation, local Hanly application modification, paid OpenAI call, or Telegram test message was performed. Production synchronization remains unvalidated by this implementation.

Manual setup: supply a legitimate external `hanly-auth.json`, mode 0600, set `HANLY_AUTH_FILE` (or the documented Docker directory mount), and opt in separately before any live integration test. No credentials are supplied by the repository.

## Quality / dependency maintenance — 2026-09-14

A fresh Python 3.12 virtual environment installed the project with `python -m pip install -r requirements.lock -e '.[test]'`. `ruff check .`, `ruff format --check .`, `python -m pytest -q`, and `python -m pip check` passed: 47 formatted Python files, 193 tests, no broken requirements. Workflow and Dependabot YAML were validated against their SchemaStore JSON schemas using a YAML 1.2-compatible loader.

Exact production versions are unchanged: pins moved to Dependabot-discoverable `requirements.txt`, included by the existing `requirements.lock` entry point. CI installs ffmpeg and runs with no service credentials. Small B/SIM fixes preserve existing behavior. No production redeployment is required for the quality configuration.

## Command menu and language-specific output — 2026-09-14

198 tests pass. New tests cover the private Telegram command menu, vocabulary-only schema and artifacts for en/de/nl, immutable transcripts, two-document delivery, minimal ZIP contents, independent non-zh cache versioning, rejection of fabricated quotations, and zero integration calls for non-zh. Existing Mandarin study/upload tests remain green. Ruff lint and formatting checks pass. No paid generation is needed for these tests.
