#!/usr/bin/env python3
"""
EKA Agent — Corpus Redactor
===========================
Replaces credential *values* with typed placeholders instead of dropping
chunks. Most hits live in ``.codex`` / ``.claude`` / ``.gemini`` session logs —
the richest personal data in the corpus — so deleting a whole conversation
because one line echoed a key would trade away the wrong thing.

Guarantees (asserted by tests/test_redact.py):
  1. Every credential ``scan_secrets`` finds is replaced by ``[REDACTED-<TYPE>]``.
  2. A PEM private key loses its whole body, not just the header line.
  3. Placeholders are plain ASCII with no quotes/backslashes, so a redacted
     string stays valid inside a JSON record.
  4. Record structure is preserved: every field survives, types unchanged.
  5. A record with no secrets is emitted byte-identically (the original line,
     untouched) — redaction only rewrites lines it actually changes.
  6. Re-running the pipeline's own scanner over the output reports zero. This
     final rescan uses the SAME scan_secrets that flagged the corpus (imported
     below), so "verified clean" means clean by the exact standard that found
     the hits.

Usage:
  python eka_redact.py --in corpus.jsonl --out corpus.redacted.jsonl
  python eka_redact.py --in corpus.jsonl --out out.jsonl --verify   # + rescan
"""
from __future__ import annotations

import argparse
import json
from typing import Any, List, Tuple

try:  # allow both "python scripts/eka_redact.py" and "import eka_redact"
    from eka_scan_secrets import Secret, scan_secrets, scan_strings
except ImportError:  # pragma: no cover - path shim for package-style import
    from scripts.eka_scan_secrets import Secret, scan_secrets, scan_strings


def placeholder_for(secret_type: str) -> str:
    """The typed replacement token, e.g. 'OPENAI_KEY' -> '[REDACTED-OPENAI_KEY]'.

    Intentionally ASCII-only and free of quotes/backslashes so it never breaks
    the surrounding JSON string.
    """
    return f"[REDACTED-{secret_type}]"


def redact_text(text: str) -> Tuple[str, List[Secret]]:
    """Redact every credential in ``text``. Returns (redacted_text, hits).

    Replacement is done right-to-left so earlier spans keep their offsets. For a
    PEM block the hit span covers the entire block, so the key body is removed.
    """
    hits = scan_secrets(text)
    if not hits:
        return text, []
    out = text
    for h in sorted(hits, key=lambda s: s.start, reverse=True):
        out = out[: h.start] + placeholder_for(h.type) + out[h.end :]
    return out, hits


def redact_obj(obj: Any) -> Tuple[Any, int]:
    """Recursively redact string values in a JSON-like object.

    Returns (new_obj, hit_count). Keys, non-string scalars, container shapes,
    and ordering are all preserved.
    """
    if isinstance(obj, str):
        red, hits = redact_text(obj)
        return red, len(hits)
    if isinstance(obj, dict):
        new = {}
        total = 0
        for k, v in obj.items():
            new[k], n = redact_obj(v)
            total += n
        return new, total
    if isinstance(obj, list):
        new_list = []
        total = 0
        for v in obj:
            rv, n = redact_obj(v)
            new_list.append(rv)
            total += n
        return new_list, total
    return obj, 0


def redact_record(record: dict) -> Tuple[dict, int]:
    """Redact one corpus record (a JSON object). Returns (record, hit_count)."""
    new, total = redact_obj(record)
    return new, total


def verify_structure(original: Any, redacted: Any) -> bool:
    """True iff ``redacted`` preserves ``original``'s shape and non-string data.

    String leaves are allowed to differ (that's the redaction); everything else
    — dict keys, list lengths, numbers, bools, nulls, types — must match.
    """
    if isinstance(original, str):
        return isinstance(redacted, str)
    if isinstance(original, dict):
        if not isinstance(redacted, dict) or original.keys() != redacted.keys():
            return False
        return all(verify_structure(original[k], redacted[k]) for k in original)
    if isinstance(original, list):
        if not isinstance(redacted, list) or len(original) != len(redacted):
            return False
        return all(verify_structure(a, b) for a, b in zip(original, redacted))
    # numbers / bool / None: type and value must be identical
    return type(original) is type(redacted) and original == redacted


def redact_corpus(in_path: str, out_path: str) -> dict:
    """Redact a JSONL corpus line by line.

    Clean lines are written verbatim (byte-identical); only lines containing a
    credential are parsed, redacted, and re-serialised. Returns summary stats.
    """
    lines_total = 0
    lines_redacted = 0
    secrets_removed = 0

    with open(in_path, "r", encoding="utf-8") as fin, \
            open(out_path, "w", encoding="utf-8") as fout:
        for raw in fin:
            lines_total += 1
            stripped = raw.rstrip("\n")
            if not stripped:
                fout.write(raw)
                continue
            record = json.loads(stripped)
            redacted, n = redact_record(record)
            if n == 0:
                fout.write(raw)  # byte-identical: no re-serialisation
            else:
                lines_redacted += 1
                secrets_removed += n
                # ensure_ascii=False keeps unicode personal data intact;
                # placeholders are ASCII so the line stays valid JSON.
                fout.write(json.dumps(redacted, ensure_ascii=False) + "\n")

    return {
        "lines_total": lines_total,
        "lines_redacted": lines_redacted,
        "secrets_removed": secrets_removed,
    }


def rescan_corpus(path: str) -> int:
    """Return the total number of secrets the scanner still finds in ``path``.

    The contract: this must be 0 after redaction. Uses the same scan_secrets
    that flagged the corpus in the first place.
    """
    remaining = 0
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            stripped = raw.rstrip("\n")
            if not stripped:
                continue
            remaining += len(scan_strings(json.loads(stripped)))
    return remaining


def main() -> int:
    ap = argparse.ArgumentParser(description="Redact credentials from a JSONL corpus.")
    ap.add_argument("--in", dest="in_path", required=True, help="input JSONL corpus")
    ap.add_argument("--out", dest="out_path", required=True, help="output JSONL corpus")
    ap.add_argument("--verify", action="store_true",
                    help="rescan the output and fail if any secret remains")
    args = ap.parse_args()

    stats = redact_corpus(args.in_path, args.out_path)
    print(f"  Lines: {stats['lines_total']:,}  "
          f"Redacted: {stats['lines_redacted']:,}  "
          f"Secrets removed: {stats['secrets_removed']:,}")

    if args.verify:
        remaining = rescan_corpus(args.out_path)
        print(f"  Rescan: {remaining} secret(s) remaining")
        if remaining:
            print("  FAIL — redaction did not clear the corpus.")
            return 1
        print("  OK — corpus is clean by the same scanner that flagged it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
