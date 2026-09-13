import re
import unicodedata
from difflib import SequenceMatcher

from .models import MosaicSentence

PROMOTION = re.compile(
    r"https?://|patreon|buymeacoffee|[\w.+-]+@[\w.-]+|请.{0,12}(?:订阅|点赞|关注)|欢迎.{0,8}(?:订阅|关注)|感谢.{0,10}(?:赞助|支持)|我的(?:邮箱|微信)|打赏",
    re.IGNORECASE,
)


def sentence_key(text: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFC", text)
        if not c.isspace() and not unicodedata.category(c).startswith("P")
    )


def select_candidates(sentences: list[MosaicSentence], source: str) -> list[MosaicSentence]:
    """Enforce source fidelity and remove promotions/repeated listening lines before ranking."""
    selected: list[MosaicSentence] = []
    keys: list[str] = []
    for sentence in sentences:
        if sentence.chinese not in source:
            # Defense in depth: never permit render callers to smuggle a paraphrase.
            from ..models import UserError

            raise UserError(
                "A Mosaic sentence is not present verbatim in the canonical transcript."
            )
        if PROMOTION.search(sentence.chinese):
            continue
        key = sentence_key(sentence.chinese)
        if not key or key in keys:
            continue
        duplicate = False
        for previous in keys:
            comparison = SequenceMatcher(None, previous, key, autojunk=False)
            if comparison.ratio() >= 0.94:
                differences = "".join(
                    previous[a:b] + key[c:d]
                    for op, a, b, c, d in comparison.get_opcodes()
                    if op != "equal"
                )
                if differences and set(differences) <= set("嗯啊呀呃"):
                    duplicate = True
                    break
        if not duplicate:
            selected.append(sentence)
            keys.append(key)
    return selected
