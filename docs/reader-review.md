# Reader Review v1

Review v1 uses authentic Reader contexts. Save means “I want to learn this”.
The existing Dutch Saved Vocabulary already has this meaning; no existing items
are removed or rewritten. Each existing lexical item remains the learning target.
One target has one schedule, regardless of exercise type. Sessions select at most
10 distinct due targets, oldest due first, then target ID. Again does not put an
item back into the same bounded session. Reopen Review to check due items again.

## Persistence and provenance

SQLite's existing additive `CREATE TABLE IF NOT EXISTS` convention adds:

- `reader_saved_spans`: canonical Python/codepoint offsets referencing an existing
  saved vocabulary ID. New saves retain the first selected occurrence. Legacy
  rows can use cloze only if the exact saved surface occurs once in the stored
  sentence. No global replacement or guess among duplicate occurrences.
- `reader_saved_sentences`: distinct sentence targets, with canonical document and
  sentence identity, original text snapshot, language, preferred translation
  language and source timestamps. These are never fake vocabulary words.
- `reader_review_state`: references exactly one vocabulary or saved sentence ID;
  owner, due date, interval, ease, review/lapse counts and last review time.
- `reader_review_history`: owner, target, interaction, exercise, grade, timestamp,
  previous and resulting scheduling states. No copied answer text.

Source text and timing follow Saved Vocabulary's existing snapshot convention;
translation remains in its existing cache, not a second review answer store.
Document titles are resolved at display time, so renaming cannot break identity.
The current v1 target has one context; multiple contexts could attach to the same
vocabulary target later. Saving the same expression again within the same sentence
reuses the vocabulary target and its first retained span, matching existing
vocabulary deduplication. A sentence save is unique per owner/document/sentence/text.

State is initialized lazily with insert-or-ignore from each owner's saved targets
on Review access. Original vocabulary rows remain intact. New targets are due at
creation time. The due index bounds the returned batch; initialization may touch
all of an owner's targets, but the collection is never sent to the client.

## Referential integrity and deletion

Every Storage connection enables SQLite foreign-key enforcement. The declared review
foreign keys use default `ON DELETE NO ACTION` (no cascade). Opening a database with
orphan saved-span, review-state or history references fails explicitly with
`sqlite3.IntegrityError`; it does not silently delete evidence or guess new parents.

A saved span belongs to exactly one vocabulary ID and is interpreted only against
that item's immutable saved source snapshot. Vocabulary IDs themselves can be reused
by SQLite, so referenced vocabulary rows cannot be deleted without explicit child
cleanup. There is currently no vocabulary-delete API. An unreferenced vocabulary row
can be deleted; after explicit cleanup/re-save a fresh AUTOINCREMENT review target
ID prevents a stale interaction from applying to the new target.

Sentence deletion through Review deliberately removes history, then state, then the
saved sentence in one transaction. Direct SQL deletion of referenced sentence/state
parents is blocked. Review history is otherwise retained.

Saved targets intentionally have no foreign key to Reader documents or sentence IDs:
they retain authentic source snapshots across re-imports. No document-delete API
exists. Direct SQL document deletion leaves snapshots/state/history intact; cards can
still show the saved context, but answer reveal returns an explicit unavailable error.
Re-imports do not migrate saved offsets to new text. If sentence IDs/text change,
Review keeps the original snapshot and uses only exact ID+text matches for lazy answer
generation. Current-document links and saved timing can become stale after a source
revision; source-version navigation is outside v1.

## Scheduler

No scheduler dependency exists in the inspected main. V1 uses a small deterministic
interval/ease heuristic to keep integration and persistence simple. This is **not
FSRS** and has no calibrated retention probability. All times are UTC and calculated
on the server, from the grade submission time. The service accepts an injected clock.

Initial state: interval 0 days, ease 2.5, counts 0, due immediately.
For previous interval `I` (days) and ease `E`:

| Grade | New interval | New ease |
| --- | --- | --- |
| Again | 10 minutes | max(1.3, E − 0.2) |
| Hard | max(1 day, I × 1.2) | max(1.3, E − 0.15) |
| Good | max(1 day, I × E) | E |
| Easy | max(4 days, I × E × 1.3) | min(3, E + 0.15) |

