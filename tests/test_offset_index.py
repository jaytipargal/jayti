"""FAISS retrieval servers: byte-offset document index (build / load / read).

eka_retrieval_server_vps and scripts/eka_retrieval_server are near-identical
(only paths + OMP thread count differ), so one parameterized suite covers both.
"""
import json
from pathlib import Path

import numpy as np
import pytest

import eka_retrieval_server_vps as vps
import eka_retrieval_server as local

MODULES = [vps, local]
IDS = ["vps", "local"]


@pytest.fixture(params=MODULES, ids=IDS)
def mod(request):
    return request.param


def _write_doc_store(path, docs):
    with open(path, "wb") as f:
        for d in docs:
            f.write((json.dumps(d) + "\n").encode("utf-8"))


DOCS = [
    {"id": "d0", "document": "first doc", "metadata": {"k": "v"}, "line": 0},
    {"id": "d1", "document": "second doc — with unicode ☃", "metadata": {}, "line": 1},
    {"id": "d2", "document": "third", "metadata": {"p": "P1"}, "line": 2},
]


def test_build_offset_index_records_line_offsets(mod, tmp_path, monkeypatch):
    doc_store = tmp_path / "doc_store.jsonl"
    offsets_path = tmp_path / "doc_offsets.bin"
    _write_doc_store(doc_store, DOCS)
    monkeypatch.setattr(mod, "DOC_STORE_PATH", doc_store)
    monkeypatch.setattr(mod, "OFFSET_PATH", offsets_path)

    arr = mod.build_offset_index()

    assert len(arr) == len(DOCS)
    assert arr[0] == 0
    assert offsets_path.exists()  # persisted to disk
    # Each recorded offset must land exactly on the start of that JSON line.
    with open(doc_store, "rb") as f:
        for i, expected in enumerate(DOCS):
            f.seek(int(arr[i]))
            assert json.loads(f.readline())["id"] == expected["id"]


def test_load_offset_index_prefers_existing_file(mod, tmp_path, monkeypatch):
    offsets_path = tmp_path / "doc_offsets.bin"
    np.array([0, 10, 20], dtype=np.int64).tofile(str(offsets_path))
    monkeypatch.setattr(mod, "OFFSET_PATH", offsets_path)
    # DOC_STORE_PATH points nowhere; load must NOT rebuild since the file exists.
    monkeypatch.setattr(mod, "DOC_STORE_PATH", tmp_path / "does_not_exist.jsonl")
    arr = mod.load_offset_index()
    assert list(arr) == [0, 10, 20]


def test_load_offset_index_rebuilds_when_missing(mod, tmp_path, monkeypatch):
    doc_store = tmp_path / "doc_store.jsonl"
    _write_doc_store(doc_store, DOCS)
    monkeypatch.setattr(mod, "DOC_STORE_PATH", doc_store)
    monkeypatch.setattr(mod, "OFFSET_PATH", tmp_path / "missing_offsets.bin")
    arr = mod.load_offset_index()
    assert len(arr) == len(DOCS)


def test_get_doc_by_index_roundtrip(mod, tmp_path, monkeypatch):
    doc_store = tmp_path / "doc_store.jsonl"
    offsets_path = tmp_path / "doc_offsets.bin"
    _write_doc_store(doc_store, DOCS)
    monkeypatch.setattr(mod, "DOC_STORE_PATH", doc_store)
    monkeypatch.setattr(mod, "OFFSET_PATH", offsets_path)
    arr = mod.build_offset_index()

    handle = open(doc_store, "rb")
    try:
        monkeypatch.setattr(mod, "doc_offsets", arr)
        monkeypatch.setattr(mod, "doc_file_handle", handle)
        assert mod.get_doc_by_index(1)["id"] == "d1"
        assert "unicode" in mod.get_doc_by_index(1)["document"]
        # Reads must be independent of order (seek-based, not sequential).
        assert mod.get_doc_by_index(0)["id"] == "d0"
        assert mod.get_doc_by_index(2)["id"] == "d2"
    finally:
        handle.close()
