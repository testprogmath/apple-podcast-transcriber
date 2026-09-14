# Apple Podcast Transcriber & Study Bot

A private, single-user Telegram bot: send an Apple Podcasts episode URL to get a faithful transcript and a curated language-learning pack, then read either that transcript or any Chinese text in an interactive Reader that feeds Hanly and Mandarin Mosaic. Python 3.12+, SQLite, ffmpeg, the official OpenAI Python SDK, Telegram long polling, and a small static Mini App served by the same process. No external queue, no Node build.

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

Meanings, translations and usage notes are written in the configured native language, and that is now enforced rather than merely requested. A model that answers in Chinese — repeating the source in place of a translation, or explaining a word in Chinese — fails validation and the chunk is retried. The Reader applies the same rule to already-generated packs: a "translation" that is just the source again is dropped rather than shown, so a Hanly note falls back to `原文：` alone instead of printing the Chinese twice.

Chinese speech recognition does not translate. The text-processing stage derives all learning files from the saved transcript. It never rewrites that source, even when it suspects an ASR error. The current correction policy is deliberately **suggestions only**; confidence and reasons appear in study notes and metadata, with no silent replacement in quotations or Mosaic sentences.

For every source language other than `zh`, the bot generates only `transcript.txt` and `vocabulary.md`: selected useful words/expressions with meanings, usage and translated examples. It does not generate a full translation, reading guide, pinyin, grammar/culture notes or importer CSVs, and never uploads those episodes to Hanly or Mandarin Mosaic. Available SRT is preserved. `/zip` contains the minimal files plus internal metadata/JSON. Old non-Chinese study packages require `/regenerate` to switch to this format, without repeating transcription.

## Telegram commands

The private chat has a Telegram command menu with Russian descriptions. Tap **Menu** or type `/`. Commands such as `/language` and `/transcribe` still require the argument shown in their description.

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
/reader
/mosaic
/hanly
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
- `/reader` opens the latest transcript or study pack in the Reader. It never retranscribes; it reuses the stored transcript.
- Long jobs update one status message. Study failures leave the transcript usable and deliver it on its own, with an explanation and `/retry` guidance.

## Interactive Reader

The Reader is a Telegram Mini App for reading Chinese and collecting material from it. Open it and you get the text in comfortable type, with every Chinese lexical item tappable and every sentence selectable.

Two destinations, deliberately different:

**Hanly gets vocabulary.** Tapping a word opens it rather than filing it: a bottom sheet shows the glyph, its tone-mark pinyin, and its contextual meaning when the study pack has one, with a single `+ Add to Hanly` control. Nothing enters the basket until you press it, and reopening a selected word offers `✓ In Hanly — remove` instead. The header then shows `Hanly · 3`. Each entry remembers the sentence it was selected from, which becomes the card's study note; selecting the same word again from a different sentence keeps the first context rather than silently re-pointing the note. There is no free-text selection: the Reader segments Chinese into lexical items and you pick from those, so what reaches Hanly is always a real word or chunk rather than whatever your finger dragged across. Multi-character chunks that the study pipeline already extracted for an episode (`研究成果`, `做研究`) are recognised as single items ahead of the generic segmenter. Punctuation, whitespace and Latin text are not tappable.

**Mandarin Mosaic gets sentences.** Tap anywhere in a sentence that is not a word, or its ◎ marker, and the whole sentence is selected. Uploading sends those complete sentences through the existing Mandarin Mosaic sentence API with the existing jieba segmentation. Whole documents are never uploaded.

**Words carry a definition.** The sheet shows the study pack's own contextual meaning when it has one for that exact term. Otherwise it falls back to a local CC-CEDICT definition, labelled `CC-CEDICT` so the two are never confused. When neither knows the word the sheet shows glyph and pinyin only, with no empty label. Dictionary membership is never a gate: a chunk CC-CEDICT has never heard of stays tappable and uploadable, which matters because Hanly accepts arbitrary Chinese strings.

