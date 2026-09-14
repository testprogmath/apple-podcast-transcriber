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

## Interactive Reader Mini App — 2026-09-14

279 tests pass, 58 of them new. They cover sentence parsing (Chinese terminators, mixed Chinese/English, decimals, quotes, ellipsis, empty input, exact source preservation with whitespace-only gaps, paragraph grouping), reader tokenization (multi-character words preferred, study-pipeline chunks overriding the generic segmenter, punctuation/English/whitespace left unclickable, lossless round-trip), Telegram initData validation (valid, tampered field, appended field, stripped hash, empty, non-encoded, expired, foreign bot token), the API (unauthorized, foreign user, unknown and malformed document IDs, another chat's document, malformed bodies, five invalid Hanly payloads, four invalid sentence-ID payloads, traversal attempts on static files), Hanly (single call through the existing service, glyph dedup, rejection of items absent from the document, stable collection UUID across retries, failure surfaced as 502 with the row left `unconfirmed`), Mandarin Mosaic (only the selected canonical sentences uploaded, existing jieba segmentation applied and verified lossless, stable pack across retries with no duplicated sentences, per-sentence partial-failure reporting), security (no credentials in any static asset or API response, no document enumeration, opening the Reader mutating nothing externally), and the Telegram entry points (Chinese text, `.txt`/`.md` upload, rejected file types, `/reader` reusing the stored transcript, missing `READER_PUBLIC_URL`).

Four deliberate mutations were each caught by exactly one test: disabled authorization, removed glyph dedup, non-stable Hanly collection UUID, and removed Mosaic sentence dedup.

The HTTP server was exercised over a real loopback socket and with `curl`: static assets, document JSON, security headers, 404 on traversal, and 401 for forged init data in dev mode. The Mini App's JavaScript was run against a DOM shim to confirm rendering, word and sentence selection, basket editing, upload, basket clearing, total-failure state retention, and partial-failure per-sentence retention.

Ruff lint and formatting, `python -m compileall`, `docker compose config`, and a packaging check that the static assets install with the wheel all pass.

No real Hanly, Firebase, Mandarin Mosaic, OpenAI, or Telegram call was made. Production Hanly and Mandarin Mosaic synchronization from the Reader is **not** validated: no live upload was performed. Rendering inside the actual Telegram client, on iOS/Android/desktop, and behind a production TLS reverse proxy also remains unvalidated.

## Hanly note enrichment from the Reader — 2026-09-14

319 tests pass, 40 of them new. Note formatting is covered as a pure function: meaning plus source plus translation, each part missing in turn, exactly one blank line, no trailing whitespace, no empty `Перевод：` label, Chinese punctuation preserved, and an empty result when there is nothing to say.

The Firestore protocol is exercised against a mock transport that enforces real `updateTime` semantics: creation carries `currentDocument.exists: false`, an update carries the document's `updateTime`, every write sets only `story` under an `updateMask` with a `REQUEST_TIME` transform on `timestamp`, Chinese document IDs are percent-encoded on reads and sent unencoded in the resource name, conflicts re-read and retry within three attempts before raising, a diverged read-back fails verification, a rejected ID token refreshes once and succeeds, invalid Firestore document IDs are refused before any request, and no credential appears in logs or errors.

User-note protection is tested from both layers: an unrecognised remote note is preserved, a note byte-identical to our recorded value is updated, a remotely edited note is never overwritten and leaves the SQLite record unchanged, and a remote note already equal to the new story is reported `unchanged` without a write.

Reader upload semantics cover a successful glyph with a created note, a successful glyph with a skipped note, and a successful glyph with a failed note, including an unexpected non-domain exception, which is contained and never leaks its text. A glyph absent from its referenced sentence, an arbitrary prose span, a client-supplied sentence field, and a malformed item are all refused before any Hanly call. An arbitrary Chinese chunk such as `辛苦了` is accepted with no dictionary lookup of any kind.

Six deliberate mutations were each caught by the intended tests: removed ownership check, removed `REQUEST_TIME` transform, removed precondition, removed post-write verification, token membership weakened to a substring check, and note failures no longer contained.

Beyond the suite, the whole path was run end to end against a mock Firebase/Firestore transport: the commit body, the stored multiline note, the SQLite ownership row, an idempotent second upload reported `unchanged`, and a manually edited remote note reported `skipped-user-modified` with the remote text intact. The Mini App was run against a DOM shim to confirm the item payload carries `sentence_id`, that a failed upload keeps every selection, and that the note summary reads correctly at count one.

Ruff lint and formatting, `python -m compileall podcast_bot`, and `docker compose config` pass. No real Hanly, Firebase, Mandarin Mosaic, OpenAI, or Telegram call was made. Writing a real `personalizedStories` document in production remains unverified by this implementation.

## Reader lexical popup and interlinear pinyin — 2026-09-14

339 Python tests and 22 frontend tests pass, 20 and 22 of them new.

Python tests cover tone-mark pinyin (never numeric), neutral tone carrying no mark for 了/的/吗/我们, polyphonic resolution from the whole lexical item (银行 `yín háng` against 行走 `xíng zǒu`), empty pinyin for punctuation, Latin and whitespace, determinism across repeated calls with sockets blocked, and the token representation: `p` on every word token, `m` only when the study pack has a meaning for that exact term, and punctuation tokens carrying nothing but `t` and `w`. Direct-text documents keep pinyin without meanings.

The Mini App has no build step, so its behaviour is now covered by a DOM shim in `tests/frontend/` run under the Node.js already present on the CI runner, wired in as a `Reader frontend tests` step. It asserts that tokens render as real `<button>` elements with `<ruby>`/`<rt>` and `aria-pressed`, that tapping opens the sheet without selecting anything, that only the explicit action selects or removes, that the sheet always shows pinyin and omits an unknown meaning rather than labelling it, that a selection retains its `sentence_id` and the first context wins when a glyph is met again, that the toggle switches inline pinyin and persists to `localStorage`, that a stored preference is restored, that failing site data does not break reading, that the scrim and Escape dismiss without mutating, that focus moves to the action and returns to the token, that a lexical tap never toggles the surrounding Mandarin Mosaic sentence, and that successful uploads clear while failed and partially failed ones keep their selections.

Five deliberate mutations were each caught: tap-selects-immediately, removed `stopPropagation`, pinyin defaulting on, a removed `localStorage` guard, and last-context-overwrites-first.

Scenarios were exercised through the shim with pinyin off, pinyin on, the popup open, a selected glyph, and simultaneous Hanly and Mandarin Mosaic selections; the stylesheet was checked for fixed widths, media queries, full-bleed sheets and safe-area insets. Pixel layout at real viewport widths still needs a browser or the Telegram client.

Ruff lint and formatting, `python -m compileall podcast_bot`, `node --check`, and `docker compose config` pass. `pypinyin` was added as the only new dependency; `requirements.txt` was edited to add that single pin rather than recompiled, because a full recompile also swapped the production HTTP stack from `httpx`/`distro`/`tqdm` to `httpx2`/`httpcore2`/`truststore`, which is unrelated to this change.

No external service is contacted by any test. Rendering inside the real Telegram WebView remains unverified.
