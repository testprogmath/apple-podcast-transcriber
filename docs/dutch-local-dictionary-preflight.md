# Dutch local dictionary: preflight stopped

Historical checkpoint: superseded by [the raw-source gate](dutch-local-dictionary-raw-source.md)
and [production implementation](dutch-local-dictionary.md).

Checked 2026-10-15 against freshly fetched main
`04c0c468407a06297cadef88a80fed66c758407a` in branch
`feature/dutch-local-dictionary`.

## Decision

**Merge blocker: unresolved provenance of the evaluated postprocessed source.**
The implementation request explicitly requires stopping when source provenance
makes redistribution unclear. No production code or dictionary artifact was built.
This is an evidence gap, not a finding that Kaikki data is prohibited.

The publisher states that the per-language postprocessed data includes information
merged from other sources, without identifying those additions on the download page:
https://kaikki.org/dictionary/Dutch/index.html
The same page deprecates this distribution and recommends raw Wiktextract instead.

The local evaluated source was scanned in full and matches the requested pin:

- File: `/private/tmp/dutch-dictionary-spike-data/dutch.jsonl`
- Bytes: 256173778
- Records: 146492
- SHA-256: `00c8ee4a95169bb1ec0ac9b495d43dc7746a1e59e9a44914bae8909f9f167121`
- Publisher extraction: 2026-10-03; enwiktionary dump: 2026-09-02.

No license/copyright/provenance fields were found at any nesting level. Source
fields occur in categories, forms and thesaurus relationships; examples include
`w`, `w+disamb`, `declension`, `Thesaurus:hallo`. One top-level `source` is
`thesaurus`. These are not a mapping of merged external content to its licenses.
This scan cannot establish which projected gloss/form fields were externally merged.
Simply excluding audio, examples and categories does not establish that distinction.

The Wiktextract repository licenses its software, which does not by itself settle
rights in the publisher's additional postprocessing inputs:
https://github.com/tatuylonen/wiktextract#license

## Current architecture and implementation plan

Inspected `reader/api.py`, `reader/lexical.py`, `reader/languages.py`,
`reader/dictionary.py`, `reader/review.py`, `storage.py`, the lexical inspector in
`reader/static/app.js`, and `Dockerfile`. Inspected spike documentation and builder
as research only; no spike files were copied or modified.

1. Add a streaming, offline, checksum-verified builder and normalized SQLite schema
   for entries, senses, grammatical/usage labels, explicit forms and references.
   Retain lexical records and sense-dependent article evidence separately.
   Resolve references with bounded traversal and cycle protection. Index normalized
   lemma and surface. Store source/schema/builder metadata and run integrity checks.
2. Add a Dutch read-only service independent of Reader Storage and the existing
   Chinese dictionary. Validate schema, source identity and required tables;
   missing, corrupt or incompatible artifacts return explicit unavailable status.
   Keep deterministic candidate ordering without claiming contextual disambiguation.
3. Extend the existing authenticated, document-owner-scoped lexical endpoint with
   an explicit local lookup mode, using the existing canonical span validation.
   It must return without calling or waiting for `ContextualLexical.resolve`.
   Keep existing contextual meaning and save payloads independent.
4. Extend the existing popup with separate dictionary and contextual sections.
   Local responses must render independently; contextual errors/retries must preserve
   dictionary evidence. Main currently has a 45-second backend contextual deadline,
   but no frontend deadline/empty-success guard in `loadContextualMeaning`.
   Existing uncommitted fixes in other worktrees must remain untouched.
5. Do not alter saved vocabulary, exact-span ownership, Review state or scheduling.
   Review currently uses cached saved meaning or its existing contextual service.
   Dutch tokens contain surface spans, not reliable POS annotations.
6. Prefer a reproducible, pinned deployment build or verified external artifact,
   keeping raw JSONL/generated binaries out of git. Existing Docker builds Chinese
   CC-CEDICT offline from a committed small source snapshot; the 244 MiB Dutch
   source does not fit that strategy without an explicit acquisition/cache step.
   A final artifact strategy requires resolved provenance and measured compact size.
7. Add focused fixture/API/DOM tests, full backend/frontend regressions and quality
   checks, then measure the real artifact, requested examples and NPO coverage.
   Dictionary evidence must remain distinct from contextual meaning.

## Unblocking requirements and classifications

- **Merge blocker:** establish applicable provenance/licenses of the projected
  fields in this exact snapshot, e.g. publisher documentation identifying merged
  sources and their scope. Alternatively use a vetted raw English-Wiktionary
  extraction, pin/checksum it and repeat coverage/morphology validation.
- **Follow-up:** the publisher offers raw English-edition extraction with English
  glosses across languages; filter `lang_code=nl`. Its published size is 23.9 GB
  or 2.8 GB gzip. The separate Dutch-edition raw file is not an equivalent source
  for English glosses. https://kaikki.org/dictionary/rawdata.html
- **Acceptable v1 limitations (planned):** ambiguity, unknown article/plural,
  missing compounds/phrases; no generated morphology or automatic disambiguation.
- **No issue observed:** an independent local lookup fits current API/inspector
  boundaries without a large unrelated refactor.

No production schema, generated artifact, build timings, latency, new coverage
measurements or real-artifact examples exist for this branch. Prior spike results
are not relabeled as production measurements. No test suites were run because only
this preflight document changed; `git diff --check` was run. No commit, push or deploy.