**Pinyin is a toggle.** `拼音 OFF / ON` in the header adds interlinear tone-mark pinyin above every Chinese lexical item using `<ruby>`, never over punctuation or Latin text. It is off by default and remembered per browser in `localStorage`; if site data is unavailable the Reader simply starts with it off. The pinyin sits in the DOM either way, so toggling is instant and line spacing does not shift while it is off. The lexical sheet always shows pinyin regardless of the toggle.

Both baskets are local until you press upload. Open a basket from its counter to review the items, remove any of them, then upload.

### Opening it

| Input | How |
| --- | --- |
| Processed episode | The **📖 Open Reader** button on the finished job, or `/reader` |
| Chinese text | Send the text to the bot; it replies with the button |
| `.txt` / `.md` file | Send the file as a document; UTF-8, up to 2 MB |

Text documents accept up to 200,000 characters and must contain Chinese. PDF, EPUB, OCR and subtitle formats are out of scope.

### What upload does

Hanly: the Reader document owns one stable collection UUID, allocated in SQLite before any network call. Uploading merges the selected glyphs into that collection in a single batch through the existing Firestore client, preserving the collection's existing cards, its optimistic-concurrency retry, and its read-back verification. Tapping a word never touches Firestore.

Each uploaded glyph then gets a study note at `personalizedStories/<glyph>`, so the card carries the context you read it in:

```text
получать

原文：他们获得了菲尔兹奖。
Перевод：Они получили премию Филдса.
```

Nothing in that note is generated. Its first line uses the same meaning the Reader showed you: the study pack's contextual meaning, or a CC-CEDICT definition when the pack has none, so a card never says less than the popup did. The sentence translation comes only from the study pack for that exact sentence, and is omitted otherwise, with no empty labels left behind. The label is `原文` rather than `例句` because the sentence is the real one from the source. A direct-text document has no study pack, so its notes carry `原文：` alone. Opening the Reader never triggers translation, and this change adds no model call anywhere.

Mandarin Mosaic: the document owns one stable pack. Sentence payloads and their UUIDs are committed to SQLite before the first request, so a retry re-sends the same identifiers instead of creating duplicates, and a sentence already staged for that pack is never staged twice. `SuccessfulUpdates` / `UnsuccessfulUpdates` are reconciled as before, and the Mini App reports exactly which sentences failed and keeps them selected.

The two destinations bind differently, because the services themselves differ. A document opened from a podcast episode uses that episode's own Hanly identity, so tapped words merge into the same collection the automatic study upload writes to. Its Mandarin Mosaic pack is separate: a study pack's sentence snapshot is frozen at its first upload, and hand-picked Reader sentences must not rewrite it. Names come from the episode title; direct text derives a short title from its first line, falling back to a timestamp. Names are display only: the UUID and the PackId are the identifiers.

### Notes never overwrite your own writing

Adding the glyph to the collection is the primary operation; the note is enrichment, and a note failure never reports a stored glyph as failed. The Mini App reports both: `Hanly: 3 uploaded to "…" (11 cards).` followed by `Notes: 1 added, 1 kept (yours), 1 failed.`

Ownership is explicit rather than guessed from the note's text. SQLite `hanly_glyph_notes` records the exact story this integration last wrote for each glyph, and a remote note is overwritten only when the document does not exist yet or its `story` is byte-identical to that record. Edit a note in Hanly and the next Reader upload leaves it alone, reporting `skipped-user-modified`. Nothing is added to the Firestore document to mark ownership: it stays exactly the `story` plus `timestamp` shape Hanly writes itself.

A failed upload keeps the basket intact and shows the reason. Reloading the Reader creates nothing externally; only an explicit upload mutates anything.

### Mini App setup

The Reader needs a public HTTPS address, because Telegram requires HTTPS for `web_app` buttons. Put a TLS reverse proxy in front of the bot and point `READER_PUBLIC_URL` at it:

```dotenv
READER_PUBLIC_URL=https://reader.example.com
READER_HOST=127.0.0.1
READER_PORT=8081
```

