# Apple Podcast Transcriber & Study Bot

A private, single-user Telegram bot. Send it an Apple Podcasts episode link and it returns a faithful transcript. For Mandarin it also builds a validated study pack, and an interactive Reader lets you pick words and sentences from any transcript or Chinese text and send them to two flashcard apps, Hanly and Mandarin Mosaic.

It is built for one person: a Mandarin learner whose native language is Russian. Only the configured Telegram user ID in a private chat is served. Other source languages get a transcript and a vocabulary list, nothing more.

Python 3.12+, SQLite, ffmpeg, the official OpenAI Python SDK, Telegram long polling, and a small static Mini App served by the same process. No external queue, no Node build. CI tests every change and deploys `main` to a single server.

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

A ZIP containing these files plus validated `study.json` is created automatically. `/zip` sends the most recently completed archive. SRT subtitles, written only when the transcription model returns segment timestamps, are preserved in the pack and sent by `/srt` rather than with every delivery.

Meanings, translations and usage notes are written in the configured native language, and that is now enforced rather than merely requested. A model that answers in Chinese — repeating the source in place of a translation, or explaining a word in Chinese — fails validation and the chunk is retried. The Reader applies the same rule to already-generated packs: a "translation" that is just the source again is dropped rather than shown, so a Hanly note falls back to `原文：` alone instead of printing the Chinese twice.

Chinese speech recognition does not translate. The text-processing stage derives all learning files from the saved transcript. It never rewrites that source, even when it suspects an ASR error. The current correction policy is deliberately **suggestions only**; confidence and reasons appear in study notes and metadata, with no silent replacement in quotations or Mosaic sentences.

For every source language other than `zh`, the bot generates only `transcript.txt` and `vocabulary.md`: selected useful words/expressions with meanings, usage and translated examples. It does not generate a full translation, reading guide, pinyin, grammar/culture notes or importer CSVs, and never uploads those episodes to Hanly or Mandarin Mosaic. Available SRT is preserved and fetched with `/srt`. `/zip` contains the minimal files plus internal metadata/JSON. Old non-Chinese study packages require `/regenerate` to switch to this format, without repeating transcription.

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
/srt
/reader
/mosaic
/hanly
/add_hanly 不知不觉
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
- `/srt` sends subtitles for the latest episode. Nothing is generated on demand: the file exists only when the transcription model timed the episode.
- The finished-job message links the episode audio and its Apple Podcasts page, so the recording sits beside its transcript.
- `/reader` opens the standalone Reader library, including when it is empty. No podcast is required. Episode completion buttons still open that episode directly.
- `/add_hanly <text>` files any Chinese word, phrase or sentence as a single Hanly card, with pinyin, a Russian meaning and a translated example in its note, and no episode, transcript or study pack involved.
- Long jobs update one status message. Study failures leave the transcript usable and deliver it on its own, with an explanation and `/retry` guidance.

## Interactive Reader

The Reader is a Telegram Mini App for reading Chinese and collecting material from it. Open it and you get the text in comfortable type, with every Chinese lexical item tappable and every sentence selectable.

Two destinations, deliberately different:

**Hanly gets vocabulary.** Tapping a word opens it rather than filing it: a bottom sheet shows the glyph, its tone-mark pinyin, and its contextual meaning when the study pack has one, with a single `+ Add to Hanly` control. Nothing enters the basket until you press it, and reopening a selected word offers `✓ In Hanly — remove` instead. The header then shows `Hanly · 3`. Each entry remembers the sentence it was selected from, which becomes the card's study note; selecting the same word again from a different sentence keeps the first context rather than silently re-pointing the note. There is no free-text selection: the Reader segments Chinese into lexical items and you pick from those, so what reaches Hanly is always a real word or chunk rather than whatever your finger dragged across. Multi-character chunks that the study pipeline already extracted for an episode (`研究成果`, `做研究`) are recognised as single items ahead of the generic segmenter. Punctuation, whitespace and Latin text are not tappable.

**Mandarin Mosaic gets sentences.** Tap anywhere in a sentence that is not a word, or its ◎ marker, and the whole sentence is selected. Uploading sends those complete sentences through the existing Mandarin Mosaic sentence API with the existing jieba segmentation. Whole documents are never uploaded.

**Words carry a definition, in either language.** A `RU / EN` toggle beside the pinyin one switches the sheet between the learner's language and English, remembered per browser. The Russian side prefers the study pack's own contextual meaning and falls back to the optional Russian dictionary; the English side comes from CC-CEDICT. Whichever side is empty falls back to the other rather than showing nothing, and the source is labelled so the two are never confused. Each dictionary sense renders on its own line. When neither language knows the word the sheet shows glyph and pinyin only, with no empty label. Dictionary membership is never a gate: a chunk CC-CEDICT has never heard of stays tappable and uploadable, which matters because Hanly accepts arbitrary Chinese strings.

