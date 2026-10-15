# Review v1 implementation and validation report

This report records the validation and checkout snapshot before commit/push.
Publication status is reported separately in the chat.

## 1–3. Checkout

- Worktree: `/private/tmp/apple-podcast-transcriber-reader-review-v1`
- Branch: `feature/reader-review-v1`
- Base: `82e18b4b3e2d3ef533d7421114df1d1cab55e2ff` (freshly fetched `origin/main`,
  Dutch tools merge #44). HEAD still equals this base; no commit has been created.
- No existing feature worktree was modified. No push, PR, deployment or production
  data changes were performed.

## 4–7. Architecture and schema

Inspected actual merged storage, save/lexical APIs, translation cache/service,
ReaderDocument and Sentence identities, NPO timestamps, language capabilities,
shared speech channel, Telegram navigation/auth, migrations and external integrations.
The implementation plan was returned before edits.

Saved Vocabulary remains the lexical/expression learning target. No second vocabulary
collection was added. `reader_review_state` references either an existing vocabulary
item or a distinct `reader_saved_sentences` target. Exercises do not own queues.

Four additive tables: `reader_saved_spans`, `reader_saved_sentences`,
`reader_review_state`, `reader_review_history`. Added owner/due index; new target IDs
use AUTOINCREMENT so deleted-target tokens cannot become valid for a new target.
Lazy insert-or-ignore state initialization preserves existing vocabulary rows.
Vocabulary saves and exact-span insertion are one transaction. Legacy duplicate
surfaces safely fall back to recognition/listening rather than guessing a cloze span.

Saved sentence targets retain owner, document/sentence identity, canonical source
snapshot, language, preferred translation language and optional source timestamps.
Existing translations stay in the existing translation store. Titles are resolved
from the referenced document, so renaming preserves context identity.

## 8. Scheduler

A small deterministic interval/ease heuristic; **not FSRS**. Inspected dependencies
contain no scheduler. Avoided an additional scheduling dependency and documented the
heuristic rather than claiming calibrated retention. All calculations run server-side
with an injected clock in tests.

Initial interval 0 days, ease 2.5, count/lapses 0, due at target creation.
For previous interval I in days and ease E:

| Grade | Interval | Ease |
| --- | --- | --- |
| Again | 10 minutes | max(1.3, E - 0.2) |
| Hard | max(1, I * 1.2) | max(1.3, E - 0.15) |
| Good | max(1, I * E) | E |
| Easy | max(4, I * E * 1.3) | min(3, E + 0.15) |

Interval caps at 3650 days; due is submission time plus interval. Every grade increments
review count; Again increments lapse count, including a first review. Overdue time
adds no bonus. Stored previous/resulting state supports inspection and later migration.

## 9–12. Exercises

Selection by completed review count n: first recognition; then listening when supported
and n modulo 3 is 2; otherwise cloze on odd n for lexical/expression targets with a
valid span; otherwise recognition. Sentence targets never receive cloze.

Recognition shows authentic context and retrieves contextual meaning before reveal.
Cloze slices exact canonical codepoint offsets into one blank, including the correct
occurrence when a surface repeats. The source stays unchanged. Listening hides text
until reveal, then identifies the target and shows meaning. Full sentences reveal
translation rather than pretending to be lexical words.

Web Speech uses the existing shared channel and the target locale (`nl-NL` for Dutch).
New playback cancels previous playback; leaving Review stops speech. No backend audio,
external TTS, media extraction or original NPO audio was added. Unsupported browser
TTS prevents listening selection, while runtime failure leaves reveal usable.

## 13. Idempotency and failures

HMAC tokens bind owner, target, review version/count and exercise. BEGIN IMMEDIATE
serializes grading across SQLite connections. History insertion and schedule update
commit atomically. An identical replay returns the original result; changed grade,
forged token or stale version is rejected. Tests cover actual two-connection retries.

Frontend locks double taps and advances only after successful submission. A network
failure retains the current card and retries the same token and grade. Reveal failures
remain retryable and do not grade. No bulk generation occurs: missing answers resolve
one item lazily through the existing coalesced/cache-backed services on explicit reveal.

## 14. API endpoints

- GET `/api/reader/review`: summary and up to 10 due targets, oldest due then ID.
- POST `/api/reader/review`, `{listening}`: capability-aware bounded batch.
- POST `/api/reader/review/{target}/answer`, `{}`: lazy reveal.
- POST `/api/reader/review/{target}/grade`, `{grade, exercise, interaction}`.
- POST `/api/reader/{document}/saved-sentences`, `{sentence_id, target_language?}`.
- DELETE `/api/reader/{document}/saved-sentences/{saved_sentence}`.

Every operation uses Telegram initData authentication and owner checks. Clients cannot
provide source text, owner, timestamps or next due date. Unknown fields and malformed
interaction values fail validation. Deleting a saved sentence removes its own state
and history; it does not touch lexical vocabulary.

## 15. Frontend navigation

Existing `/reader` Telegram command → Open Reader → Library → Review → bounded
session → completion → Library. The bot's existing Mini App URL construction remains
unchanged. Reader's sentence controls gain compact Save sentence. Review uses the same
API/auth helper and speech channel. No Chinese Hanly/Mosaic controls appear in Review.

## 16. Files changed

- `.github/workflows/ci.yml`: include Review DOM tests in CI.
- `README.md`: link Review documentation.
- `podcast_bot/storage.py`: additive schema and exact saved spans.
- `podcast_bot/reader/review.py`: targets, scheduling, queue, answer resolution, history.
- `podcast_bot/reader/api.py`: authenticated Review and sentence-save/delete routes.
- `podcast_bot/reader/static/{index.html,app.js,app.css}`: navigation, exercises, speech,
  sentence action and minimal styling.
- `tests/test_reader_review.py`: scheduler, migration, ownership, concurrency and API.
- `tests/frontend/review.test.js`: real handler/DOM review flow tests.
- `tests/frontend/language-tools.test.js`: sentence-save handler and retry regression.
- `tests/frontend/dom.js`: implement standard Node.remove for real reveal behavior.
- `docs/reader-review.md`: architecture, semantics and limits.
- `docs/reader-review-validation.md`: this report.

## 17–23. Validation results

Final code verification on this checkout:

| Suite/check | Result |
| --- | --- |
| Focused Review Python | 25 passed |
| Saved Vocabulary/Dutch tools Python | 42 passed |
| Reader API/Chinese regression (`test_reader`) | 143 passed |
| Reader translations | 50 passed |
| Library/rename/position | 43 passed |
| Reader audio/media | 22 + 19 passed |
| NPO | 76 passed |
| Hanly | 64 + 59 passed |
| Mandarin Mosaic | 32 passed |
| Full Python | **1025 passed in 11.70s** |
| Existing core frontend/DOM | 125 passed |
| Library frontend | 18 passed |
| Selection frontend | 22 passed |
| Dutch frontend (including new sentence-save test) | 14 passed |
| Review frontend/DOM | 9 passed |
| Total frontend/DOM | **188 passed** |
| JS syntax | passed |
| Ruff lint | passed |
| Ruff formatting | 123 files already formatted |
| git diff --check | passed |

Full command: `python -m pytest -q --junitxml=/private/tmp/review-final-python.xml`.
All frontend scripts were run separately. Full tests mock external HTTP/LLM and forbid
unexpected network calls; they do not validate live provider or production behavior.
No existing test was weakened. CI now includes the new Review DOM script.

Manual local browser inspection used synthetic stored Dutch data and the actual
ReaderServer/API/frontend with existing development authentication mode. Walked through
recognition, cloze, listening, full-sentence translation, grading, completion and return
to Library. Reopened Review showed Nothing due and the next due time. Also opened
the actual Reader document and verified Save sentence changed to Sentence saved
for the already-saved sentence without resetting its schedule. Persisted
state across reopening SQLite is also tested. TTS Play was exercised; audible voice
quality and a real Telegram WebView/device were not verified. Production Telegram
authentication is covered by API and real-handler DOM tests, not a live Telegram run.
No public NPO VTT download was needed or committed. The fixture server explicitly
refuses generation, so no paid calls were made.

Evidence outside the repository:
`/private/tmp/review-final-python.log`, `/private/tmp/review-final-python.xml`,
`/private/tmp/review-final-{app,library,selection,language,review}.log`,
`/private/tmp/review-sentence-preview.png`.

## 24. git diff --stat

The exact unstaged tracked-file output (Git does not include untracked files):

```text
 .github/workflows/ci.yml              |   1 +
 README.md                             |   2 +
 podcast_bot/reader/api.py             |  70 ++++++++++++-
 podcast_bot/reader/static/app.css     |   8 ++
 podcast_bot/reader/static/app.js      | 181 +++++++++++++++++++++++++++++++++-
 podcast_bot/reader/static/index.html  |   8 ++
 podcast_bot/storage.py                |  47 +++++++--
 tests/frontend/dom.js                 |   6 ++
 tests/frontend/language-tools.test.js |  12 +++
 9 files changed, 325 insertions(+), 10 deletions(-)
```

New untracked implementation/documentation files, in addition:

```text
docs/reader-review.md: 137 new lines
podcast_bot/reader/review.py: 303 new lines
tests/frontend/review.test.js: 98 new lines
tests/test_reader_review.py: 344 new lines
docs/reader-review-validation.md: this report
```

## 25. git status --short

```text
 M .github/workflows/ci.yml
 M README.md
 M podcast_bot/reader/api.py
 M podcast_bot/reader/static/app.css
 M podcast_bot/reader/static/app.js
 M podcast_bot/reader/static/index.html
 M podcast_bot/storage.py
 M tests/frontend/dom.js
 M tests/frontend/language-tools.test.js
?? docs/reader-review-validation.md
?? docs/reader-review.md
?? podcast_bot/reader/review.py
?? tests/frontend/review.test.js
?? tests/test_reader_review.py
```

All changes are local and unstaged; there are no commits beyond the base.

## 26–27. Limitations and Review v2

Review v1 uses authentic Reader contexts. Generated practice sentences, original NPO
audio and Chinese internal Review are not implemented. Chinese Reader, segmentation,
pinyin and external export regression suites pass; live external integration calls
were not made. No adaptive selector, typed production, shadowing, multiple contexts,
offline review queue, statistics or gamification was added.

Legacy ambiguous spans do not get cloze. Existing vocabulary deduplication retains
the first selected occurrence in the same sentence. Language support follows existing
internal-save capabilities. Browser API presence cannot guarantee an installed Dutch
voice. Missing meanings/translations may require one ordinary lazy generation request.
Local visual checks do not establish mobile voice quality or production deployment.

Revisit for v2: calibrated/FSRS scheduling and state migration, multiple contexts per
learning target, explicit review-language capabilities, legacy-span recovery, per-context
answer language, history retention when deleting sentences, original audio provider
adapters and comprehensive Chinese review QA.

## 28. Suggested commit

`feat(reader): add contextual review sessions`

No commit, push or deployment was performed.
