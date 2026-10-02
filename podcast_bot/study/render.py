import csv
import io
import re

from .models import StudyMaterial
from .settings import COLUMN_ROLES, StudySettings


def md(text: str) -> str:
    """Escape inline Markdown/HTML so source text cannot introduce links or formatting."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]#|])", r"\\\1", text)


def pinyin_markdown(material: StudyMaterial) -> str:
    return (
        "\n\n".join(
            "\n\n".join(f"{md(line.source)}  \n{md(line.pinyin)}" for line in passage.lines)
            for passage in material.passages
        )
        + "\n"
    )


def translation_markdown(material: StudyMaterial) -> str:
    return "\n\n".join(md(p.translation) for p in material.passages) + "\n"


def study_markdown(material: StudyMaterial, settings: StudySettings) -> str:
    lines = [f"# Study notes — {md(settings.learner_level)}", "", "## Vocabulary & expressions", ""]
    for v in material.vocabulary:
        lines += [
            f"### {md(v.term)}",
            f"**Pinyin:** {md(v.pinyin)}" if settings.target_language == "zh" else "",
            f"**Meaning:** {md(v.meaning)}",
            f"**Register:** {md(v.register_note)}",
            "**From the podcast:**",
            md(v.example),
            "**Translation:**",
            md(v.example_translation),
            "**Usage:**",
            md(v.usage),
            "",
        ]
    lines += ["## Useful patterns", ""]
    for p in material.patterns:
        lines += [
            f"### {md(p.pattern)}",
            f"**Meaning:** {md(p.meaning)}",
            f"**Usage:** {md(p.usage)}",
            "**From the podcast:**",
            md(p.example),
            "**Translation:**",
            md(p.example_translation),
            "",
        ]
    for heading, notes in [
        (
            "How Chinese speakers actually use this"
            if settings.target_language == "zh"
            else "How speakers actually use this",
            material.pragmatics,
        ),
        ("Idioms & cultural references", material.cultural_references),
    ]:
        if notes or heading.startswith("How"):
            lines += [f"## {heading}", ""]
        for note in notes:
            lines += [
                f"### {md(note.title)}",
                md(note.explanation),
                "**From the podcast:**",
                md(note.example),
                "**Translation:**",
                md(note.example_translation),
                "**Use or recognize:**",
                md(note.recommendation),
                "",
            ]
    if material.possible_asr_errors:
        lines += [
            "## Possible ASR issues",
            "",
            "Suggestions only. No correction has been applied to the transcript, quotations, pinyin source, or translation instructions.",
            "",
        ]
        for issue in material.possible_asr_errors:
            lines += [
                f"**Original:** {md(issue.original)}",
                f"**Suggested correction:** {md(issue.suggested_correction)}",
                f"**Confidence:** {issue.confidence:.0%}",
                f"**Reason:** {md(issue.reason)}",
                "",
            ]
    return "\n".join(lines).strip() + "\n"


def hanly_csv(material: StudyMaterial, settings: StudySettings) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(settings.csv_columns)
    for item in material.vocabulary:
        values = item.model_dump()
        values["tags"] = ";".join(dict.fromkeys([settings.learner_level, "podcast", *item.tags]))
        writer.writerow([values[COLUMN_ROLES[column]] for column in settings.csv_columns])
    return output.getvalue()


def reader_markdown(material: StudyMaterial, settings: StudySettings) -> str:
    paragraphs = []
    for passage in material.passages:
        reading = "\n\n".join(
            md(line.source) + ("  \n" + md(line.pinyin) if settings.target_language == "zh" else "")
            for line in passage.lines
        )
        paragraphs.append(
            reading
            + "\n\n**Translation ("
            + settings.native_language
            + "):**\n\n"
            + md(passage.translation)
        )
    return "\n\n---\n\n".join(paragraphs) + "\n"


def mosaic_csv(material: StudyMaterial) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["Chinese", "English"])
    for sentence in material.mosaic_sentences:
        writer.writerow([sentence.chinese, sentence.english])
    return output.getvalue()


def exercises_markdown(material: StudyMaterial, canonical: str) -> str:
    """Cloze practice from verified source quotes, without another model request."""
    items = [
        item
        for item in material.vocabulary
        if item.term in item.example and item.example in canonical
    ][:15]
    lines = [
        "# Practice from your subtitles",
        "",
        "Read the text, then fill each gap using the word bank.",
        "",
    ]
    if not items:
        return "# Practice from your subtitles\n\nRead the text, choose five new expressions, and write your own example for each.\n"
    lines += [
        "Word bank: "
        + "; ".join(md(item.term) for item in sorted(items, key=lambda item: item.term)),
        "",
    ]
    for index, item in enumerate(items, 1):
        lines += [
            f"{index}. {md(item.example.replace(item.term, '____'))}",
            f"   Hint: {md(item.meaning)}",
            "",
        ]
    lines += ["## Answers", ""]
    for index, item in enumerate(items, 1):
        lines += [f"{index}. **{md(item.term)}** — {md(item.example)}", ""]
    return "\n".join(lines)
