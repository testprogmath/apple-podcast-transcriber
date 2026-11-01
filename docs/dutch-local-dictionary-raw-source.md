# Dutch raw-source provenance and data-equivalence gate

Research only, 2026-10-15. Worktree `/private/tmp/apple-podcast-transcriber-dutch-local-dictionary`,
branch `feature/dutch-local-dictionary`, base `04c0c468407a06297cadef88a80fed66c758407a`.
No Reader production changes and no final production artifact.

## Source evidence: documented facts

The [official raw download page](https://kaikki.org/dictionary/rawdata.html) identifies
an English-Wiktionary extraction, English glosses, multiple languages, dump
2026-09-02, extraction 2026-10-03. It links this gzip-compressed JSONL:

https://kaikki.org/dictionary/raw-wiktextract-data.jsonl.gz

HTTP metadata captured locally: Content-Length 2981058381, Last-Modified
2026-10-03 08:24:38 UTC, ETag `"6ac0bbc6-b1af574d"`. The download URL is mutable;
a reproducible future build must pin the computed checksum, not just this URL.
No published checksum was identified on the download page.

The page identifies Wiktextract revision
[`1a05e46f9efbccda6a2b2f8e21b30a9c0c46513a`](https://github.com/tatuylonen/wiktextract/commit/1a05e46f9efbccda6a2b2f8e21b30a9c0c46513a)
and Wikitextprocessor revision
[`e3d6d4edb77618f4d6680edc66e3f774bea59820`](https://github.com/tatuylonen/wikitextprocessor/commit/e3d6d4edb77618f4d6680edc66e3f774bea59820).

In [upstream issue #1178](https://github.com/tatuylonen/wiktextract/issues/1178),
the maintainer distinguishes the recommended extraction from the later website
processing: alphabetical sorting, identifiers and disambiguation are website work.
The [July 28 maintainer clarification](https://github.com/tatuylonen/wiktextract/issues/1178#issuecomment-3125447535)
describes postprocessed data as the same data with added website-specific material.
The issue also explicitly recommends streaming JSONL and filtering `lang_code`.
This is stronger evidence than inferring provenance from the word "raw" alone.

## Pipeline evidence: code-derived inference

The exact linked Wiktextract revision was checked out for inspection outside the
application worktree. Relevant paths, all at that revision:

- [`wiktwords.py:427–458`](https://github.com/tatuylonen/wiktextract/blob/1a05e46f9efbccda6a2b2f8e21b30a9c0c46513a/src/wiktextract/wiktwords.py#L427): CLI takes the dump and passes it to extraction. It supports optional override files; the bundled overrides contain no `en.json` at this revision.
- [`wiktionary.py:90–145`](https://github.com/tatuylonen/wiktextract/blob/1a05e46f9efbccda6a2b2f8e21b30a9c0c46513a/src/wiktextract/wiktionary.py#L90): `parse_wiktionary` passes the XML dump to `process_dump`, then runs extraction; `write_json_data` serializes records directly as JSONL.
- [`wiktionary.py:37–85`](https://github.com/tatuylonen/wiktextract/blob/1a05e46f9efbccda6a2b2f8e21b30a9c0c46513a/src/wiktextract/wiktionary.py#L37): `page_handler` reads saved page bodies and calls `parse_page`. Redirects can be records without `word`/`lang_code`.
- [`wiktionary.py:764–828`](https://github.com/tatuylonen/wiktextract/blob/1a05e46f9efbccda6a2b2f8e21b30a9c0c46513a/src/wiktextract/wiktionary.py#L764): extraction iterates saved pages, writes parsed records, and emits Wiktionary thesaurus-only entries.
- [`thesaurus.py`](https://github.com/tatuylonen/wiktextract/blob/1a05e46f9efbccda6a2b2f8e21b30a9c0c46513a/src/wiktextract/thesaurus.py#L82): thesaurus data comes from the dump's Thesaurus namespace, not a separate lexical publisher.

Inference: this output stage is Wiktionary-derived JSONL before the website's
opaque merging stage. The traced extraction path does not merge an external lexical
dataset. Classifier/tag mappings and template-expansion code are transformations,
not evidence of an external Dutch dictionary being appended.

Limit: the hosting/deployment job and exact command that published the gzip were
not independently reconstructed. Optional CLI overrides demonstrate capability,
not evidence that such overrides were used for the published English artifact.
Publisher statements, revision metadata and the observed data comparison must be
considered together; no claim of a cryptographically attested build is made.

## Licensing evidence and distribution scope

The upstream work is English Wiktionary text by Wiktionary contributors, including
its templates and thesaurus. Wiktextract parses/expands and normalizes that work;
our subset operation filters records without changing their bytes.

[Wikimedia Terms of Use §7](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use#7._Licensing_of_Content)
permit reuse subject to underlying licenses, page-linked attribution, retained
additional attribution notices, and identification of modifications. Adapted text
must carry CC BY-SA 4.0 or later as applicable. Imported text and non-text media can
have extra obligations; the extractor's MIT software license does not license data.
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) permits transformation
and redistribution with credit, license notice, change indication and share-alike,
without additional restrictions on those licensed rights.

A future projection must ship Wiktionary-contributor attribution; source page/history
links (retain `original_title` when relevant); Kaikki/Wiktextract credit; the license
notice/link; dump/extraction dates, source URL, checksums and extractor/builder/schema
versions; a clear account of transformations; and any applicable extra notices.
Limit the proposed projection to lexical text/morphology. Do not bundle audio,
images, quotations or assume media metadata grants media-reuse rights.


## Measured extraction and evaluation

- uncompressed_bytes: `25614284530`
- raw_records: `10913997`
- dutch_records: `146492`
- seconds: `183.09313129191287`
- subset_bytes: `222550596`
- subset_sha256: `e63cb76e7cac43b8fa03d886bb0cda388f5ec8354e9f9c4854c2f46a7b4d9ff8`
- compressed_bytes: `2981058381`
- compressed_sha256: `3dac8a09e57827bef493e2e6b552fe917bf3b1fc55dee05c4e4a3702aff5dbdb`

Disk check before download: 259 GiB free. The complete uncompressed stream was never materialized. The gzip was fetched using validated HTTP ranges with a fixed ETag; headers are retained outside git. Source ingestion reads gzip to EOF (including gzip CRC validation), parses every JSON object, and writes only unchanged Dutch lines. The index is an evaluation-only full-record index, not a proposed production artifact.

Evaluation index: 333844480 bytes; build 6.612 s. Source processing time above excludes network download and index construction. Download ran from approximately 14:27 UTC on 2026-10-15; segmented downloader timing is recorded in the local log.

Corpus: 667 cues, 405 sentences, 5179 lexical occurrences, 1439 normalized keys. VTT SHA-256 `63adf80a9577d03bf24a9d48aae31873739def8abd9a2a5b56b158a7e5c5b041`. Same normalizer, tokenizer and evaluation functions as the spike, imported read-only by explicit path.

| Metric | Postprocessed | Raw |
|---|---:|---:|
| Records | 146492 | 146492 |
| A exact headword | 1308/1439 (90.9%) | 1308/1439 (90.9%) |
| B direct lexical | 860/1439 (59.8%) | 860/1439 (59.8%) |
| C form-aware | 1345/1439 (93.5%) | 1345/1439 (93.5%) |
| D usable POS | 1345/1439 (93.5%) | 1345/1439 (93.5%) |
| E lexical English gloss | 1345/1439 (93.5%) | 1345/1439 (93.5%) |
| F noun article | 587/587 (100.0%) | 587/587 (100.0%) |
| G noun explicit plural | 539/587 (91.8%) | 539/587 (91.8%) |
| H noun same-record triple | 539/587 (91.8%) | 539/587 (91.8%) |
| I strict noun triple | 304/587 (51.8%) | 304/587 (51.8%) |
| Ambiguous distinct lemma/POS | 489/1345 (36.4%) | 489/1345 (36.4%) |
| Unresolved | 94/1439 (6.5%) | 94/1439 (6.5%) |

Article/plural denominators count surfaces with at least one noun candidate; these are not gold-standard sentence POS annotations. H uses an English lexical gloss on the same noun record. I requires one lemma/POS identity, a consistent single article, explicit plural and an unflagged gloss across noun records. Historical senses remain available; the unflagged restriction is a diagnostic metric only. Candidate coverage is not contextual accuracy.

## Manual nouns from the raw evaluation index

| Noun | Article evidence | Explicit plural(s) | Explicit diminutive(s) | First lexical gloss / sense count |
|---|---|---|---|---|
| [zolder](https://en.wiktionary.org/wiki/zolder) | masculine → de | zolders | zoldertje | attic (space, often unfinished and with sloped walls, directly below the roof); 1 glosses |
| [huis](https://en.wiktionary.org/wiki/huis) | neuter → het | huizen | huisje | a house, home; residence; 5 glosses |
| [kind](https://en.wiktionary.org/wiki/kind) | neuter → het | kinderen, kinders | kindje, kindertje, kindeken, kindelijn | child, kid, non-adult human; 3 glosses |
| [bevrijding](https://en.wiktionary.org/wiki/bevrijding) | feminine → de | bevrijdingen | bevrijdinkje | liberation; 1 glosses |
| [oorlog](https://en.wiktionary.org/wiki/oorlog) | masculine → de | oorlogen | oorlogje | war; 1 glosses |
| [greep](https://en.wiktionary.org/wiki/greep) | masculine → de | grepen | greepje | grasp, clutches; 4 glosses |
| [greep](https://en.wiktionary.org/wiki/greep) | feminine → de | grepen | greepje | grip, handle; 1 glosses |
| [jaar](https://en.wiktionary.org/wiki/jaar) | neuter → het | jaren | jaartje | year (the time it takes the Earth to circle the Sun; its equivalent on other planets); 2 glosses |
| [mens](https://en.wiktionary.org/wiki/mens) | masculine → de | mensen | mensje | human, any member of the species Homo sapiens; 2 glosses |
| [mens](https://en.wiktionary.org/wiki/mens) | neuter → het | mensen | mensje | woman; 1 glosses |
| [stad](https://en.wiktionary.org/wiki/stad) | feminine → de | steden | stadje | city, town; 2 glosses |
| [schip](https://en.wiktionary.org/wiki/schip) | neuter → het | schepen | scheepje | ship (a large, water borne vessel); 2 glosses |
| [museum](https://en.wiktionary.org/wiki/museum) | neuter → het | musea, museums | museumpje | museum; 1 glosses |
| [water](https://en.wiktionary.org/wiki/water) | neuter → het | waters, wateren | watertje | water (H₂O); 3 glosses |
| [meisje](https://en.wiktionary.org/wiki/meisje) |  → unknown | meisjes | none | none; 0 glosses |
| [meisje](https://en.wiktionary.org/wiki/meisje) | neuter → het | meisjes | none | girl (especially of a premature age); 2 glosses |
| [jongen](https://en.wiktionary.org/wiki/jongen) | masculine → de | jongens | jongetje | a boy, any male child; 6 glosses |
| [jongen](https://en.wiktionary.org/wiki/jongen) |  → unknown | none | none | none; 0 glosses |
| [politie](https://en.wiktionary.org/wiki/politie) | feminine → de | none | none | police; 2 glosses |
| [regering](https://en.wiktionary.org/wiki/regering) | feminine → de | regeringen | regerinkje | governing (act of; ruling a state or similar political entity); 3 glosses |

## Inflected surfaces from raw data

| Surface | Candidate lemma / POS |
|---|---|
| huizen | huis [noun]; huizen [verb]; Huizen [name] |
| kinderen | kind [noun] |
| steden | stad [noun]; steden [verb] |
| schepen | schip [noun]; schepen [noun]; schepen [verb] |
| musea | museum [noun] |
| jaren | jaar [noun] |
| jongens | jong [noun]; jongen [noun]; jongens [noun]; jongens [intj] |
| geschreven | schrijven [verb] |
| liep | lopen [verb] |
| kwam | komen [verb] |
| groter | groot [adj] |

All 11 expected lemmas are present. Exact projected manual noun equality with the old evaluation: True. Exact projected inflected candidate equality: True. Ordering differences are evaluated separately below; no first-candidate correctness claim is made.

## Identity and evidence differences

```json
{
  "identity": "original word, POS, etymology_number; record multiplicity retained",
  "old_identities": 145589,
  "raw_identities": 145589,
  "only_old_count": 0,
  "only_raw_count": 0,
  "only_old_examples": [],
  "only_raw_examples": [],
  "changed_evidence_counts": {
    "senses": 0,
    "forms": 0,
    "head_templates": 0,
    "sounds": 0
  },
  "order_insensitive_changed_counts": {
    "senses": 0,
    "forms": 0,
    "head_templates": 0,
    "sounds": 0
  },
  "changed_evidence_examples": {
    "senses": [],
    "forms": [],
    "head_templates": [],
    "sounds": []
  }
}
```

## Schema compatibility

The attached metrics JSON contains record/sense/form field counts. Extraction reuses `word`, `pos`, lexical `senses[].glosses/tags`, explicit `forms[].form/tags`, `form_of`, `alt_of`, head templates and sounds. Generic `common` is not a gender; only masculine/feminine/common-gender map to de and neuter to het. Form metadata is not article evidence; diminutive plurals are excluded from base plurals. No suffix inference is used.

## Origin and significance of differences

There are **zero identities exclusive to either dataset**, including exact spelling,
POS and etymology number, and matching multiplicity in the evidence comparison.
There is no postprocessed-only entry to classify as a likely external addition.
Do not generalize this snapshot-specific result to all languages or later releases.

For all shared identities, the checked lexical sense projection (`glosses`, `tags`,
`form_of`, `alt_of`), complete forms, complete head templates and complete sounds
match as multisets of records, including order inside those fields. Thus no important
morphology or lexical-gloss loss was observed. This does not claim every source field
is byte-identical or that record order is the same.

Representative **schema normalization / explained postprocessing**:
`zolder` raw sense categories are string names, including Dutch morphology and
etymology categories. Postprocessed categories are objects with `name`, `kind`,
`parents`, `source`, sometimes `orig`/`langcode`, with many original categories removed.
These fields are excluded from the lexical-evidence equality comparison.

Other top-level field-presence differences:

| Field present on records | Postprocessed | Raw |
|---|---:|---:|
| categories | 471 | 43108 |
| derived | 5627 | 16233 |
| related | 1977 | 7957 |
| synonyms | 863 | 4653 |
| hypernyms | 40 | 286 |
| translations | 0 | 1 |

These are consistent with documented website filtering/disambiguation and movement
or removal of ancillary data. The exact proprietary website transformation for each
relationship was not reconstructed: classify those individual transformations as
**unresolved**, not as proven external additions. They do not affect the measured
projection. The smaller raw subset is therefore not evidence of lower lexical coverage.
No likely-external-addition claim is supported by the observed Dutch identity diff.

## Ambiguity and grammatical evidence

`mens` has a masculine record for human/person and a separate neuter record for
woman, explicitly labelled **derogatory, informal**. Both datasets agree. Articles
must remain linked to their respective senses/records, not presented as interchangeable.
`huizen` retains noun `huis`, verb `huizen`, and proper-name `Huizen`.
`jongens` retains noun candidates `jong`, `jongen`, `jongens` and an interjection;
the reader must not turn this set into an authoritative first-row answer.

The noun table deliberately includes the separate form-reference-only records for
`meisje` and `jongen`: their empty lexical gloss/article is not a missing lexicalized
entry. `meisje` also has its own lexical neuter noun entry, which remains intact.
The complete manual projection and all its glosses are in the metrics JSON.

Raw noun-sense/form evidence matches the spike. Observed sense-tag counts include
neuter 35442, masculine 23839, feminine 21777, common-gender 368, generic common 7.
Generic common is never mapped to an article. Among 35673 lexical noun records,
35349 have article evidence, 30134 explicit base plurals, and 20672 explicit singular
diminutives. These whole-dictionary denominators differ from corpus noun surfaces.
Historical labels are retained; the conservative strict metric is not a deletion rule.

No new compound splitting, correction, stemming or contextual disambiguation was used.
The 94 unresolved corpus keys remain a contextual-fallback requirement, not a reason
to synthesize dictionary evidence.

## Licensing gate answers

A. **Upstream copyrighted work:** English Wiktionary lexical text, templates and
thesaurus by its contributors. It can contain material already imported into
Wiktionary; retain applicable attribution notices. This is distinct from an opaque
external dictionary appended by Kaikki.

B. **Transformation:** the published English-edition Wiktextract extraction described
above, followed here by unchanged-line filtering to `lang_code=nl`. Future SQLite
projection would be another explicitly documented transformation.

C. **External lexical datasets merged into this raw artifact:** publisher documentation
and the traced output path support a Wiktionary-derived extraction before website
postprocessing. No separate external lexical merge was found in that path; all
146492 Dutch records have matching identities/evidence in the evaluated snapshot.
This is a documented/code-supported provenance assessment, not independent attestation
of the publisher's entire server. The opaque website pipeline is not needed for adoption.

D. **Obligations:** use the applicable Wiktionary text license, CC BY-SA 4.0 for the
planned adapted text projection; retain credit, source links, supplied extra notices,
license notice/link, change indication and share-alike. Exclude media and quotations
from this projection. An actual [English Wiktionary entry footer](https://en.wiktionary.org/wiki/zolder)
links the Creative Commons text license and warns that additional terms can apply.

E. **Redistribution assessment:** a Dutch-only SQLite text/morphology projection can
be created and redistributed under those conditions. No evidence found here requires
an unidentified additional lexical-source license. This pass does not build or ship
that projection or certify compliance of a future implementation.

F. **Metadata to ship:** contributor and Kaikki/Wiktextract attribution, original
page/history links, applicable extra notices, CC BY-SA notice/link, source URL,
dump/extraction date, compressed/subset checksums, extractor revisions, and
projection/builder/schema version with a clear statement of filtering/transformations.
Keep dictionary data licensing separate from application code licensing.

G. **Remaining material provenance uncertainty:** none identified for adopting this
pinned raw text/morphology source instead of website-postprocessed data. The unavailable
hosting command is a reproducibility limit; optional overrides are an upstream capability,
not evidence of their use. Ordinary Wiktionary license compliance still must be implemented.

## Recommendation

**PROCEED WITH RAW SOURCE**

The raw route removes reliance on the opaque website transformation and loses no
measured lexical evidence or NPO coverage in this snapshot. Keep candidates and
sense-dependent articles; dictionary evidence is not contextual meaning.
Do not resume Reader integration automatically: this recommendation resolves only
the source/data gate. The earlier preflight stop remains a record of what was known
before this investigation.

## Reproduction and validation

Data and full per-token results stay outside git in
`/private/tmp/dutch-dictionary-spike-data/`. The small repository metrics JSON excludes
per-token rows. The existing spike module was used as research reference only;
no production dependency on it is proposed.

```sh
python -m tools.dutch_raw_source_gate \
  --gzip /private/tmp/dutch-dictionary-spike-data/raw-en.jsonl.gz \
  --subset /private/tmp/dutch-dictionary-spike-data/raw-dutch.jsonl \
  --index /private/tmp/dutch-dictionary-spike-data/raw-evaluation.sqlite3 \
  --old-index /private/tmp/dutch-dictionary-spike-data/evaluation-final.sqlite3 \
  --reference /private/tmp/apple-podcast-transcriber-dutch-dictionary-evaluation/tools/dutch_dictionary_spike.py \
  --vtt /private/tmp/dutch-dictionary-spike-data/VPWON_1361725.vtt \
  --output /private/tmp/dutch-dictionary-spike-data/raw-gate-results.json
```

Use fresh subset/index/output paths on rerun: accidental overwrite is refused.
The evaluation index is 333844480 bytes, built in 6.612 s; this is a full-record research
index, **not** a compact production artifact. Warm form-aware lookup over 14390 calls:
median 94.729 µs, p95 485.042 µs, max 47.440 ms. Warm timings do not promise cold-start latency.


Validation: new streaming/comparison tests **3 passed**; reused spike evaluation tests
**7 passed** (article tags including generic common, plurals/diminutives, references,
ambiguity, Unicode, cycles and the separate historical FreeDict parser fixture).
Ruff check passed; Ruff format check passed for all 129 Python files;
`git diff --check` passed. No full application suite was required: production code
was untouched. Final comparison was independently rerun with the final research
script and matched the saved results exactly.

The dictionary gloss examples and derived dictionary data in the metrics JSON are
attributed to Wiktionary contributors via the linked entry pages (with histories),
extracted by Wiktextract and provided by Kaikki, transformed for this comparison;
these dictionary data excerpts are provided under CC BY-SA 4.0:
https://creativecommons.org/licenses/by-sa/4.0/
No endorsement is implied. This data notice is distinct from the repository code license.
