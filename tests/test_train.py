"""eka_train pure/file-I/O helpers: format_for_training, load_training_data, rollback."""
import json

import pytest

import eka_train


# ── format_for_training (pure) ──────────────────────────────────────────
def test_format_maps_input_to_instruction():
    out = eka_train.format_for_training([
        {"input": "Q?", "output": "A", "category": "c", "title": "t"}
    ])
    assert out == [{"instruction": "Q?", "output": "A", "category": "c", "title": "t"}]


def test_format_pretty_prints_json_output():
    out = eka_train.format_for_training([{"input": "Q", "output": json.dumps({"a": 1})}])
    assert '"a": 1' in out[0]["output"]
    assert "\n" in out[0]["output"]  # indent=2 produces newlines


def test_format_leaves_non_json_output_untouched():
    out = eka_train.format_for_training([{"input": "Q", "output": "plain text"}])
    assert out[0]["output"] == "plain text"


def test_format_supplies_defaults_for_missing_fields():
    out = eka_train.format_for_training([{}])
    assert out[0] == {"instruction": "", "output": "", "category": "", "title": ""}


def test_format_empty_list():
    assert eka_train.format_for_training([]) == []


# ── load_training_data (file I/O via monkeypatched constants) ────────────
def test_load_training_data_missing_date_batch_returns_empty(tmp_path, monkeypatch):
    # date_str mode: a missing rollback batch file returns [] (guarded branch).
    monkeypatch.setattr(eka_train, "ROLLBACK_DIR", str(tmp_path))
    assert eka_train.load_training_data(date_str="2099-01-01", batch_size=5) == []


def test_load_training_data_reads_date_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(eka_train, "ROLLBACK_DIR", str(tmp_path))
    batch = tmp_path / "2026-08-22_batch.jsonl"
    batch.write_text(
        json.dumps({"input": "q", "output": "o"}) + "\n", encoding="utf-8"
    )
    loaded = eka_train.load_training_data(date_str="2026-08-22", batch_size=5)
    assert loaded == [{"input": "q", "output": "o"}]


def test_load_training_data_skips_first_10_and_malformed_lines(tmp_path, monkeypatch):
    f = tmp_path / "chunks.jsonl"
    # First 10 lines are "instruction" chunks the loader deliberately skips.
    skipped = [json.dumps({"instruction": i}) for i in range(10)]
    # Real data begins at index 10; interleave one malformed line among them.
    tail = [
        json.dumps({"input": "q10", "output": "o10"}),
        "{bad json",
        json.dumps({"input": "q12", "output": "o12"}),
        json.dumps({"input": "q13", "output": "o13"}),
    ]
    f.write_text("\n".join(skipped + tail) + "\n", encoding="utf-8")
    monkeypatch.setattr(eka_train, "TRAINING_FILE", str(f))
    loaded = eka_train.load_training_data(date_str=None, batch_size=100)
    inputs = [r.get("input") for r in loaded]
    assert inputs == ["q10", "q12", "q13"]  # first-10 + malformed both dropped


# ── rollback (file I/O) ─────────────────────────────────────────────────
def test_rollback_no_log_is_noop(tmp_path, monkeypatch):
    # No training_log.jsonl at all → early return, nothing written.
    monkeypatch.setattr(eka_train, "STATE_DIR", str(tmp_path))
    eka_train.rollback("2026-08-22")
    assert not (tmp_path / "current_adapter.txt").exists()


def test_rollback_unknown_date_does_not_write_current(tmp_path, monkeypatch):
    log = tmp_path / "training_log.jsonl"
    log.write_text(
        json.dumps({"date": "2026-08-20", "adapter_path": str(tmp_path / "a")}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(eka_train, "STATE_DIR", str(tmp_path))
    # Requested date isn't in the log → no current_adapter.txt.
    eka_train.rollback("1999-01-01")
    assert not (tmp_path / "current_adapter.txt").exists()


def test_rollback_writes_current_when_adapter_exists(tmp_path, monkeypatch):
    adapter = tmp_path / "adapter_2026-08-20"
    adapter.mkdir()
    log = tmp_path / "training_log.jsonl"
    log.write_text(
        json.dumps({"date": "2026-08-20", "adapter_path": str(adapter)}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(eka_train, "STATE_DIR", str(tmp_path))
    eka_train.rollback("2026-08-20")
    current = tmp_path / "current_adapter.txt"
    assert current.exists()
    assert current.read_text() == str(adapter)
