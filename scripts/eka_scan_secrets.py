#!/usr/bin/env python3
"""
EKA Agent — Secret Scanner
==========================
Single source of truth for "what is a credential" across the redaction
pipeline. The redactor (``eka_redact.py``) and the corpus verifier both import
``scan_secrets`` from here, so redaction and the final rescan-expect-zero check
use the *same* detector by construction — the contract can't drift.

Detects, as typed hits with exact character spans:
  - ANTHROPIC_KEY    sk-ant-...            (checked before OPENAI so it wins)
  - OPENAI_KEY       sk-... / sk-proj-...  (excluding the sk-ant- prefix)
  - GOOGLE_API_KEY   AIza...
  - AWS_ACCESS_KEY   AKIA... / ASIA...
  - PRIVATE_KEY      a full PEM block, -----BEGIN...PRIVATE KEY----- through
                     -----END...PRIVATE KEY-----  (the WHOLE block, so the key
                     body is removed on redaction — not just the header line)

Design notes
------------
* A PEM private key is detected by its block, and the reported span covers the
  entire block including the key material. Reporting only the ``BEGIN`` line
  would leave the key body in the corpus and make a rescan report the record
  clean — strictly worse than not redacting.
* Matches that fall *inside* a detected PEM block are suppressed, so a base64
  line of key material is never double-reported.

To swap in a different detector, replace this module's ``scan_secrets`` with an
implementation that returns the same ``Secret`` list; nothing else changes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Pattern, Tuple


@dataclass(frozen=True)
class Secret:
    """One detected credential.

    ``value`` is the exact matched substring; ``start``/``end`` are its span in
    the scanned text such that ``text[start:end] == value``.
    """

    type: str
    value: str
    start: int
    end: int


# ── Patterns ─────────────────────────────────────────────────────────────
# A PEM private-key block, matched whole (DOTALL, non-greedy). "[A-Z0-9 ]*"
# covers RSA / EC / OPENSSH / DSA / PGP and plain "PRIVATE KEY".
_PEM_RE: Pattern = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)

# Order matters: Anthropic before OpenAI (both start with "sk-").
_TOKEN_PATTERNS: List[Tuple[str, Pattern]] = [
    ("ANTHROPIC_KEY", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OPENAI_KEY", re.compile(r"sk-(?!ant-)(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("GOOGLE_API_KEY", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("AWS_ACCESS_KEY", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
]


def scan_secrets(text: str) -> List[Secret]:
    """Return all credential hits in ``text``, sorted by start offset.

    Non-overlapping: token matches inside a PEM block are suppressed so key
    material is reported once, as part of the block.
    """
    if not text:
        return []

    hits: List[Secret] = []

    # PEM blocks first — they define exclusion zones for the token patterns.
    pem_spans: List[Tuple[int, int]] = []
    for m in _PEM_RE.finditer(text):
        pem_spans.append((m.start(), m.end()))
        hits.append(Secret("PRIVATE_KEY", m.group(), m.start(), m.end()))

    def _inside_pem(pos: int) -> bool:
        return any(s <= pos < e for s, e in pem_spans)

    for sec_type, pattern in _TOKEN_PATTERNS:
        for m in pattern.finditer(text):
            if _inside_pem(m.start()):
                continue
            hits.append(Secret(sec_type, m.group(), m.start(), m.end()))

    hits.sort(key=lambda h: (h.start, h.end))
    return hits


def has_secret(text: str) -> bool:
    """True if ``text`` contains at least one credential."""
    return bool(scan_secrets(text))


def scan_strings(obj) -> List[Secret]:
    """Recursively scan every string value in a JSON-like object.

    Spans are per-string (not global to the record); useful for counting and
    for asserting a redacted record contains zero secrets.
    """
    found: List[Secret] = []
    if isinstance(obj, str):
        found.extend(scan_secrets(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            found.extend(scan_strings(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            found.extend(scan_strings(v))
    return found


if __name__ == "__main__":
    import sys

    data = sys.stdin.read()
    for s in scan_secrets(data):
        print(f"{s.type}\t{s.start}:{s.end}\t{s.value[:24]}...")
