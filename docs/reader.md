# Interactive Reader

[Back to the README](../README.md)

The Reader is a Telegram Mini App for reading Chinese and collecting material from it. Open it and you get the text in comfortable type, with every Chinese lexical item tappable and every sentence selectable.

Two destinations, deliberately different:

**Hanly gets vocabulary.** Tapping a word opens it rather than filing it: a bottom sheet shows the glyph, its tone-mark pinyin, and its contextual meaning when the study pack has one, with a single `+ Add to Hanly` control. Nothing enters the basket until you press it, and reopening a selected word offers `✓ In Hanly — remove` instead. The header then shows `Hanly · 3`. Each entry remembers the sentence it was selected from, which becomes the card's study note; selecting the same word again from a different sentence keeps the first context rather than silently re-pointing the note. There is no free-text selection: the Reader segments Chinese into lexical items and you pick from those, so what reaches Hanly is always a real word or chunk rather than whatever your finger dragged across. Multi-character chunks that the study pipeline already extracted for an episode (`研究成果`, `做研究`) are recognised as single items ahead of the generic segmenter. Punctuation, whitespace and Latin text are not tappable.

**Mandarin Mosaic gets sentences.** Tap anywhere in a sentence that is not a word, or its ◎ marker, and the whole sentence is selected. Uploading sends those complete sentences through the existing Mandarin Mosaic sentence API with the existing jieba segmentation. Whole documents are never uploaded.

**Words carry a definition, in either language.** A `RU / EN` toggle beside the pinyin one switches the sheet between the learner's language and English, remembered per browser. The Russian side prefers the study pack's own contextual meaning and falls back to the optional Russian dictionary; the English side comes from CC-CEDICT. Whichever side is empty falls back to the other rather than showing nothing, and the source is labelled so the two are never confused. Each dictionary sense renders on its own line. When neither language knows the word the sheet shows glyph and pinyin only, with no empty label. Dictionary membership is never a gate: a chunk CC-CEDICT has never heard of stays tappable and uploadable, which matters because Hanly accepts arbitrary Chinese strings.

**Pinyin is a toggle.** `拼音 OFF / ON` in the header adds interlinear tone-mark pinyin above every Chinese lexical item using `<ruby>`, never over punctuation or Latin text. It is off by default and remembered per browser in `localStorage`; if site data is unavailable the Reader simply starts with it off. The pinyin sits in the DOM either way, so toggling is instant and line spacing does not shift while it is off. The lexical sheet always shows pinyin regardless of the toggle.

Both baskets are local until you press upload. Open a basket from its counter to review the items, remove any of them, then upload.

## Opening it

| Input | How |
| --- | --- |
| Processed episode | The **📖 Open Reader** button on the finished job, or `/reader` |
| Chinese text | Send the text to the bot; it replies with the button |
| `.txt` / `.md` file | Send the file as a document; UTF-8, up to 2 MB |

Text documents accept up to 200,000 characters and must contain Chinese. PDF, EPUB, OCR and subtitle formats are out of scope.

## What upload does

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

## Notes never overwrite your own writing

Adding the glyph to the collection is the primary operation; the note is enrichment, and a note failure never reports a stored glyph as failed. The Mini App reports both: `Hanly: 3 uploaded to "…" (11 cards).` followed by `Notes: 1 added, 1 kept (yours), 1 failed.`

Ownership is explicit rather than guessed from the note's text. SQLite `hanly_glyph_notes` records the exact story this integration last wrote for each glyph, and a remote note is overwritten only when the document does not exist yet or its `story` is byte-identical to that record. Edit a note in Hanly and the next Reader upload leaves it alone, reporting `skipped-user-modified`. Nothing is added to the Firestore document to mark ownership: it stays exactly the `story` plus `timestamp` shape Hanly writes itself.

A failed upload keeps the basket intact and shows the reason. Reloading the Reader creates nothing externally; only an explicit upload mutates anything.

## Mini App setup

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

## Security model

Every external call is made by the backend. The Mini App holds no Firebase API key, no refresh token, no ID token, no Mosaic JWT and no `Authorization` header, and it never contacts Hanly or Mandarin Mosaic directly. It only calls this bot.

Each API request carries `X-Telegram-Init-Data`, validated server-side with Telegram's documented scheme: `secret_key = HMAC_SHA256(key="WebAppData", message=<bot token>)`, then a constant-time comparison against `HMAC_SHA256(key=secret_key, message=<data check string>)`, plus an `auth_date` freshness window. The verified user must be `TELEGRAM_ALLOWED_USER_ID`.

