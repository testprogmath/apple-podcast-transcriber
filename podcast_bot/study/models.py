from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

Text = Annotated[str, Field(min_length=1, max_length=3000)]
Short = Annotated[str, Field(min_length=1, max_length=400)]

# The system prompt alone did not stop the model answering in the target language, so the
# requirement is repeated on every field the model must write in the learner's own language.
NATIVE = (
    " Write this in the native language named by settings.native_language in the input,"
    " NOT in the source/target language. Copying or rewording the Chinese source here is wrong."
)
Translation = Annotated[
    str,
    Field(
        min_length=1,
        max_length=3000,
        description="Natural translation of the whole block." + NATIVE,
    ),
]
Rendered = Annotated[
    str,
    Field(min_length=1, max_length=3000, description="Translation of the quoted example." + NATIVE),
]
Meaning = Annotated[
    str, Field(min_length=1, max_length=3000, description="What the item means." + NATIVE)
]
Explanation = Annotated[
    str, Field(min_length=1, max_length=3000, description="Explanation for the learner." + NATIVE)
]
ShortNative = Annotated[
    str, Field(min_length=1, max_length=400, description="Short note." + NATIVE)
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadingLine(StrictModel):
    source: Text
    pinyin: str = Field(max_length=6000)


class Passage(StrictModel):
    block_id: int
    source: Text
    lines: list[ReadingLine] = Field(min_length=1, max_length=100)
    translation: Translation


class Vocabulary(StrictModel):
    term: Short
    pinyin: str = Field(max_length=400)
    meaning: Meaning
    register_note: ShortNative
    example: Text
    example_translation: Rendered
    usage: Explanation
    tags: list[Short] = Field(max_length=8)


class Pattern(StrictModel):
    pattern: Short
    meaning: Meaning
    usage: Explanation
    example: Text
    example_translation: Rendered


class Note(StrictModel):
    title: Short
    explanation: Explanation
    example: Text
    example_translation: Rendered
    recommendation: Explanation


class ASRIssue(StrictModel):
    original: Short
    suggested_correction: Short
    confidence: float = Field(ge=0, le=1)
    reason: Text


class MosaicSentence(StrictModel):
    chinese: Text
    english: Text
    reason: Text
    source_start: float | None
    source_end: float | None


class ChunkMaterial(StrictModel):
    passages: list[Passage] = Field(min_length=1, max_length=100)
    vocabulary: list[Vocabulary] = Field(max_length=60)
    patterns: list[Pattern] = Field(max_length=15)
    pragmatics: list[Note] = Field(max_length=12)
    cultural_references: list[Note] = Field(max_length=12)
    possible_asr_errors: list[ASRIssue] = Field(max_length=30)
    mosaic_sentences: list[MosaicSentence] = Field(max_length=60)


class Selection(StrictModel):
    selected_ids: list[int] = Field(max_length=120)


class StudyMaterial(StrictModel):
    passages: list[Passage]
    vocabulary: list[Vocabulary]
    patterns: list[Pattern]
    pragmatics: list[Note]
    cultural_references: list[Note]
    possible_asr_errors: list[ASRIssue]
    mosaic_sentences: list[MosaicSentence]
    correction_policy: Literal["suggestions_only"] = "suggestions_only"


def chunk_schema(block_count: int) -> type[ChunkMaterial]:
    """Constrain structured decoding to cover every input block, including the last."""
    if not 1 <= block_count <= 100:
        raise ValueError("Study chunk must contain 1–100 blocks")
    return create_model(
        f"ChunkMaterial{block_count}Blocks",
        __base__=ChunkMaterial,
        passages=(list[Passage], Field(min_length=block_count, max_length=block_count)),
    )
