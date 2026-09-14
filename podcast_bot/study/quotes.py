"""Source-owned quotations: the model selects IDs and never writes citation text."""

import re
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, create_model

from .chunking import split_blocks
from .lexical import LexicalChunk
from .models import ChunkMaterial, StrictModel

QUOTE_FIELDS = {
    "vocabulary": ("example", "example_id"),
    "patterns": ("example", "example_id"),
    "pragmatics": ("example", "example_id"),
    "cultural_references": ("example", "example_id"),
    "possible_asr_errors": ("original", "original_id"),
    "mosaic_sentences": ("chinese", "sentence_id"),
}

QUOTE_PROMPT = """
Citation selection protocol (the response schema is authoritative):
quote_segments is an indexed catalogue sliced directly from the source blocks. For every
example choose example_id; for Mosaic choose sentence_id; for an ASR issue choose original_id.
Use only a supplied ID. The application inserts its exact text. Never output your own example,
original or chinese field. Translate/explain the WHOLE selected segment, without paraphrasing
its source or translating a different example. Choose the segment containing the relevant term
or construction. If no segment supports an item, omit that item rather than inventing evidence.
Mosaic selections must be complete sentences/utterances; omit fragments. ASR corrections remain
suggestions, never edits to the source. Reading passages still cover every complete input block.
"""


def catalogue(blocks: list[dict]) -> list[dict]:
    result = []
    for block in blocks:
        # Split on sentence endings, retaining punctuation and exact internal whitespace.
        for match in re.finditer(
            r'.+?(?:[。！？!?；;\n]+[”’」』»"\']*|[.](?=\s|$)|$)', block["text"], re.S
        ):
            for part in split_blocks(match.group(), limit=400) if match.group().strip() else []:
                text = part.text.strip()
                if text:
                    result.append({"id": len(result), "block_id": block["id"], "text": text})
    if not result:
        raise ValueError("No source quotations available")
    return result


class SourceQuotes:
    def __init__(self, schema: type[BaseModel], payload: dict):
        self.original_schema = schema
        self.segments = catalogue(payload["blocks"])
        self.by_id = {s["id"]: s["text"] for s in self.segments}
        selector = Literal[tuple(self.by_id)]
        overrides = {}
        for name, (source_field, id_field) in QUOTE_FIELDS.items():
            if name not in schema.model_fields:
                continue
            field = schema.model_fields[name]
            item = field.annotation.__args__[0]
            fields = {
                key: (value.annotation, deepcopy(value))
                for key, value in item.model_fields.items()
                if key != source_field
            }
            fields[id_field] = (selector, ...)
            selected = create_model(f"Selected{item.__name__}", __base__=StrictModel, **fields)
            overrides[name] = (list[selected], deepcopy(field))
        self.schema = create_model(f"SourceQuoted{schema.__name__}", __base__=schema, **overrides)
        self.payload = {**payload, "quote_segments": self.segments}

    def resolve(self, response: BaseModel) -> BaseModel:
        # Revalidate references even if a test/custom client bypassed structured decoding.
        data = self.schema.model_validate(response.model_dump()).model_dump()
        for name, (source_field, id_field) in QUOTE_FIELDS.items():
            for item in data.get(name, []):
                item[source_field] = self.by_id[item.pop(id_field)]
        return self.original_schema.model_validate(data)


def supports_quotes(schema: type[BaseModel]) -> bool:
    return issubclass(schema, (ChunkMaterial, LexicalChunk))
