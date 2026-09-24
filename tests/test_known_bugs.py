"""Regression tests for defects found during the coverage audit (PR #1).

Each asserts the intended behavior. They were xfail(strict=True) until the bugs
were fixed; new known-but-unfixed defects should follow the same pattern using
the ``known_bug`` marker.
"""
import json

import pytest


class _FakeCompleted:
    def __init__(self, stdout):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


# ── Bug #1: eka_vector_db.stats() divides by zero on an empty training file ──
def test_stats_handles_empty_training_file(tmp_path, monkeypatch):
    import eka_vector_db as vdb

    class _Coll:
        def count(self):
            return 0

    empty = tmp_path / "training.jsonl"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setattr(vdb, "get_collection", lambda: _Coll())
    monkeypatch.setattr(vdb, "TRAINING_FILE", str(empty))
    monkeypatch.setattr(vdb, "INDEX_STATE_FILE", str(tmp_path / "missing.json"))
    vdb.stats()  # should not raise


# ── Bug #3: eka_agent_push.push_items never removes its temp payload file ──
def test_push_items_cleans_up_payload_file(tmp_path, monkeypatch):
    import eka_agent_push as push

    monkeypatch.setattr(push, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        push.subprocess, "run",
        lambda *a, **k: _FakeCompleted('{"ok": true, "inserted": 1, "duplicate": 0, "errors": 0}'),
    )
    push.push_items([{"device": "d", "content": {"x": 1}}], "dev")
    leftover = list(tmp_path.glob("push_payload_*.json"))
    assert leftover == []  # payload file should have been cleaned up


# ── Bug #5: eka_agent_pull.vps_get crashes on empty curl output ──
def test_vps_get_handles_empty_response(monkeypatch):
    import eka_agent_pull as pull

    monkeypatch.setattr(
        pull.subprocess, "run", lambda *a, **k: _FakeCompleted("")  # curl failed → empty
    )
    pull.vps_get("/pull")  # should degrade gracefully, not raise