Reader document IDs are opaque 128-bit values scoped to the owning chat, so documents cannot be enumerated by counting up. The client submits sentence IDs, never sentence text; the backend re-derives canonical sentences from the stored source. A Hanly item is accepted only when its glyph is one of the lexical items the Reader itself offered for the referenced sentence, so the endpoint cannot be used to write an arbitrary note for an arbitrary string, and arbitrary prose spans cannot be turned into cards. Responses carry `nosniff`, `no-referrer` and a Content-Security-Policy that allows scripts only from this origin and `telegram.org`.

## Dictionary and pronunciation

Reader lookups are entirely local. Tapping a word makes no request of any kind: every definition and pronunciation is already in the document response, resolved once per distinct glyph when the document is served.

Pinyin resolves in order: the study pack's own pronunciation for its curated terms, then an exact CC-CEDICT entry, then [pypinyin](https://github.com/mozillazg/python-pinyin) as the local fallback. The dictionary step matters for polyphones a character-by-character fallback gets wrong. Meaning resolves in order: the study pack's contextual meaning, then an optional Russian gloss, then a CC-CEDICT definition, then nothing — a contextual meaning is never replaced by a generic one, and the API reports which source won.

## Optional Russian dictionary

Set `READER_DICTIONARY_RU` and the popup shows Russian instead of English wherever the study pack has no contextual meaning. It also covers compounds CC-CEDICT omits: 研究成果 resolves to `результаты исследований`, which CC-CEDICT has no entry for at all.

This data is **not** part of the project. 大БКРС offers no formal licence — only an informal note that the databases may be used freely — and documents no provenance for the published dictionaries it draws on, so nothing here redistributes it. `tools/build_bkrs_dictionary.py` converts a copy you download yourself into SQLite, and `docker-compose.bkrs.yml` mounts the result read-only, the same optional-override pattern the Hanly auth file uses:

```sh
mkdir -p ~/dictionaries/bkrs/source && cd ~/dictionaries/bkrs
curl -L -o source/dabkrs.gz https://bkrs.info/downloads/daily/dabkrs_<YYMMDD>.gz
python3 build_bkrs_dictionary.py source/dabkrs.gz bkrs.sqlite3
```

Roughly 25 seconds for 3.46 million entries and a 384 MB database, kept outside the repository and outside the image so other applications on the same host can share it. Definitions are stored with their ABBYY DSL markup intact so the database stays a faithful reformatting; `reader/bkrs.py` is the reference renderer. Pinyin is still taken from CC-CEDICT, which separates syllables (`rèn wéi`) where the Russian source does not (`rènwéi`).

CC-CEDICT stores several entries for a written form when it has several readings. The lowest source id is the deterministic primary, supplying the displayed pronunciation; definitions merge across the homographs in source order, deduplicated, and the popup shows at most three. The alternatives stay available in `reader/dictionary.py` rather than being discarded.

Both languages travel in one `glossary` keyed by glyph rather than repeated on every token. A transcript repeats each word about 2.7 times, so keying by glyph is what makes carrying two languages cheaper than carrying one used to be.

Lookup is one indexed SQLite query per document, not one per tap: 125,061 entries in a 14.6 MB read-only database, adding roughly 0.6 ms to a full episode's response and 0.2 ms to a short one, measured interleaved on a warm process. The response grows from about 82 KB to 181 KB for a full episode, which the reverse proxy compresses. Without the database the Reader still works and simply shows no definitions.

Simplified and traditional forms are both indexed and looked up exactly. Source text is never rewritten, and Hanly receives the exact glyph selected from the source.

The pinned snapshot, its checksum, the build command and the update procedure are in [`dictionary/README.md`](../dictionary/README.md). CC-CEDICT is published by MDBG under **CC BY-SA 4.0**; the generated database is an adaptation and carries the same licence, which is why [`dictionary/LICENSE-CC-CEDICT.txt`](../dictionary/LICENSE-CC-CEDICT.txt) ships beside it in the image and the Reader footer credits it. That licence covers the dictionary data only, not this repository's code.

## Reader personal vocabulary

The Reader remembers exact Chinese words/chunks globally across its documents.
`unknown` means no SQLite row; only `known` and `learning` are persisted in
`vocabulary_state(glyph TEXT PRIMARY KEY, state TEXT CHECK(state IN
('learning', 'known')) NOT NULL, updated TEXT NOT NULL)`. The table is created
additively on startup; existing documents need no reprocessing.

