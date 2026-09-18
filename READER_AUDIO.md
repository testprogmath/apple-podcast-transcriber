# Original podcast sentence audio

## Scope and architecture

The existing `○ 🔊 文` controls are unchanged. Sentence audio prefers the original
podcast range and falls back to the existing Mandarin system voice. Word audio
continues to use system speech. No paid alignment, cloud TTS, audio clips, new
selection control, background download, or shadowing UI is introduced.

### Transcription and persistence

Before this change, Whisper requested segment timestamps; segments were retained
in chunk-cache JSON and exported to SRT. Provider word timestamps were discarded.
The Reader used only canonical text and had no audio mapping. Episode metadata
already retained the resolved enclosure URL, RSS feed and episode identity; the
local audio download was temporary and removed after processing.

Whisper now requests both word and segment timestamps in its existing
`verbose_json` request. Other configured models are not changed or silently
replaced. Their responses can supply timing if available, but timing is not
assumed. OpenAI documents word/segment timestamp requests for `whisper-1` only:
[Speech to text](https://developers.openai.com/api/docs/guides/speech-to-text).
No extra transcription call is made.

Words survive chunk caching, with each chunk's existing offset added on combine.
`audio-timing.json` is written atomically beside `transcript.txt`, containing a
schema version, exact canonical text, original enclosure URL, measured source
duration, words and segments. Study generation copies this sidecar unchanged.
Existing SQLite `reader_documents.source_reference` locates the durable artifact;
no schema migration or per-sentence duplication of the URL is needed. Reader
reopening recomputes cheap deterministic ranges locally; it never retranscribes.

Old documents, cached chunks without words, cached study packs without the sidecar,
missing/corrupt sidecars, and plain-text documents remain readable and use TTS.
Existing caches are not invalidated. Retrieving legacy SRT timing is deliberately
not attempted because it lacks the new canonical-text association.

### Alignment and confidence

Normalize NFC and remove punctuation/whitespace, preserving letter case, digits
and Chinese characters. Require the *entire ordered stream* of timing text to
match the normalized canonical sentence stream. Align by cumulative positions,
never substring search, so repeated words/sentences remain distinct.

Prefer words; use segments when words produce no usable ranges. A sentence can
span several units, but both boundaries must coincide with unit boundaries. A
shared segment/word is not divided or assigned guessed timing. Missing content,
invalid/nonfinite times, overlaps and out-of-order units fail conservatively.
Unmapped sentences use TTS. Canonical text is never edited.

Default padding is 120 ms before and 200 ms after, clamped to zero, source duration
and neighboring units. These are conservative starting values, not a measured
quality claim. Listen to real samples before tuning them.

### Source transport and security

Use direct HTTPS enclosure playback through one lazy `HTMLAudioElement` with
`preload="metadata"`. It is created and assigned a URL only on a user tap. The
browser can request metadata/byte ranges; opening Reader downloads no podcast.
No `crossorigin` attribute is set: ordinary media playback does not require a
JavaScript-readable CORS response ([MDN audio reference](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/audio)).
CSP allows HTTPS media; API fetches remain same-origin.

The authenticated Reader GET determines the source from its owned document.
There is no URL input or server-side proxy fetch. Reject non-HTTPS sources,
userinfo credentials, unusual ports, local-name suffixes and nonpublic IP
literals. This is a source-format filter, not DNS/redirect validation: the backend
does not connect to the URL. Existing ownership and Telegram initData checks apply.

A limited live probe of the existing Buzzsprout fixture enclosure with an Origin
header and `Range: bytes=0-1023` received a 302 with `Access-Control-Allow-Origin: *`,
then a 403. This does **not** establish successful byte-range playback or device
compatibility. No CORS-specific blocker was demonstrated. A proxy is not added
speculatively: it would not inherently resolve a host refusal and would introduce
an authenticated streaming/redirect/SSRF surface. Direct playback is best effort
with tested fallback; real-device transport validation remains a release check.
If it establishes a browser-specific transport blocker, revisit a narrow,
owner-authorized streaming proxy with Range and redirect validation.

Enclosures can expire, be removed, or change (including dynamic ads). No automatic
RSS refresh substitutes a potentially different recording under existing timings.
Load/seek failures use TTS. Reject source duration changes over one second and
ranges outside the loaded duration. Equal-duration content changes cannot be
detected; original-source permanence and alignment are not guaranteed.

### Playback and cancellation

One reusable player has loading, seeking and playing phases. Existing speech state
tracks TTS separately; every entry point cancels the other channel. A new sentence
or repeated tap pauses the previous request and restarts at its own range. Word
speech, page hiding and page navigation stop original playback. Generation/request
checks discard callbacks and rejected promises belonging to earlier requests.

Check media time on `timeupdate` and every 50 ms while playing; pause at the end.
An eight-second loading/seeking/no-progress watchdog prevents an indefinitely
active control. Failed load, seek, `play()`, unavailable source or missing mapping
fall back silently to Mandarin TTS before useful playback. “Useful” means at least
350 ms of observed advancing playback, or half the range for shorter sentences.
After that threshold, failure stops gracefully without repeating the full sentence.
If TTS also fails, use the existing pronunciation-unavailable toast. Mobile TTS
activation restrictions after an asynchronous media failure still need device tests.

Translation, pinyin and Mosaic do not participate in audio mapping or source
selection. Future repeat/shadowing can call the same sentence playback abstraction
and range metadata without adding/replacing the sentence control; recording and
scoring would remain separate work.

## Validation and manual release checks

Automated tests simulate all external services and browser media. They cover:
word/segment alignment and repetition, ambiguous shared units, invalid boundaries,
padding, cache compatibility, chunk offsets, temporary-download cleanup, durable
Reader reload, study copying, source filtering and document authorization;
original-first playback, lazy reuse, early/late failures, watchdogs, rejected
promises, rapid taps, stale callbacks, word interruption and missing system speech.

Use a **newly transcribed timed** episode (Whisper, or a response verified to contain
timing). No paid transcription was triggered for development. Before release:

1. In Telegram iOS, Android, Desktop and normal Safari/Chrome, verify an actual
   podcast speaker is heard, then replay and switch sentences rapidly. Check
   network responses for successful media/Range requests and seeking.
2. Sample 10–20 sentences: beginning/end of episode, short/long sentences, repeated
   phrases, chunk boundaries, mixed Chinese/English, pauses and segment-spanning
   sentences. Record expected vs heard start/end, clipped sounds, neighboring
   speech and fallback reason. Do not claim timing quality from text tests alone.
3. Disable/block the enclosure before playback: verify silent system-voice fallback.
   Interrupt networking after audible progress: verify no full TTS replay. Verify
   fallback after delayed failures on iOS still has permission to speak.
4. Test original A → B → A, original → word TTS and reverse, same-sentence restart,
   popup close, app background/foreground and page close. Confirm one active voice.
5. Toggle RU/EN translation and pinyin; select Mosaic independently. Test a plain
   text document and an old podcast document without timing. Reopen Telegram and
   restart the bot: source/ranges survive and no paid requests occur on opening.
6. Simulate missing/expired audio and duration mismatch; reading, translations and
   existing integrations must continue working through TTS fallback.

No physical-device listening, production deployment or successful live Range test
has been performed for this change. Those checks remain necessary before claiming
reliable original playback across supported clients.

## Implementation record

- Worktree: `/Users/ext-anna.khvorostianova@goflink.com/private/apple-podcast-transcriber-original-sentence-audio`
- Branch: `feature/reader-original-sentence-audio`
- Local main base: `bef409eb47e2863dc2ac87b01c3335b39dac9765`
- All implementation, formatting and tests ran in the new worktree. Existing
  worktrees were not edited, reset, stashed, committed, merged or rebased.
- Changed files: `models.py`, `pipeline.py`, `storage.py`,
  `transcription/openai.py`, `study/service.py`, `reader/api.py`, new
  `reader/audio.py`, and `reader/static/app.js` under `podcast_bot/`;
  `tests/test_reader_audio.py`, `tests/test_pipeline_bot.py`,
  `tests/test_study.py`, `tests/frontend/app.test.js`, and this document.
- Validation: Ruff lint and formatting passed; pytest **621 passed**; frontend
  **114/114 passed**; `compileall`, `node --check`, and `git diff --check` passed.
  `docker compose config --quiet` passed with a temporary empty `.env` removed
  immediately afterward; production secrets were not loaded. No image build,
  deployment, paid provider calls, or physical-device tests were performed.

### Why original audio is absent

`document_audio` returns a status alongside the source and ranges, and the Reader
says it once per document rather than on every tap. The statuses separate causes
that look identical from the outside:

| Status | Meaning |
| --- | --- |
| `untimed` | The sidecar holds neither words nor segments. The transcription model returned no timestamps: `timestamp_granularities` is accepted for `whisper-1` only, so any other configured model produces this. |
| `unaligned` | Timings exist but do not reconstruct the canonical sentences. A data problem, not a configuration one. |
| `source` | The stored enclosure URL failed the source filter. |
| `stale` | The sidecar is missing its version, describes other text, or is unreadable. |
| `missing` | No sidecar was written for this episode, which is the case for everything transcribed before this feature. |
| `text` | A pasted-text document. The Reader stays silent: there is nothing to explain. |

A source that loads but will not play reports `Original audio would not play; using
speech.` once, which is the case to watch on a real device given the enclosure probe
recorded in the section above.
