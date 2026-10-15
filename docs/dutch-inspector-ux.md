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
initial senses, one “Meaning in context” value, then the source sentence. Additional
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
cache separation and unchanged non-Dutch behavior. Six additional DOM cases cover
one contextual result, hidden legacy prose, target selection, source disclosure,
entry/license links, order and provider unavailability. Existing failure, timeout,
miss, ambiguous noun, Save and Chinese tests also pass.

- Focused Dutch inspector/dictionary/Reader language tests: **96 passed**.
- Full Python suite: **1126 passed**.
- Full frontend/DOM: **205 passed** (125 core, 31 Dutch, 18 library, 22 selection,
  9 Review).
- Ruff lint/format, JS syntax and `git diff --check`: passed.

Browser acceptance used the real existing local dictionary and mocked contextual
responses for the two exact production sentences. oorlog shows `de oorlog · oorlogen`,
`war`, `война`; vrijheid shows `de vrijheid · vrijheden`, `freedom`, `свобода`.
Native source disclosure expansion exposed the entry and license links. Preview
server/tab were stopped after verification. No paid/live provider calls were made;
these checks verify request semantics/parsing/presentation, not live model quality.

Final review: **MERGE BLOCKER: none**. **ACCEPTABLE V1 LIMITATION:** structural
validation and prompt cannot guarantee semantic translation quality; long outputs
are conservatively unavailable. **FOLLOW-UP:** evaluate live contextual quality
only in an explicitly authorized provider run. **NO ISSUE:** artifact/schema,
builder/distribution/morphology, Saved Vocabulary/Review, Chinese, Hanly and Mosaic
implementation remain unchanged. No push or deployment is part of this task.