The popup resolves state in this order: current local Hanly basket → learning;
otherwise saved state; otherwise unknown. **I know this / Mark as known** saves
immediately. **Mark as unknown** deletes the row. Removing a basket item restores
its saved state. Successful Hanly collection upload saves learning; failed upload
leaves saved state unchanged. Marking a selected word known keeps it effectively
learning until removal; sending it successfully saves learning again. This does
not delete Hanly cards or synchronize knowledge from Hanly.

Authenticated `POST /api/reader/<document-id>/vocabulary-state` accepts only
`{"glyph":"获得","state":"known"}` (or `unknown`) and returns
`{"glyph":"获得","vocabulary_state":"known"}`. Glyphs must be exposed by the
canonical document segmentation; dictionary membership is unnecessary. The GET
response includes `glossary[glyph].vocabulary_state`, explicitly `unknown` when
absent. Loading performs one additional indexed SELECT per 500 unique glyphs
(usually one; zero for no glyphs), reusing results across repeated tokens.

Failed saves show an error without changing saved UI state or either basket.
Within a WebView, pending knowledge saves and Hanly uploads cannot overlap.
The popup is authoritative; this feature adds no document-wide coloring and
changes neither Mandarin Mosaic nor Hanly Notes. Verify the secondary action,
status badge, focus, and wrapping in Telegram's narrow and dark WebViews before
release. Frontend regression tests: `node tests/frontend/app.test.js`.

## On-demand Reader sentence translations

Each Chinese sentence has a compact **Show translation** action. Reveal or hide
several Russian translations independently; word taps, Pinyin, Hanly and Mosaic
selection keep their existing behavior. Opening a document never generates translations.

`POST /api/reader/<document-id>/sentences/<sentence-id>/translation` requires the
same Telegram authentication and document ownership as other Reader endpoints.
Send an empty body or `{}`; source text and prompts are never accepted from the
browser. The response contains `sentence_id`, `translation`, and `source`
(`study` or `generated`).

Resolution order: Russian study material → generated SQLite cache → one lazy
model request. Study examples/passages match the **whole canonical sentence**,
ignoring only whitespace and NFC differences (the existing study normalization
rule). Punctuation and all other characters must agree. Conflicting matches,
partial passages, non-Russian packs and untranslated output are not reused.
English Mosaic export translations are not used for this Russian reading aid.

The additive `reader_sentence_translations` table stores document/sentence IDs,
exact source text, translation, source, created and updated timestamps, with a
composite primary key. Generated cache hits require identical source text;
stale entries are replaced after successful generation. Study translations stay
in their existing material files. Hiding does not delete either cache.

Generation reuses `OpenAIStudyClient` and the bot's shared OpenAI connection,
using the existing mini default (`StudySettings.model`). The structured response
contains only a translation. A consistent single-answer prompt includes up to
3,000 target characters and 600 characters from each neighboring sentence;
only the target translation is saved. Output is bounded to 2,400 tokens and
6,000 characters and must contain Russian text. No extra temperature parameter
is passed through the shared model abstraction.

The total request budget, including waiting for a slot, is 45 seconds. Up to two
generations run concurrently, with at most eight distinct pending requests.
Same-sentence concurrent requests share one task, including its failure. SDK
retries remain disabled; failures require an explicit retry and may have been
billed. No public translation service or full-document generation is exposed.

Manual Telegram checks before release (paid generation requires explicit approval):

- Open a study sentence with an exact Russian translation: reveal immediately,
  confirm `source=study` and no model request.
- Open direct text: first reveal loads locally, then displays Russian; reopen
  the Reader and confirm cached reuse without another generation.
- Try `大家好，欢迎回来，Mami Chinese。`: preserve the show name in natural Russian.
- Select a Hanly word and a Mosaic sentence, reveal several translations and
  toggle Pinyin: all selections and visible translations remain independent.
- Check narrow/light/dark WebViews, focus, readable secondary text and retry UI.

Sentence translation now follows the Reader RU/EN toggle. Requests accept only
an optional `language` field (`ru` or `en`, default `ru`). Open translations refresh
on language changes; late responses cannot overwrite the selected language.
The additive `reader_translations_by_language` table keys cached results by
(document_id, sentence_id, language); existing Russian cache entries are copied
without deleting the legacy table. Exact English Mosaic study translations may
be reused for EN. Hidden sentences never generate merely because language changes.

## System word pronunciation