Compose publishes `READER_PORT` on `127.0.0.1` by default; set `READER_BIND_ADDRESS` if the proxy runs on another host. No BotFather configuration is required: the button carries the URL, and Mini Apps opened from an inline `web_app` button in a private chat need no registered domain. Leaving `READER_PUBLIC_URL` empty disables the button and every Reader entry point; the bot otherwise behaves as before.

For local development, run the bot with:

```dotenv
READER_PUBLIC_URL=http://127.0.0.1:8081
READER_DEV_MODE=true
```

and open `http://127.0.0.1:8081/reader/?doc=<id>` in a browser. Dev mode accepts a request that carries *no* Telegram init data; forged init data is still rejected, and the setting must stay `false` in production.

### Security model

Every external call is made by the backend. The Mini App holds no Firebase API key, no refresh token, no ID token, no Mosaic JWT and no `Authorization` header, and it never contacts Hanly or Mandarin Mosaic directly. It only calls this bot.

Each API request carries `X-Telegram-Init-Data`, validated server-side with Telegram's documented scheme: `secret_key = HMAC_SHA256(key="WebAppData", message=<bot token>)`, then a constant-time comparison against `HMAC_SHA256(key=secret_key, message=<data check string>)`, plus an `auth_date` freshness window. The verified user must be `TELEGRAM_ALLOWED_USER_ID`.

Reader document IDs are opaque 128-bit values scoped to the owning chat, so documents cannot be enumerated by counting up. The client submits sentence IDs, never sentence text; the backend re-derives canonical sentences from the stored source. A Hanly item is accepted only when its glyph is one of the lexical items the Reader itself offered for the referenced sentence, so the endpoint cannot be used to write an arbitrary note for an arbitrary string, and arbitrary prose spans cannot be turned into cards. Responses carry `nosniff`, `no-referrer` and a Content-Security-Policy that allows scripts only from this origin and `telegram.org`.

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

Each study response must cover source blocks exactly once, in order, preserving every Chinese source character apart from reading-line whitespace. Citation text is inserted from a deterministic catalogue of transcript segments: the model selects a schema-constrained segment ID for vocabulary examples, grammar/culture notes, ASR quotations and Mosaic sentences. It cannot write or paraphrase those citation fields. Translations and explanations remain generated. The same selection protocol applies to non-Chinese vocabulary examples. Global ranking over bounded candidate batches removes duplicate/overlapping vocabulary and picks the strongest episode-wide teaching points. It treats Mosaic sentences as a separate candidate category from vocabulary.

For Mandarin Mosaic:

- Every Chinese sentence must be a verbatim source substring. No paraphrase, simplification, correction, fabricated example, or joining of separated source spans is accepted.
- The model selects self-contained useful utterances and natural constructions; 8–35 characters is guidance, not a truncation rule.
- English is used regardless of `NATIVE_LANGUAGE`. No pinyin, Russian, reasons, tags, or timestamps enter the CSV.
- Deterministic filtering removes obvious support/advertising material and repeated listening sentences. Punctuation-only repetitions and harmless interjection variants are deduplicated; global model ranking handles semantic overlap.
- Reasons are retained in `study.json`. Timestamp fields remain null because the study stage does not invent audio alignment.

## Direct Mandarin Mosaic upload

After receiving a Chinese study pack, send `/mosaic` to upload its selected sentences. This command performs no transcription or LLM generation. CSV export remains available independently. With `STUDY_AUTO_UPLOAD=true` (default), completed Chinese study packs are uploaded to configured Hanly and then Mandarin Mosaic services. Set it to `false` for manual `/hanly` and `/mosaic` only.

Set both secrets in the server environment or its private `.env`, then recreate the container with `docker compose up -d`:

```dotenv
MANDARIN_MOSAIC_REFRESH_TOKEN=
MANDARIN_MOSAIC_REFRESH_TOKEN_ID=
```

