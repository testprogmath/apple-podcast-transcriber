# Study pack and validation

[Back to the README](../README.md)

For Mandarin with Russian as the learner's native language, the bot sends these individual files as one Telegram document group:

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

## Study validation and long transcripts

The official Responses API returns structured Pydantic-validated JSON; Python renders Markdown/CSV/ZIP deterministically. Model output is never interpreted as an arbitrary CSV or Markdown document.

Transcripts are split at paragraph/sentence boundaries into blocks of at most 1200 characters and bounded request batches (default 3000 characters). A hard 300,000-character transcript safety limit prevents an uncontrolled request count. Extremely long unpunctuated spans may need a hard character boundary.

Each study response must cover source blocks exactly once, in order, preserving every Chinese source character apart from reading-line whitespace. Citation text is inserted from a deterministic catalogue of transcript segments: the model selects a schema-constrained segment ID for vocabulary examples, grammar/culture notes, ASR quotations and Mosaic sentences. It cannot write or paraphrase those citation fields. Translations and explanations remain generated. The same selection protocol applies to non-Chinese vocabulary examples. Global ranking over bounded candidate batches removes duplicate/overlapping vocabulary and picks the strongest episode-wide teaching points. It treats Mosaic sentences as a separate candidate category from vocabulary.

For Mandarin Mosaic:

- Every Chinese sentence must be a verbatim source substring. No paraphrase, simplification, correction, fabricated example, or joining of separated source spans is accepted.
- The model selects self-contained useful utterances and natural constructions; 8–35 characters is guidance, not a truncation rule.
- English is used regardless of `NATIVE_LANGUAGE`. No pinyin, Russian, reasons, tags, or timestamps enter the CSV.
- Deterministic filtering removes obvious support/advertising material and repeated listening sentences. Punctuation-only repetitions and harmless interjection variants are deduplicated; global model ranking handles semantic overlap.
- Reasons are retained in `study.json`. Timestamp fields remain null because the study stage does not invent audio alignment.
