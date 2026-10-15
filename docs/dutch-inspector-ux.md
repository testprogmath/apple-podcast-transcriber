# Dutch lexical inspector cleanup

Base: `5a1cd3690c5d19e4dc8c7f7ed878ba2c4ada006b` (fresh main containing
both the local dictionary and distribution implementation).

## Contextual translation contract

Previously `ContextualLexical` asked for `meaning` plus optional `context_note`.
The sheet displayed the first under “Meaning in context”, then a “Generated
contextual meaning” badge and the note after the source sentence. Both values
came from the contextual provider, independently of Wiktionary evidence.

Only Dutch now uses `DutchTranslation`, a strict structured response containing
one `translation` string and no extra properties. Its prompt asks for a natural
lexical/phrase equivalent of `selected_text` as used in `source_sentence`, in the
existing RU/EN `target_language`. It permits idiomatic multiword equivalents,
forbids explanation/meta-preambles and does not require a difference from a generic
dictionary gloss. Inputs remain quoted untrusted data. No model call sees or alters
dictionary glosses, article evidence or morphology.

The schema bounds output to 120 characters. Parsing rejects line breaks, more than
16 whitespace-separated words, empty/unavailable results and invalid target-language
output through the existing safe/retryable error path. Output is never truncated,
and no frontend prefix-stripping regex exists. Bounds are conservative translation
limits, not a one-word restriction or proof of semantic correctness.

For API/storage compatibility, the result maps to the existing `meaning` column
and an empty `context_note`; no migration or Saved Vocabulary/Review model change.
Historical saved values are untouched. A Dutch-only cache-key contract version
prevents previous explanatory cache entries from being reused. Old cache rows are
retained; with no provider and no new-contract cache entry, lookup reports unavailable.
Other languages keep their exact prior prompt, response schema and cache identity.

## Presentation and attribution

The existing sheet/design remains. Dutch shows dictionary heading/morphology and
initial senses as fallback, one contextual translation, then the source sentence.
For non-English targets, successful contextual translation moves all original English
glosses into “More senses and forms”; morphology remains in the primary card. Loading,
unavailable or failed contextual lookup retains the immediate English fallback.
English targets always show the primary gloss normally. RU labels the contextual
section “Значение в контексте”. This changes rendering only, without mutating source
glosses or the translation contract. Additional
senses/forms and other analyses remain reachable below the sentence. All analyses,
sense-dependent articles, labels and forms are preserved; their resolution is unchanged.
The Dutch generated badge and context note are omitted, including legacy notes in
an older compatible response. Other language inspector presentation is unchanged.

A native keyboard/touch accessible disclosure, “Dictionary source: Wiktionary ⓘ”,
follows useful content. It contains every candidate's original Wiktionary entry link
(including contributor-history access), Wiktionary contributors, Kaikki/Wiktextract,
CC BY-SA 4.0 with its clickable license URL, snapshot and compact-projection credit.
The required existing credit from `dictionary/LICENSE-DUTCH.txt` and the dictionary
documentation is retained rather than deleted or permanently hidden. Distribution
assets/notices are unchanged. The summary is always reachable for a dictionary hit,
including provider failure; misses/unavailable dictionaries do not imply evidence.

Dictionary and contextual requests retain independent state/deadlines. Loading,
provider failure or unavailable provider never hides local data, source disclosure,
pronunciation or Save. Dictionary misses still allow contextual translation.

## Validation and review

15 new deterministic Python cases cover exact oorlog/vrijheid sentences, idiomatic
greep and a selected phrase, RU/EN targets, response shape/verbosity failures,
cache separation and unchanged non-Dutch behavior. Eighteen additional DOM cases cover
one contextual result, hidden legacy prose, target selection, source disclosure,
entry/license links, order, provider unavailability, RU fallback-to-details transitions, source preservation
and EN/RU switching. Existing failure, timeout,
miss, ambiguous noun, Save and Chinese tests also pass.

- Focused Dutch inspector/dictionary/Reader language tests: **96 passed**.
- Full Python suite: **1126 passed**.
- Full frontend/DOM: **217 passed** (125 core, 43 Dutch, 18 library, 22 selection,
  9 Review).
- Ruff lint/format, JS syntax and `git diff --check`: passed.

Browser acceptance used the real existing local dictionary and mocked contextual
responses for the two exact production sentences. oorlog shows `de oorlog · oorlogen`,
`война` in the primary RU view and original `war` in details; vrijheid shows
`de vrijheid · vrijheden`, `свобода` with `freedom` available in details. EN keeps
its English primary gloss. The additional presentation-only pass reran all 209
frontend tests and JS syntax/diff checks; Python code stayed unchanged since the
1126-test full run.
Native source disclosure expansion exposed the entry and license links. Preview
server/tab were stopped after verification. No paid/live provider calls were made;
these checks verify request semantics/parsing/presentation, not live model quality.

Final review: **MERGE BLOCKER: none**. **ACCEPTABLE V1 LIMITATION:** structural
validation and prompt cannot guarantee semantic translation quality; long outputs
are conservatively unavailable. **FOLLOW-UP:** evaluate live contextual quality
only in an explicitly authorized provider run. **NO ISSUE:** artifact/schema,
builder/distribution/morphology, Saved Vocabulary/Review, Chinese, Hanly and Mosaic
implementation remain unchanged. No push or deployment is part of this task.


## Full sentence translation in the inspector

The preceding cleanup was already committed as `e1dbf8b` and `93e0445`; this is
an explicit follow-up in the same worktree/branch. Dutch inspectors now show the
full-sentence translation directly below the Dutch source under “Пример” (RU) or
“Example” (EN). Only the selected Dutch source span is highlighted; no translated
span alignment or extra lexical-provider output is requested.

`translationControl` retains its existing sentence/language cache, pending requests,
validation and endpoint. It exposes that same cache/loader through a view registry
for the inspector. There is one cached value and one pending request per existing
sentence control/target, shared in both directions. Cache hits render synchronously;
backend study/cache lookup remains in the existing `SentenceTranslations` service.
The dictionary request starts first; word and sentence translations load independently.
Failure shows a short localized sentence-only unavailable message and preserves
source, dictionary, lexical translation, speech and Save. Reopening the inspector or
using the existing Reader sentence control can retry. Late results cannot replace
another selected sentence or target language.

Eight new deterministic frontend cases cover RU/EN output and labels, immediate
cache hits without duplicate requests, shared in-flight requests, pending/loading,
failure isolation, reverse cache reuse and stale sentence/language responses.
All **217 frontend/DOM** and **1126 Python** tests passed after this follow-up;
Ruff lint/format, JS syntax and diff checks passed. Browser acceptance used the real
local dictionary plus independently mocked word/sentence translation responses.
No live provider call, push or deployment occurred. Dictionary assets, morphology,
lexical translation semantics, storage schemas, Review and integrations are untouched;
Chinese regression tests pass. Final review found no merge blocker.
