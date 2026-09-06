"""eka_vector_db pure helpers: chunk_to_document, chunk_to_metadata, state I/O."""
import json

import eka_vector_db


# ── chunk_to_document ───────────────────────────────────────────────────
def test_document_contains_all_sections(sample_chunk):
    doc = eka_vector_db.chunk_to_document(sample_chunk)
    assert "Title: report.pdf" in doc
    assert "Category: documents" in doc
    assert "Input:" in doc
    assert "Output:" in doc


def test_document_pretty_prints_json_output():
    chunk = {"output": json.dumps({"b": 2, "a": 1})}
    doc = eka_vector_db.chunk_to_document(chunk)
    # Valid JSON output is re-serialised with indentation.
    assert '"a": 1' in doc
    assert '"b": 2' in doc


def test_document_falls_back_on_malformed_json_output():
    chunk = {"output": "not-json{{"}
    doc = eka_vector_db.chunk_to_document(chunk)
    assert "not-json{{" in doc  # kept as raw text, no crash


def test_document_handles_non_string_output():
    chunk = {"output": {"already": "dict"}}
    doc = eka_vector_db.chunk_to_document(chunk)
    assert "already" in doc


def test_document_truncates_long_input():
    chunk = {"input": "x" * 5000}
    doc = eka_vector_db.chunk_to_document(chunk)
    # Input field capped at 500 chars.
    assert "x" * 500 in doc
    assert "x" * 501 not in doc


def test_document_handles_missing_keys():
    doc = eka_vector_db.chunk_to_document({})
    assert "Title: " in doc  # empty defaults, no KeyError


# ── chunk_to_metadata ───────────────────────────────────────────────────
def test_metadata_all_values_are_strings(sample_chunk):
    meta = eka_vector_db.chunk_to_metadata(sample_chunk)
    assert all(isinstance(v, str) for v in meta.values())
    assert meta["chunk_id"] == sample_chunk["id"]
    assert meta["source_device"] == "windows_pc_abcom"


def test_metadata_parses_stringified_nested_metadata():
    chunk = {"metadata": json.dumps({"priority": "P1", "source_device": "s24"})}
    meta = eka_vector_db.chunk_to_metadata(chunk)
    assert meta["priority"] == "P1"
    assert meta["source_device"] == "s24"


def test_metadata_tolerates_malformed_nested_metadata():
    chunk = {"metadata": "not-json{{"}
    meta = eka_vector_db.chunk_to_metadata(chunk)
    assert meta["priority"] == ""  # falls back to empty dict


def test_metadata_truncates_title():
    meta = eka_vector_db.chunk_to_metadata({"title": "t" * 500})
    assert len(meta["title"]) == 200


def test_metadata_handles_empty_chunk():
    meta = eka_vector_db.chunk_to_metadata({})
    assert meta["chunk_id"] == ""
    assert meta["title"] == ""


# ── get_state / save_state (file I/O via monkeypatched constants) ────────
def test_get_state_default_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(eka_vector_db, "INDEX_STATE_FILE", str(tmp_path / "nope.json"))
    assert eka_vector_db.get_state() == {"last_indexed_line": 0, "total_indexed": 0}


def test_save_then_get_state_roundtrip(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setattr(eka_vector_db, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(eka_vector_db, "INDEX_STATE_FILE", str(state_dir / "s.json"))
    eka_vector_db.save_state({"last_indexed_line": 42, "total_indexed": 42})
    assert eka_vector_db.get_state()["last_indexed_line"] == 42