Leave both empty to disable direct upload. Never paste credentials into Telegram or commit them. A missing/invalid optional configuration does not stop the transcription bot; `/mosaic` explains what needs configuring.

`mosaic/client.py` uses only the supplied refresh, `addpacks`, and `sentences` POST endpoints. It does not discover endpoints, implement Google OAuth, or synchronize `/api/usersentences`. Pack creation is confirmed by UUID membership in `SuccessfulUpdates` and absence from `UnsuccessfulUpdates`. Every sentence UUID is checked the same way; missing acknowledgements are reported as **unconfirmed**, separately from explicit rejection. HTTP 200 alone is insufficient.

JWTs are kept in memory, refreshed with a 30-second expiry margin under an async lock, and replaced once after HTTP 401 before exactly one retry. Returned refresh credentials are reused and saved atomically in private configuration at `DATA_DIR/mosaic-session.json` (mode `0600`); JWTs are never persisted. Keep this file on the existing persistent data volume. A changed environment credential pair replaces the saved session on next startup. Do not share this file or its backups. A lost refresh response can still require replacement credentials if the server invalidated the previous pair.

### Dictionary and pronunciation

Reader lookups are entirely local. Tapping a word makes no request of any kind: every definition and pronunciation is already in the document response, resolved once per distinct glyph when the document is served.

Pinyin resolves in order: the study pack's own pronunciation for its curated terms, then an exact CC-CEDICT entry, then [pypinyin](https://github.com/mozillazg/python-pinyin) as the local fallback. The dictionary step matters for polyphones a character-by-character fallback gets wrong. Meaning resolves in order: the study pack's contextual meaning, then a CC-CEDICT definition, then nothing — a contextual meaning is never replaced by a generic one, and the API reports which source won.

CC-CEDICT stores several entries for a written form when it has several readings. The lowest source id is the deterministic primary, supplying the displayed pronunciation; definitions merge across the homographs in source order, deduplicated, and the popup shows at most three. The alternatives stay available in `reader/dictionary.py` rather than being discarded.

Lookup is one indexed SQLite query per document, not one per tap: 125,061 entries in a 14.6 MB read-only database, adding roughly 0.6 ms to a full episode's response and 0.2 ms to a short one, measured interleaved on a warm process. The response grows from about 82 KB to 181 KB for a full episode, which the reverse proxy compresses. Without the database the Reader still works and simply shows no definitions.

Simplified and traditional forms are both indexed and looked up exactly. Source text is never rewritten, and Hanly receives the exact glyph selected from the source.

The pinned snapshot, its checksum, the build command and the update procedure are in [`dictionary/README.md`](dictionary/README.md). CC-CEDICT is published by MDBG under **CC BY-SA 4.0**; the generated database is an adaptation and carries the same licence, which is why [`dictionary/LICENSE-CC-CEDICT.txt`](dictionary/LICENSE-CC-CEDICT.txt) ships beside it in the image and the Reader footer credits it. That licence covers the dictionary data only, not this repository's code.

