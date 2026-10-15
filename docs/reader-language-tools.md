# Reader language tools

Capabilities are serialized by `reader.languages.capabilities(document.language)`.
Chinese keeps existing segmentation, pinyin, shared Mandarin speech, Hanly and
Mosaic. Dutch gets local word spans, contextual inspection, `nl-NL` speech and
internal save. Dutch tools do not depend on NPO source type. Unknown languages
retain plain text without these tools. Pasted/file constructors and the documents
POST endpoint accept optional `language: "nl"`; the default remains Chinese.
The import UI does not introduce a new language selector.

Dutch word boundaries are Unicode alphabetic runs with combining marks and
internal apostrophes (`'`, `’`, `ʼ`) or hyphens. Tokens retain sentence-local Python
code-point start/end offsets; punctuation/whitespace stays untouched. This is
surface-form interaction. A separate [local Dutch dictionary](dutch-local-dictionary.md)
now provides explicit lexical/morphological evidence, independently of contextual generation.
Dutch capabilities include `local_dictionary: "dutch"`; Chinese capabilities are unchanged.

## API and contextual meanings

Authenticated document ownership is checked before every contextual/save request.

- `POST /api/reader/{document_id}/lexical` (`mode: "dictionary"` for local Dutch evidence; omitted/`"contextual"` for generated meaning)
- `POST /api/reader/{document_id}/saved-vocabulary`
- `GET /api/reader/{document_id}/saved-vocabulary`

POST bodies contain `sentence_id`, code-point `start`/`end`, exact `text`, and
optional `target_language` (`ru` default, or `en`, matching the existing Reader
RU/EN preference). Server text must match the canonical sentence slice; it trims
surrounding whitespace and caps the selection at 200 characters. It rejects
unknown/stale spans, cross-sentence text, extra fields and unsupported languages.
Source sentence, language, timing and owner are derived on the server.

Lexical generation reuses the sentence translation service's configured
`OpenAIStudyClient` instance and model; it does not precompute on document open.
Input contract:

```json
{
  "selected_text": "in de greep van",
  "source_sentence": "Het land was in de greep van oorlog.",
  "source_language": "nl",
  "target_language": "ru"
}
```

Strict output: `meaning` (nonempty plain text, up to 200 characters), optional
`context_note` (up to 300). Extra fields/types/oversize or clearly wrong output
language are refused. The instruction requests a compact contextual sense of
the whole selection, no whole-sentence translation, morphology or etymology.
It treats source inputs as untrusted quoted data. This validates shape and coarse
language, not semantic correctness of a generated sense.

Cache key hashes owner, document ID, sentence ID, source language, NFC/casefolded
surface with collapsed whitespace, complete exact source sentence and target
language. Same surfaces in different sentences/documents or target languages do
not share meanings. Concurrent identical lookups coalesce; a two-slot semaphore,
eight pending request bound and 45-second timeout limit generation. Failures are
not cached, expose no raw exceptions, and can be retried explicitly.
Sentence translation keeps its own existing cache and reveal interaction.

## Backward-compatible SQLite additions

No existing table is rewritten. `CREATE TABLE/INDEX IF NOT EXISTS` adds:

- `reader_contextual_lexicon`: context hash, owner, meaning, context note, created.
- `saved_vocabulary`: ID, owner, source language, exact text, normalized text,
  document ID, sentence ID, complete source sentence, source start/end in seconds,
  target language, meaning, context note, created/updated.
- `saved_vocabulary_owner_document` index for owner/document queries.

Saved uniqueness is `(owner_id, language, normalized_text, document_id,
sentence_id, source_sentence)`. Repeated occurrences of the same normalized item
in one sentence/context collapse; different sentences/documents or changed source
sentences do not. First exact text/created time is retained; repeated saving can
update available cached meaning/target and updated time. No destination IDs are
required. GET and document serialization include only that document owner's items.

Save does not require a provider, Hanly or Mosaic. A word uses cached meaning if
available; expression Save attempts one contextual phrase lookup and still saves
when it fails. No custom lexical override or tokenization mutation is performed.
Native selection includes source nodes only, requires one sentence, uses Unicode
code-point offsets and trims surrounding whitespace. Saved status persists across
reopening; changing an item is not a new SRS/export flow. No deletion UI is added.

## Speech and UI

The existing inspector and compact sentence controls are reused. Dutch hides
pinyin, dictionary attribution, Hanly/Mosaic counters and Mosaic sentence marking.
Contextual lookup failure leaves pronunciation, Save and an explicit retry usable.
Asynchronous results cannot overwrite a subsequently opened word or changed target.

Word and sentence speech share the existing utterance channel and stop previous
word/sentence speech. `nl-NL` is requested; a voice tagged nl-NL/nl_NL is preferred,
then any nl voice. With no matching enumerated voice, the browser chooses from the
utterance language. Voice availability and actual pronunciation depend on the OS
and Telegram WebView. Dutch sentence speech bypasses original media playback;
existing NPO source timestamps remain in persistence/API and saved items.

## Validation and limits

Backend/DOM tests cover source-neutral Dutch documents, canonical spans, contexts,
ownership, persistence/migration, deduplication, targets, failure/retry, native
selection, shared TTS and Chinese/NPO regressions. Local browser review uses real
NPO VTT outside the repository and mocked model responses. It verifies inspector,
phrase selection, requests, persistence, translation and speech controls, without
paid model calls or deployment. Model semantic quality and device-specific iOS
Telegram speech/selection behavior still require live user review. There is no
Dutch dictionary, lemma inference, SRS, export or original NPO playback.
