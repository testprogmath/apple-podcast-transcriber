"""Small language boundary shared by document tokenization and API capabilities."""

import re

from .tokens import Token

LETTER = r"[^\W\d_](?:[\u0300-\u036f])*"
DUTCH_WORD = re.compile(rf"(?:{LETTER})+(?:['’ʼ-](?:{LETTER})+)*", re.UNICODE)
CAPABILITIES = {
    "zh": {
        "lexical_inspector": "chinese",
        "speech_language": "zh-CN",
        "pinyin": True,
        "hanly": True,
        "mosaic": True,
        "internal_save": False,
    },
    "nl": {
        "lexical_inspector": "contextual",
        "local_dictionary": "dutch",
        "speech_language": "nl-NL",
        "pinyin": False,
        "hanly": False,
        "mosaic": False,
        "internal_save": True,
    },
}


def capabilities(language):
    return CAPABILITIES.get(
        language,
        {
            "lexical_inspector": None,
            "speech_language": "",
            "pinyin": False,
            "hanly": False,
            "mosaic": False,
            "internal_save": False,
        },
    ).copy()


def dutch_tokens(text):
    tokens, cursor = [], 0
    for match in DUTCH_WORD.finditer(text):
        if match.start() > cursor:
            tokens.append(Token(text[cursor : match.start()], False, cursor, match.start()))
        tokens.append(Token(match[0], True, match.start(), match.end()))
        cursor = match.end()
    if cursor < len(text):
        tokens.append(Token(text[cursor:], False, cursor, len(text)))
    return tokens
