from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Text = Annotated[str, Field(min_length=1, max_length=3000)]
Short = Annotated[str, Field(min_length=1, max_length=400)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadingLine(StrictModel):
    source: Text
    pinyin: str = Field(max_length=6000)


class Passage(StrictModel):
    block_id: int
    source: Text
    lines: list[ReadingLine] = Field(min_length=1, max_length=100)
    translation: Text


class Vocabulary(StrictModel):
    term: Short
    pinyin: str = Field(max_length=400)
    meaning: Text
    register_note: Short
    example: Text
    example_translation: Text
    usage: Text
    tags: list[Short] = Field(max_length=8)


class Pattern(StrictModel):
    pattern: Short
    meaning: Text
    usage: Text
    example: Text
    example_translation: Text


class Note(StrictModel):
    title: Short
    explanation: Text
    example: Text
    example_translation: Text
    recommendation: Text


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
