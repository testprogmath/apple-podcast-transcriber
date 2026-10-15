"""Offline research gate; stream a raw gzip and retain unchanged Dutch JSONL lines.

Not imported by Reader. Evaluation reuses an explicitly supplied research module.
"""

import argparse
import gzip
import hashlib
import importlib.util
import json
import time
from collections import Counter
from pathlib import Path


def subset(source, output):
    began = time.perf_counter()
    counts = Counter()
    digest = hashlib.sha256()
    with gzip.open(source, "rb") as stream, output.open("xb") as target:
        for line in stream:
            counts["uncompressed_bytes"] += len(line)
            record = json.loads(line)
            counts["raw_records"] += 1
            if counts["raw_records"] % 500000 == 0:
                print(json.dumps(dict(counts)), flush=True)
            if record.get("lang_code") == "nl":
                if not isinstance(record.get("word"), str):
                    raise ValueError("Dutch record has no word")
                target.write(line)
                digest.update(line)
                counts["dutch_records"] += 1
    return {
        **counts,
        "seconds": time.perf_counter() - began,
        "subset_bytes": output.stat().st_size,
        "subset_sha256": digest.hexdigest(),
        "compressed_bytes": source.stat().st_size,
        "compressed_sha256": hashlib.file_digest(source.open("rb"), "sha256").hexdigest(),
    }


def reference(path):
    spec = importlib.util.spec_from_file_location("dictionary_research", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical(value):
    """Order-insensitive diagnostic only; does not change lookup evidence."""
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        return sorted((canonical(v) for v in value), key=lambda x: json.dumps(x, sort_keys=True))
    return value


def compare(old, new):
    """Compare multisets of lexical evidence, excluding publisher-only IDs/categories."""
    fields = ("senses", "forms", "head_templates", "sounds")

    def summaries(index, unordered=False):
        identities = Counter()
        evidence = {f: {} for f in fields}
        for row in index.db.execute("SELECT record FROM entries"):
            r = json.loads(row[0])
            identity = (r["word"], r.get("pos"), r.get("etymology_number"))
            identities[identity] += 1
            for field in fields:
                value = r.get(field, [])
                if field == "senses":
                    value = [
                        {k: s[k] for k in ("glosses", "tags", "form_of", "alt_of") if k in s}
                        for s in value
                    ]
                if unordered:
                    value = canonical(value)
                evidence[field].setdefault(identity, Counter()).update(
                    [
                        hashlib.sha256(
                            json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
                        ).hexdigest()
                    ]
                )
        return identities, evidence

    a, ae = summaries(old)
    _, au = summaries(old, True)
    _, bu = summaries(new, True)
    b, be = summaries(new)
    shared = set(a) & set(b)
    only_old, only_new = sorted(set(a) - set(b), key=str), sorted(set(b) - set(a), key=str)
    changed = {f: sorted([i for i in shared if ae[f][i] != be[f][i]], key=str) for f in fields}
    return {
        "identity": "original word, POS, etymology_number; record multiplicity retained",
        "old_identities": len(a),
        "raw_identities": len(b),
        "only_old_count": len(only_old),
        "only_raw_count": len(only_new),
        "only_old_examples": only_old[:30],
        "only_raw_examples": only_new[:30],
        "changed_evidence_counts": {f: len(v) for f, v in changed.items()},
        "order_insensitive_changed_counts": {
            f: sum(au[f][i] != bu[f][i] for i in shared) for f in fields
        },
        "changed_evidence_examples": {f: v[:20] for f, v in changed.items()},
    }


def schema(path):
    fields, senses, forms = Counter(), Counter(), Counter()
    for line in path.open("rb"):
        r = json.loads(line)
        fields.update(r.keys())
        for item in r.get("senses", []):
            senses.update(item.keys())
        for item in r.get("forms", []):
            forms.update(item.keys())
    return {"record_fields": dict(fields), "sense_fields": dict(senses), "form_fields": dict(forms)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--gzip", type=Path, required=True)
    p.add_argument("--subset", type=Path, required=True)
    p.add_argument("--index", type=Path, required=True)
    p.add_argument("--old-index", type=Path, required=True)
    p.add_argument("--reference", type=Path, required=True)
    p.add_argument("--vtt", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = {"extraction": subset(args.gzip, args.subset)}
    print(json.dumps(result), flush=True)
    spike = reference(args.reference)
    result["build"] = spike.build(args.subset, args.index)
    raw, old = spike.Index(args.index), spike.Index(args.old_index)
    try:
        result["evaluation"] = spike.evaluate(raw, args.vtt)
        result["comparison"] = compare(old, raw)
        result["schema"] = schema(args.subset)
    finally:
        raw.db.close()
        old.db.close()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"metrics": result["evaluation"]["metrics"], "diff": result["comparison"]}))


if __name__ == "__main__":
    main()
