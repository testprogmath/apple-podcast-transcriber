# Hanly and Mandarin Mosaic integrations

[Back to the README](../README.md)

## Manual Hanly cards

Hanly's own interface only offers words its dictionary knows. The Firestore collection behind it does not care, so `/add_hanly` puts arbitrary text on a card:

```text
/add_hanly 辛苦了
/add_hanly 不知不觉
/add_hanly 塞翁失马，焉知非福
```

The whole argument is one card. Nothing is segmented, normalised or looked up: `塞翁失马，焉知非福` becomes a single glyph string, not four words, and an expression no dictionary has heard of is filed exactly as typed. Surrounding whitespace is trimmed and everything inside is kept.

Everything lands in one collection named **Manual imports**, whose UUID is allocated once and stored in SQLite before the first network call, so it survives restarts and is keyed by neither the message nor the text. Repeating a card is a no-op that answers `✓ Already in Hanly` rather than claiming a new one. Delete the collection in Hanly and the next command restores it under the same UUID.

A collection you created yourself that happens to be called *Manual imports* is never adopted: ownership comes from the stored UUID alone, and the integration takes the distinguishable name *Manual imports (bot)* instead.

Arguments are checked only for the obvious: non-empty, at most 200 characters, one line, and containing at least one Chinese character. The cap sits well above any real sentence — the Reader's own limit for a sentence sent as translation context is 600 characters — while still rejecting a pasted paragraph. Dictionary membership is never a test.

The write reuses the same Firestore path as episode uploads: conditional `currentDocument.updateTime`, bounded conflict retry, and a read-back that must show the card before Telegram reports success.

### What the card's note says

Hanly's own **Definitions** header comes from Hanly's dictionary, which is exactly what these expressions are missing, so the study context goes into the card's **Notes** document instead:

```text
sài wēng shī mǎ yān zhī fēi fú

Нет худа без добра: неудача может обернуться удачей.

原文：塞翁失马，焉知非福，别灰心。
Перевод：Нет худа без добра, не унывай.
```

Three sources, in order of authority. Pinyin is local, deterministic and always present. The Russian meaning comes from 大БКРС when it knows the expression and is treated as authoritative: the model is told what it means rather than asked. Everything left over — a meaning for the expressions no dictionary carries, and the example sentence in every case, since nothing local supplies one — is generated in a single request and cached in `hanly_manual_cards`, so repeating a command never pays for it twice. Without `READER_DICTIONARY_RU` or with study generation disabled, the note simply gets shorter.

Generated text is checked before it is stored: the meaning and the translation must be Russian prose rather than echoed Chinese, and the example must quote the expression verbatim. Anything that fails is dropped rather than written, and is not cached, so the next command tries again.

The note is enrichment, never the point. It is written after the card is already verified in the collection, through the same ownership rule the Reader uses: SQLite records the exact text this integration last wrote, and a remote note is replaced only when it still matches that record. Edit a note in Hanly and the next command reports `Note: kept yours` and leaves your writing alone. A note that cannot be written at all still leaves the card added.

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
