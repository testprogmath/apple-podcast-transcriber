"""Replaceable local word segmentation; never calls an LLM or remote service."""

from ..models import UserError


def segment_mandarin(text: str) -> str:
    import jieba

    result = " ".join(word for word in jieba.cut(text, cut_all=False) if word.strip())
    if not result or "".join(result.split()) != "".join(text.split()):
        raise UserError("Mandarin Mosaic segmentation failed to preserve the sentence.")
    return result