**Pinyin is a toggle.** `拼音 OFF / ON` in the header adds interlinear tone-mark pinyin above every Chinese lexical item using `<ruby>`, never over punctuation or Latin text. It is off by default and remembered per browser in `localStorage`; if site data is unavailable the Reader simply starts with it off. The pinyin sits in the DOM either way, so toggling is instant and line spacing does not shift while it is off. The lexical sheet always shows pinyin regardless of the toggle.

Both baskets are local until you press upload. Open a basket from its counter to review the items, remove any of them, then upload.

### Opening it

| Input | How |
| --- | --- |
| Processed episode | The **📖 Open Reader** button on the finished job, or its card in the library |
| Pasted Chinese text | **+ New text** in the library, or send text to the bot |
| TXT / Markdown | **Upload file** in the library, or send a Telegram document: `.txt`, `.md`, `.markdown`; UTF-8 (BOM supported), up to 512 KiB |

Text documents accept up to 200,000 characters and must contain Chinese. PDF, EPUB and OCR are out of scope. SRT imports are described in [Telegram commands](docs/commands.md#study-materials-from-an-uploaded-srt).

Reader works without processing a podcast. `/reader/` opens the library;
`/reader/?doc=<id>` remains a direct link. The ← Reader link returns to the library.
The shelf lists at most 100 documents by last opened (creation time for unopened
items), with ID as a deterministic tie-breaker. Its API returns metadata only.
Reloading a document preserves its URL and saved data.

Normal paste into the textarea is always available. **Paste from clipboard** is
optional: it reads only after a click and, if denied or unavailable, asks you to
paste manually without erasing your text. Create failures keep the title and text.
Titles are optional, deterministic, single-line Unicode text; automatic titles
use the first non-empty line, up to 60 characters. Supplied/renamed titles have
markup removed, whitespace folded, and a 120-character limit. Only outer
whitespace is trimmed from pasted text; internal line breaks remain unchanged.

File import shares one adapter between Telegram and the Mini App. It validates
UTF-8 strictly, removes a BOM, rejects binary control bytes and keeps only a
sanitized basename as source metadata. Markdown headings, lists, quotes, inline
emphasis/link syntax and fence markers are removed conservatively; code contents
and literal HTML remain plain inert text. It is not a Markdown renderer. No
uploaded file blob is stored: only document text and safe filename metadata.
The browser transports bounded bytes as base64 JSON (512 KiB becomes about
683 KiB), below the existing 1 MiB HTTP body limit. The 200,000-character text
limit also applies. There is no web/PDF/DOCX/EPUB import.

**Rename** edits only the stored title and updated timestamp. Document ID,
translation cache, Hanly/Mosaic keys and study selections remain unchanged.
Existing podcast documents stay in the same table. Legacy `text` sources migrate
to `pasted_text`; supported producers are `podcast`, `pasted_text`, and `file`.
Future source adapters can produce the same entity; no web adapter is added.
SQLite adds nullable `updated` and `last_opened` columns, backfills updated from
created, and preserves IDs and integration data.

Both standalone integration keys remain `reader:<document-id>`. Identical text
created twice intentionally gets two documents and two keys. Rename itself makes
no remote request: the existing Hanly merge updates its collection name on the
next explicit upload, retaining the UUID; an existing Mosaic pack keeps its
original name/snapshot. The podcast Hanly episode key and separate Reader Mosaic
pack are unchanged. Existing Hanly Notes ownership/context rules are unchanged.

All sources use the same segmentation, CC-CEDICT/pinyin, popup, baskets and
translation endpoint. Translation remains lazy: exact study translation, then
cached generated translation, then on-demand generation. Word pronunciation uses
system Mandarin speech. The same sentence speaker button uses original podcast
audio when aligned/available, otherwise system Mandarin speech; pasted/files go
directly to speech without an absent-audio warning. Device speech availability
still applies equally to all sources.

Library/create/rename use validated Telegram initData and the existing single-user
allowlist (the existing explicit local dev mode remains). Browser owner/chat IDs
are rejected, never trusted. Titles/text/filenames are rendered with textContent;
the existing CSP remains. Requests are bounded by the existing HTTP server.
There is no create-request idempotency mechanism in the existing architecture:
buttons prevent double-submit, but an explicit retry after a lost response can
create another document; check the library first. No global text deduplication,
delete, search, folders, or reading-position tracking is added.


### What upload does

Hanly: the Reader document owns one stable collection UUID, allocated in SQLite before any network call. Uploading merges the selected glyphs into that collection in a single batch through the existing Firestore client, preserving the collection's existing cards, its optimistic-concurrency retry, and its read-back verification. Tapping a word never touches Firestore.

Each uploaded glyph then gets a study note at `personalizedStories/<glyph>`, so the card carries the context you read it in:
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
