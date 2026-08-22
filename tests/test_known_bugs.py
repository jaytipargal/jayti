"""Tests that document real defects found during the coverage audit.

Each asserts the *intended* behavior and is marked xfail(strict=True): it fails
today (proving the bug) and will flip to a passing test the moment the bug is
fixed — at which point the xfail marker should be removed. See the PR body for
the catalogue of findings.
"""
import json

import pytest


class _FakeCompleted:
    def __init__(self, stdout):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


# ── Bug #1: eka_vector_db.stats() divides by zero on an empty training file ──
@pytest.mark.known_bug
@pytest.mark.xfail(raises=ZeroDivisionError, strict=True,
                   reason="stats() computes count/total_lines with no guard for total_lines==0")
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
@pytest.mark.known_bug
@pytest.mark.xfail(strict=True,
                   reason="os.remove(payload_file) is unreachable (placed after return)")
def test_push_items_cleans_up_payload_file(tmp_path, monkeypatch):
    import eka_agent_push as push

    monkeypatch.setattr(push, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        push.subprocess, "run",
        lambda *a, **k: _FakeCompleted('{"inserted": 1, "duplicates": 0, "errors": 0}'),
    )
    push.push_items([{"device": "d", "content": {"x": 1}}], "dev")
    leftover = list(tmp_path.glob("push_payload_*.json"))
    assert leftover == []  # payload file should have been cleaned up


# ── Bug #5: eka_agent_pull.vps_get crashes on empty curl output ──
@pytest.mark.known_bug
@pytest.mark.xfail(raises=json.JSONDecodeError, strict=True,
                   reason="vps_get/vps_post call json.loads on unchecked curl stdout")
def test_vps_get_handles_empty_response(monkeypatch):
    import eka_agent_pull as pull

    monkeypatch.setattr(
        pull.subprocess, "run", lambda *a, **k: _FakeCompleted("")  # curl failed → empty
    )
    pull.vps_get("/pull")  # should degrade gracefully, not raise