Chinese words are segmented locally with [jieba in its default accurate mode](https://github.com/fxsjy/jieba); `mosaic/segmentation.py` is the replaceable adapter. Segmentation is checked to preserve all non-whitespace characters. Original Mandarin and the selected English translation are uploaded unchanged. Segmentation may differ from the official client's dictionary; no claim of identical token boundaries is made.

SQLite tables `mosaic_packs` and `mosaic_sentences` persist the source episode ID, UUIDs, exact upload payloads, and statuses **before** network activity. Credentials are never stored there. There is one Mosaic pack per source episode: the first upload freezes its selected-sentence snapshot. `/regenerate` does not replace that remote snapshot or silently create a second pack. Repeating `/mosaic` resumes that snapshot, reuses all UUIDs, skips confirmed sentences, and retries rejected/unconfirmed ones. A completed upload makes no further requests. This also applies after a process restart; remote idempotency ultimately depends on the supplied API honoring UUID updates.

A network failure during pack creation leaves creation unconfirmed and sends no sentences. A failure during sentence upload preserves the confirmed pack and reports remaining delivery as unconfirmed. Use `/mosaic` for upload retries; `/retry` remains the transcription/study job command. The upload orchestrator lives in `mosaic/service.py`; Telegram handlers contain no API payload or authentication logic.

## Hanly collections via Firebase / Firestore

`/hanly` merges the latest Chinese study pack's selected vocabulary and reusable expressions into an episode collection. It never tokenizes the whole transcript or uploads the Mosaic sentence list. The existing validated `vocabulary` and `mosaic_sentences` model fields already separate these responsibilities. Hanly terms must occur in the saved canonical transcript; incoming terms are deduplicated after surrounding-whitespace trimming and sorted by first source occurrence. Existing Hanly card order, spelling, and custom collection fields are preserved.

By default, after study generation the worker runs **Hanly → Mandarin Mosaic** for configured services, then delivers files and their upload statuses. Each integration failure is isolated: the other integration still runs, the transcript/study files stay cached, and the job can finish successfully. Use `/hanly` or `/mosaic` to retry uploads without ASR or text generation. Sending a cached episode link also reuses local materials. `STUDY_AUTO_UPLOAD=false` disables automatic uploads for both services while keeping the commands. Optional auth setup errors do not prevent bot startup. Temporary audio is still cleaned up by the existing pipeline immediately after ASR.

Store the legitimate Firebase session outside the repository, by default `~/.config/podcast-telegram-bot/hanly-auth.json`, or select a path with `HANLY_AUTH_FILE`:

```json
{
  "api_key": "YOUR_FIREBASE_API_KEY",
  "refresh_token": "YOUR_FIREBASE_REFRESH_TOKEN",
  "project_id": "hanzo-282fc"
}
```

The config is validated and restricted to mode `0600`. Its directory must be writable by the bot because rotated refresh tokens are saved through an atomic file replacement. ID tokens and the Firebase UID are obtained from Auth, cached in memory, and refreshed before expiry under an async lock. A Firestore 401 forces one refresh and exactly one request retry. Do not put credentials in Telegram, HAR files in Git, or auth data inside study archives. A lost refresh response may require replacing the configured session if the previous token was invalidated.

For Docker, place the auth file in a private **host directory outside the repository**, owned/writable by the bot's configured UID. Set `HANLY_AUTH_DIR` to that directory and use the optional override:

```sh
docker compose -f docker-compose.yml -f docker-compose.hanly.yml up -d --build
```

The override mounts the directory at `/app/hanly-auth` and sets the in-container auth path. Mount the directory, not a single file, so atomic rotation works. Use the same two Compose files for subsequent recreation. Base Compose continues to run without Hanly credentials.

`hanly/client.py` implements only the supplied Firebase Auth and Firestore REST protocol for `hanzo-282fc`. It parses `all_user_collections` as a JSON **string**, rejects unexpected schemas or duplicate JSON keys, and preserves unrelated collection values. Each mutation PATCHes exactly `all_user_collections` and `all_user_collections_timestamp`, with both field masks and `currentDocument.updateTime`. The timestamp is `max(now_ms, server_timestamp + 1)`. Up to three conditional-write attempts re-fetch and re-merge after conflicts. A verification GET checks collection identity, name, requested glyphs, active state, and timestamp before reporting success. No Hanly local application/cache files, test collections, Firestore rules, or App Check settings are modified.

SQLite `hanly_collections` maps the canonical Apple show/episode identity (RSS feed/GUID hash fallback) to a UUID **before any network write**. Retries and regeneration reuse it, adding new unique terms; a missing collection is recreated under the same UUID. Collection titles are display labels, never deduplication keys. Upload status is marked unconfirmed before a request and verified only after the read-back check. `hanly/service.py` owns this mapping and study selection; Telegram handlers contain no Firebase payload logic.

Personalized notes use `documents:commit` on `userData/<uid>/personalizedStories/<glyph>`, with the UID taken from Firebase auth and the glyph percent-encoded for reads. One write updates only `story` under an `updateMask`, sets `timestamp` with a `REQUEST_TIME` server-value transform, and carries a precondition: `exists: false` when creating, the document's `updateTime` when updating. Conflicts re-read and retry up to three times, and a verification GET confirms the stored `story` before success is reported. Notes are written one document per commit so each glyph gets its own outcome and its own retry, which an atomic multi-write commit would collapse into one all-or-nothing result.

Hanly-specific logs contain operation, safe episode identity/collection UUID, HTTP status, retry count, and per-glyph note action (`created`, `updated`, `unchanged`, `skipped-user-modified`, `failed`). HTTP transport logs are suppressed in the Hanly request context because Firebase's refresh URL contains the API key. Raw API bodies, credentials, and Authorization headers are never included in user errors.

Tests use mocked HTTP only. Production Hanly access and synchronization have **not** been tested by this implementation; no live integration test runs automatically. To verify real synchronization, explicitly opt in and use a real selected episode collection after configuring your legitimate session.

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
ruff format --check .
python -m compileall -q podcast_bot
node tests/frontend/app.test.js
```

The Mini App has no build step and no framework, so its behaviour is covered by a small DOM shim in `tests/frontend/` run with the Node.js already present on the CI runner. It exercises token rendering, the lexical sheet, explicit selection, the pinyin toggle and its persistence, and both upload outcomes.

For local fixes, run `ruff check --fix .` and `ruff format .`. Ruff targets Python 3.12 with E/F (errors), I (imports), UP (modern syntax), B (likely bugs), and SIM (simplifications). Formatting handles layout; E501 is excluded for long literal messages/SQL.

CI runs lint, formatting, and `python -m pytest -q` on pull requests and pushes to `main`, using Python 3.12 and ffmpeg without service credentials. Dependabot checks Python and Actions weekly, groups minor/patch updates, and leaves major updates separate. No updates auto-merge.

The existing pip install command remains unchanged. Exact production pins live in `requirements.txt` so Dependabot's pip ecosystem can discover them; `requirements.lock` includes it as a compatibility entry point, with no duplicate pins. When regenerating pins with the existing tool, run `uv pip compile pyproject.toml -o requirements.txt`. Ruff remains in the existing `test` development extra; no additional package manager is required for installation or CI.

Repository settings: enable Actions and Dependabot alerts/security updates if disabled. Require **Python quality and tests** in a branch ruleset for `main` to enforce CI before merging; committed YAML does not set repository rules.

Tests block unexpected networking and mock OpenAI, Telegram, Mandarin Mosaic, and Firebase/Firestore APIs. Real ffmpeg tests use generated local audio. See `VALIDATION.md` for results and validation boundaries. No paid call is required to run tests.

Modules: `resolver/`, `transcription/`, `reader/` (sentence parsing, lexical segmentation, documents, init-data validation, HTTP API/server, static Mini App), `study/` (typed schemas, source validation/chunking, prompts, API adapter/checkpoints, selection, renderers), plus `pipeline.py`, `storage.py`, `queue.py`, and `bot.py`. Additional derived outputs can be added within `study/` without changing speech recognition.

## Documentation sources

- [Apple public Search API](https://performance-partners.apple.com/search-api)
- [OpenAI file transcription](https://developers.openai.com/api/docs/guides/speech-to-text)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [GPT-5.4 mini model and pricing](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
- [OpenAI pricing](https://developers.openai.com/api/docs/pricing)
- [Telegram BotFather](https://core.telegram.org/bots/features#botfather)
- [Telegram Mini Apps and initData validation](https://core.telegram.org/bots/webapps)
- [CC-CEDICT, published by MDBG under CC BY-SA 4.0](https://www.mdbg.net/chinese/dictionary?page=cc-cedict)

- [Firestore PATCH and update masks](https://firebase.google.com/docs/firestore/reference/rest/v1/projects.databases.documents/patch)
- [Firestore update-time preconditions](https://firebase.google.com/docs/firestore/reference/rest/v1/Precondition)
