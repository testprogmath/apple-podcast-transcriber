"""Hanly personalized-note text. Pure formatting: no network, no generation."""

SOURCE_LABEL = "原文："
TRANSLATION_LABEL = "Перевод："


def build_hanly_note(
    glyph_translation: str | None,
    source_sentence: str,
    sentence_translation: str | None,
) -> str:
    """Compose a note from material that already exists. Missing parts are omitted, never labelled."""
    meaning = (glyph_translation or "").strip()
    sentence = (source_sentence or "").strip()
    translation = (sentence_translation or "").strip()
    lines = []
    if sentence:
        lines.append(SOURCE_LABEL + sentence)
        if translation:
            lines.append(TRANSLATION_LABEL + translation)
    return "\n\n".join(section for section in (meaning, "\n".join(lines)) if section)
