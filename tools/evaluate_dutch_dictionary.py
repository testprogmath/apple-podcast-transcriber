"""Real-source regression through the production Dutch lookup service (offline)."""

import argparse
import json
import statistics
import time
from pathlib import Path

from podcast_bot.reader.dutch import DutchDictionary
from podcast_bot.reader.languages import dutch_tokens
from podcast_bot.reader.lexical import normalized
from podcast_bot.reader.npo import NpoSubtitleNormalizer

LOW_PRIORITY = {"archaic", "obsolete", "dated", "rare", "historical", "dialectal", "nonstandard"}
EXAMPLES = [
    "zolder",
    "huis",
    "huizen",
    "kind",
    "kinderen",
    "bevrijding",
    "oorlog",
    "greep",
    "jaar",
    "mens",
    "stad",
    "steden",
    "schip",
    "schepen",
    "museum",
    "musea",
    "water",
    "meisje",
    "jongen",
    "jongens",
    "politie",
    "regering",
]


def articles(candidate):
    return {a for s in candidate["senses"] for a in s["articles"]}


def glosses(candidate, ordinary=False):
    return [
        g
        for s in candidate["senses"]
        if not ordinary or not LOW_PRIORITY.intersection(s["labels"])
        for g in s["glosses"]
    ]


def evaluate(dictionary, vtt):
    normalizer = NpoSubtitleNormalizer()
    cues = normalizer.parse(vtt.read_text(encoding="utf-8-sig"))
    _, sentences = normalizer.reconstruct(cues)
    surfaces = sorted(
        {normalized(t.text) for s in sentences for t in dutch_tokens(s.text) if t.word}
    )
    names = [
        "tokens",
        "exact_headword",
        "exact_lexical_entry",
        "resolved",
        "pos",
        "gloss",
        "ambiguous_lemma_pos",
        "noun_tokens",
        "noun_article",
        "noun_plural",
        "noun_triple",
        "noun_unambiguous_triple",
    ]
    metrics = dict.fromkeys(names, 0)
    for surface in surfaces:
        direct = dictionary.lookup(surface, form_aware=False)["candidates"]
        candidates = dictionary.lookup(surface)["candidates"]
        identities = {(c["lemma"], c["pos"]) for c in candidates}
        nouns = [c for c in candidates if c["pos"] == "noun"]
        metrics["tokens"] += 1
        metrics["exact_headword"] += bool(
            dictionary.db.execute(
                "SELECT 1 FROM entries WHERE key=? LIMIT 1", (surface,)
            ).fetchone()
        )
        metrics["exact_lexical_entry"] += bool(direct)
        metrics["resolved"] += bool(candidates)
        metrics["pos"] += any(c["pos"] for c in candidates)
        metrics["gloss"] += any(glosses(c) for c in candidates)
        metrics["ambiguous_lemma_pos"] += len(identities) > 1
        metrics["noun_tokens"] += bool(nouns)
        metrics["noun_article"] += any(articles(c) for c in nouns)
        metrics["noun_plural"] += any(c["plural"] for c in nouns)
        metrics["noun_triple"] += any(articles(c) and c["plural"] and glosses(c) for c in nouns)
        metrics["noun_unambiguous_triple"] += (
            bool(nouns)
            and len(identities) == 1
            and all(len(articles(c)) == 1 and c["plural"] and glosses(c, True) for c in nouns)
            and len({a for c in nouns for a in articles(c)}) == 1
        )
    samples = []
    for _ in range(10):
        for surface in surfaces:
            began = time.perf_counter_ns()
            dictionary.lookup(surface)
            samples.append((time.perf_counter_ns() - began) / 1000)
    return {
        "metrics": metrics,
        "warm_lookup": {
            "calls": len(samples),
            "median_us": statistics.median(samples),
            "p95_us": sorted(samples)[int(len(samples) * 0.95)],
            "max_us": max(samples),
        },
        "examples": {w: dictionary.lookup(w) for w in EXAMPLES},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--vtt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dictionary = DutchDictionary(args.artifact)
    if not dictionary.available:
        raise ValueError("Dictionary unavailable")
    try:
        result = evaluate(dictionary, args.vtt)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({k: v for k, v in result.items() if k != "examples"}))
    finally:
        dictionary.close()


if __name__ == "__main__":
    main()