Intervals cap at 3650 days. New due = submission time + new interval; review count
increases by one on every grade; lapse count increases on Again (including the
first review). New ease affects subsequent reviews. Overdue time gives no bonus.

## Exercises and answers

Recognition shows the authentic sentence, asks for the target's contextual meaning
(or full translation for sentence targets), and waits for reveal before grading.
Cloze replaces the exact selected occurrence with one blank; punctuation and the
stored source remain unchanged. Reveal restores the sentence and identifies the target.
Listening initially hides the text, plays the complete sentence, and reveals text
and meaning together. Sentence targets never get lexical cloze.

Selection is deterministic using completed review count `n`:

1. `n = 0`: recognition.
2. TTS supported and `n % 3 = 2`: listening.
3. Lexical/expression target with valid span and odd `n`: cloze.
4. Otherwise recognition.

Each target occurs once in a batch. One queue serves all exercise types. TTS support
is reported by the browser on session creation; unsupported clients get recognition
or cloze. A later device playback failure leaves reveal/grading usable.

No model is called while fetching cards. Reveal reuses saved lexical meanings or
existing translation cache/study translation. If missing, that one answer uses the
existing coalesced lazy generation service and caches its result. A failed reveal
can be retried; it does not grade. Missing original context produces an explicit
unavailable message/error, never an invented sentence. There is no bulk generation.

## API and retries

All routes use existing Telegram initData authentication and owner checks:

- `GET /api/reader/review`: due summary and bounded batch without listening.
- `POST /api/reader/review` with `{listening: boolean}`: same batch with capability
  aware exercise selection. No persistent session resource is required.
- `POST /api/reader/review/{target}/answer` with `{}`: lazy reveal.
- `POST /api/reader/review/{target}/grade` with `{grade, exercise, interaction}`.
- `POST /api/reader/{document}/saved-sentences` with canonical `sentence_id` and
  optional `target_language` (`ru` or `en`). Client cannot supply source text/timing.
- `DELETE /api/reader/{document}/saved-sentences/{saved_sentence}`: removes this saved
  sentence, its review state and its history. Lexical vocabulary is unaffected.

Server HMAC interaction tokens bind owner, target, review count and exercise.
A SQLite immediate transaction atomically checks the token/version, inserts history
and advances the state. Repeating the same token/grade returns its stored result;
changing the grade or using an outdated interaction fails with 409. Separate devices
cannot schedule a target twice from the same state. Unknown payload fields, including
client due dates, are rejected. The frontend disables double taps, stays on failures,
and retries the same interaction and grade after uncertain network outcomes.

## UI, speech and language boundary

Library → Review → Start Review → reveal → grade → completion → Library uses the
same API helper and Telegram auth as Reader. No standalone login is introduced.
Save sentence is a compact bookmark icon beside existing sentence controls, with
an accessible label and a filled saved state.

System Web Speech uses the existing shared channel. Review specifies the target's
speech locale (Dutch `nl-NL`) instead of changing Reader's language state. Starting
speech cancels previous Reader/Review speech and original playback; leaving Review
stops active speech. No audio files or external TTS API are used.

Review uses the existing language capability registry (`internal_save`) rather than
Dutch conditionals in the engine. Dutch is currently the only enabled internal-save
language. Chinese internal Review is NOT implemented yet. Chinese segmentation,
pinyin, Hanly and Mandarin Mosaic export retain their existing paths and identity.

## Limits and v2 decisions

Generated practice sentences are NOT implemented. Original NPO audio is NOT
implemented; source timestamps are retained for a future audio provider. No media
is downloaded or decrypted. There is no offline queue, adaptive exercise selection,
production/shadowing, multiple contexts, typed answers, statistics or gamification.
Browser TTS availability does not guarantee a Dutch voice on a given device.
A saved sentence lacking translation may incur one ordinary lazy translation request
when revealed; a saved lexical item lacking meaning behaves similarly.

For v2 revisit a calibrated scheduler/FSRS and migration policy, multi-context target
identity, ambiguous legacy spans, explicit review capability flags beyond internal
save, per-context answer-language preference, history retention on sentence deletion,
original source audio adapters and Chinese-specific review QA.