Tap the compact speaker beside pinyin in the lexical popup to pronounce the exact
trimmed Chinese word/chunk, including traditional characters and mixed text.
Only a tap starts speech; Pinyin, definitions and sentence translations are never
spoken. The button is hidden when Web Speech is unsupported. Playback uses the
browser/system speech engine with no backend API, audio storage or per-play
application charge. Availability and offline behavior depend on installed voices
and the browser's speech provider; the Reader does not guarantee offline synthesis.

Voice selection uses language metadata: zh-CN first, then zh-Hans/zh-SG/cmn,
then other Chinese voices with zh-HK/zh-MO last. Empty voice discovery falls back
to `utterance.lang = "zh-CN"` without choosing an English/Russian voice. Voices
refresh on `voiceschanged` and on tap. Rate/pitch remain natural system defaults.
Repeat taps cancel/restart the Reader's utterance. Changing or closing the popup,
hiding the page or leaving it stops active Reader speech; idle actions do not
cancel speech. No voice telemetry is logged.

Manual smoke matrix (not yet verified on physical devices):

| Browser/device | Checks |
| --- | --- |
| iOS Telegram WebView | Mandarin voice, delayed voice loading, repeat taps, popup close |
| Android Telegram WebView | Mandarin voice availability, repeat taps, switching words |
| Telegram Desktop | Voice availability, keyboard activation and focus |
| Safari | User-gesture playback, delayed voices, page hide |
| Chrome | Mandarin ranking, repeat taps, keyboard activation |

For every row, open a word, tap the speaker repeatedly, switch to another word,
and verify no queued speech and unchanged Hanly/Mosaic/translation state. Repeat
with Pinyin off/on, a non-dictionary chunk and traditional text. Inspect selected
`voice.name`/`voice.lang` temporarily in developer tools if diagnosing a device;
do not record telemetry. Test a device without Chinese voices and one without
Web Speech support. No paid API calls are necessary.

## Reader sentence pronunciation

Every sentence carries a compact speaker control between its Mosaic mark and its
translation action. Tapping it speaks the canonical Chinese sentence through the
system's Mandarin voice; the control turns blue while that sentence plays and
returns to slate when it stops. Nothing speaks until you tap. Opening a document,
scrolling, toggling Pinyin, revealing a translation and selecting for Mosaic are
all silent.

The Reader owns one utterance at a time, and the word popup shares it. Starting
sentence B stops sentence A and clears A's active state; tapping the same sentence
again restarts it instead of queueing a second reading; pronouncing a word stops a
playing sentence and the reverse. `speakMandarin` is that single channel, so there
is one Web Speech implementation rather than two. Merely opening or closing a word
popup is not a playback command and leaves a playing sentence alone.

Spoken text is the canonical sentence the backend stored, read from the document
payload and never reassembled from the DOM. Pinyin, a visible Russian translation,
the ○ and 文 controls and any lexical popup content cannot reach the synthesizer.
Chinese punctuation is passed through untouched because it carries the prosody,
and a long sentence is spoken whole rather than split or truncated.

Voices are chosen by BCP-47 metadata, never by platform voice name: `zh-CN` first,
then another Mandarin tag (`cmn`, `zh-Hans`, `zh-SG`), then any remaining `zh-*`.
With no Chinese voice at all the utterance still declares `lang="zh-CN"` and lets
the system choose. An empty first `getVoices()` is not a permanent failure;
`voiceschanged` refreshes the list.

The UI boundary is source-agnostic. The control calls `playSentenceAudio(sentence)`,
which speaks the sentence today. A later feature that ships per-sentence source
timings can play the original podcast range inside that one function, leaving the
control, its active state and its tests unchanged. This release adds no timestamps,
no clipping, no cache and no shadowing.

Where `speechSynthesis` or `SpeechSynthesisUtterance` is missing the control is not
rendered at all, and Mosaic, translations, Hanly and word taps behave exactly as
before. A synthesizer that refuses to start reports `Pronunciation unavailable` in
the usual toast and leaves the transcript alone.

Verify before release, on devices rather than a desktop browser alone (iOS Telegram
WebView, Android Telegram WebView, Telegram Desktop, Safari, Chrome):

- Tap a sentence: the complete Mandarin sentence is heard and the control is blue.
- Tap another sentence: the first stops at once and its control returns to slate.
- Tap one sentence repeatedly: it restarts cleanly and no readings pile up.
- Reveal a translation, then tap the speaker: only the Chinese is spoken, and the
  translation stays open.
- Select the sentence for Mosaic and play it: the selection is unchanged.
- Toggle Pinyin on and play again: the spoken text is identical.

Frontend regression tests: `node tests/frontend/app.test.js`.
