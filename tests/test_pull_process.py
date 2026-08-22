"""eka_agent_pull.process_items — the core ingest→training-chunk transform.

Covers dedup, category/priority mapping, per-data_type prompt derivation, and
the title fallback chain. STATE_DIR / TRAINING_FILE / ROLLBACK_DIR are redirected
into tmp_path so the transform's file writes are isolated.
"""
import json

import pytest

import eka_agent_pull as pull


@pytest.fixture
def paths(tmp_path, monkeypatch):
    training = tmp_path / "training.jsonl"
    rollback = tmp_path / "rollback"
    monkeypatch.setattr(pull, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(pull, "TRAINING_FILE", str(training))
    monkeypatch.setattr(pull, "ROLLBACK_DIR", str(rollback))
    return {"training": training, "rollback": rollback, "state": tmp_path}


def _item(**overrides):
    base = {
        "device": "windows_pc_abcom",
        "source": "filesystem",
        "data_type": "file_change",
        "content": {"filename": "report.pdf"},
        "device_time": "2026-08-22T10:00:00Z",
    }
    base.update(overrides)
    return base


def _written_chunks(training_file):
    return [json.loads(l) for l in training_file.read_text(encoding="utf-8").splitlines() if l]


def test_empty_items_returns_zeros(paths):
    stats = pull.process_items([])
    assert stats == {"chunks_created": 0, "duplicates": 0, "p0_found": 0, "p1_found": 0}


def test_single_item_creates_one_chunk(paths):
    stats = pull.process_items([_item()])
    assert stats["chunks_created"] == 1
    chunks = _written_chunks(paths["training"])
    assert len(chunks) == 1


def test_category_and_priority_mapping(paths):
    stats = pull.process_items([_item(data_type="whatsapp_chat", content={"message": "hi"})])
    chunk = _written_chunks(paths["training"])[0]
    assert chunk["category"] == "whatsapp_chat"
    assert chunk["metadata"]["priority"] == "P1"
    assert stats["p1_found"] == 1


def test_unknown_data_type_falls_back_to_defaults(paths):
    pull.process_items([_item(data_type="totally_unknown", content={"x": 1})])
    chunk = _written_chunks(paths["training"])[0]
    assert chunk["category"] == "extracted_text"  # CATEGORY_MAP default
    assert chunk["metadata"]["priority"] == "P2"   # PRIORITY_MAP default


@pytest.mark.parametrize("data_type,expected", [
    ("whatsapp_chat", "Show WhatsApp messages from"),
    ("call_recordings", "Show call recordings and logs from"),
    ("browser_data", "Show browser history from"),
    ("file_change", "What new files were found on"),
])
def test_input_prompt_per_data_type(paths, data_type, expected):
    pull.process_items([_item(data_type=data_type, content={"x": 1})])
    chunk = _written_chunks(paths["training"])[0]
    assert chunk["input"].startswith(expected)


def test_title_prefers_filename_then_title_then_message(paths):
    pull.process_items([
        _item(content={"filename": "a.pdf"}),
        _item(content={"title": "My Title"}),
        _item(content={"message": "hello world"}),
        _item(content={}),  # nothing → "<data_type> from <device>"
    ])
    titles = [c["title"] for c in _written_chunks(paths["training"])]
    assert titles[0] == "a.pdf"
    assert titles[1] == "My Title"
    assert titles[2] == "hello world"
    assert titles[3] == "file_change from windows_pc_abcom"


def test_dedup_against_existing_hash_cache(paths):
    # Seed the hash cache so a matching content_hash is treated as a duplicate.
    (paths["state"] / "chunk_hashes.txt").write_text("knownhash\n", encoding="utf-8")
    stats = pull.process_items([_item(content_hash="knownhash")])
    assert stats["duplicates"] == 1
    assert stats["chunks_created"] == 0


def test_dedup_within_same_batch(paths):
    # Two items with identical computed content produce one chunk, one duplicate.
    same = {"filename": "dup.pdf"}
    stats = pull.process_items([_item(content=same), _item(content=same)])
    assert stats["chunks_created"] == 1
    assert stats["duplicates"] == 1


def test_rollback_batch_written(paths):
    pull.process_items([_item()])
    rollback_files = list(paths["rollback"].glob("*_batch.jsonl"))
    assert len(rollback_files) == 1
