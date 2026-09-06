"""The redactor contract: redact -> verify structure -> rescan -> expect zero.

The rescan uses the SAME scan_secrets the redactor uses, so a passing suite
means the corpus is clean by the exact standard that flags credentials in the
first place. Sample keys below are pattern-valid but fabricated.
"""
import json

import pytest

import eka_scan_secrets as scanner
import eka_redact as redact

# ── Fabricated, pattern-valid sample credentials ────────────────────────
OPENAI = "sk-" + "A" * 48
ANTHROPIC = "sk-ant-api03-" + "B" * 40
GOOGLE = "AIza" + "C" * 35
AWS = "AKIAIOSFODNN7EXAMPLE"  # AWS's own documented example shape
PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEpAIBAAKCAQEA" + "D" * 40 + "\n"
    + "E" * 60 + "\n"
    "-----END RSA PRIVATE KEY-----"
)
KEY_BODY_MARKER = "MIIEpAIBAAKCAQEA"  # a slice of the PEM body


# ── Scanner: typed detection ────────────────────────────────────────────
@pytest.mark.parametrize("text,expected_type", [
    (f"my key is {OPENAI} ok", "OPENAI_KEY"),
    (f"ANTHROPIC_API_KEY={ANTHROPIC}", "ANTHROPIC_KEY"),
    (f"gemini {GOOGLE}", "GOOGLE_API_KEY"),
    (f"aws {AWS}", "AWS_ACCESS_KEY"),
    (PEM, "PRIVATE_KEY"),
])
def test_scanner_detects_each_type(text, expected_type):
    hits = scanner.scan_secrets(text)
    assert len(hits) == 1
    assert hits[0].type == expected_type
    assert text[hits[0].start:hits[0].end] == hits[0].value


def test_scanner_does_not_flag_placeholders():
    # .env.example-style placeholders contain no real key material.
    text = "OPENAI_API_KEY=your-openai-key-here\nGOOGLE_API_KEY=AIza-placeholder"
    assert scanner.scan_secrets(text) == []


def test_anthropic_not_misread_as_openai():
    hits = scanner.scan_secrets(ANTHROPIC)
    assert [h.type for h in hits] == ["ANTHROPIC_KEY"]


# ── Redaction: typed placeholders ───────────────────────────────────────
def test_redact_replaces_value_with_typed_placeholder():
    red, hits = redact.redact_text(f"before {OPENAI} after")
    assert red == "before [REDACTED-OPENAI_KEY] after"
    assert OPENAI not in red
    assert len(hits) == 1


def test_placeholder_is_ascii_and_json_safe():
    ph = redact.placeholder_for("OPENAI_KEY")
    assert ph.isascii()
    assert '"' not in ph and "\\" not in ph
    # A record whose value is fully redacted still round-trips as valid JSON.
    rec = {"text": redact.redact_text(f"key {OPENAI}")[0]}
    assert json.loads(json.dumps(rec, ensure_ascii=False)) == rec


# ── PEM: whole body removed, not just the header ────────────────────────
def test_pem_body_is_removed_not_just_header():
    red, hits = redact.redact_text(f"log:\n{PEM}\nend")
    assert hits[0].type == "PRIVATE_KEY"
    assert "[REDACTED-PRIVATE_KEY]" in red
    assert "BEGIN RSA PRIVATE KEY" not in red
    assert "END RSA PRIVATE KEY" not in red
    assert KEY_BODY_MARKER not in red          # key material gone
    assert "D" * 40 not in red
    # And the redacted text is clean by the scanner.
    assert scanner.scan_secrets(red) == []


# ── Structure preservation ──────────────────────────────────────────────
def test_structure_preserved_across_nested_record():
    rec = {
        "id": "chunk_1",
        "line": 42,
        "flagged": True,
        "score": 0.5,
        "empty": None,
        "content": f"assistant echoed {ANTHROPIC} into the log",
        "metadata": {"device": "s24", "keys": [f"see {GOOGLE}", "clean"]},
    }
    red, n = redact.redact_record(rec)
    assert n == 2  # anthropic + google
    assert redact.verify_structure(rec, red)
    # Every field still present, non-string values untouched.
    assert red["id"] == "chunk_1" and red["line"] == 42
    assert red["flagged"] is True and red["score"] == 0.5 and red["empty"] is None
    assert "[REDACTED-ANTHROPIC_KEY]" in red["content"]
    assert "[REDACTED-GOOGLE_API_KEY]" in red["metadata"]["keys"][0]
    assert red["metadata"]["keys"][1] == "clean"


def test_clean_record_is_unchanged():
    rec = {"id": "c", "content": "nothing secret here", "n": 3}
    red, n = redact.redact_record(rec)
    assert n == 0
    assert red == rec


# ── Idempotency ─────────────────────────────────────────────────────────
def test_redaction_is_idempotent():
    once, _ = redact.redact_text(f"a {OPENAI} b {GOOGLE} c")
    twice, hits = redact.redact_text(once)
    assert twice == once
    assert hits == []


# ── The full contract, end to end, over a JSONL corpus ──────────────────
def _corpus_records():
    return [
        {"id": "0", "content": f"user pasted {OPENAI} into codex", "line": 0},
        {"id": "1", "content": f"ANTHROPIC_API_KEY={ANTHROPIC}", "line": 1},
        {"id": "2", "content": f"cloudbuild has {GOOGLE}", "line": 2},
        {"id": "3", "content": f"aws example {AWS} (a false positive)", "line": 3},
        {"id": "4", "content": f"config:\n{PEM}\n", "line": 4},
        {"id": "5", "content": "an ordinary WhatsApp message, nothing to redact", "line": 5},
        {"id": "6", "content": "OPENAI_API_KEY=your-key-here", "line": 6},  # placeholder
        {"id": "7", "meta": {"nested": [f"log {ANTHROPIC}", "ok"]}, "line": 7},
    ]


def test_full_contract_redact_verify_rescan_zero(tmp_path):
    src = tmp_path / "corpus.jsonl"
    out = tmp_path / "corpus.redacted.jsonl"
    records = _corpus_records()
    src.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )

    stats = redact.redact_corpus(str(src), str(out))
    assert stats["lines_total"] == len(records)

    in_lines = src.read_text(encoding="utf-8").splitlines(keepends=True)
    out_lines = out.read_text(encoding="utf-8").splitlines(keepends=True)
    assert len(in_lines) == len(out_lines)

    for original_raw, redacted_raw, rec in zip(in_lines, out_lines, records):
        redacted_rec = json.loads(redacted_raw)
        # Structure survives for every record.
        assert redact.verify_structure(rec, redacted_rec)
        # Clean records (ids 5 and 6) are emitted byte-identically.
        if not scanner.scan_strings(rec):
            assert redacted_raw == original_raw

    # THE contract: the same scanner reports zero over the redacted corpus.
    assert redact.rescan_corpus(str(out)) == 0


def test_clean_lines_reported_and_left_alone(tmp_path):
    src = tmp_path / "c.jsonl"
    out = tmp_path / "c.out.jsonl"
    src.write_text(
        json.dumps({"id": "x", "content": "totally clean"}) + "\n", encoding="utf-8"
    )
    stats = redact.redact_corpus(str(src), str(out))
    assert stats["lines_redacted"] == 0 and stats["secrets_removed"] == 0
    assert out.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
