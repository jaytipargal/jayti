"""eka_agent_push: per-device sync-timestamp helpers + push_items empty guard."""
import eka_agent_push as push


def test_get_last_sync_defaults_to_epoch(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "STATE_DIR", str(tmp_path))
    assert push.get_last_sync("samsung_s24_ultra") == "1970-01-01T00:00:00Z"


def test_save_then_get_last_sync_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "STATE_DIR", str(tmp_path))
    push.save_last_sync("windows_pc_abcom", "2026-08-22T01:00:00Z")
    assert push.get_last_sync("windows_pc_abcom") == "2026-08-22T01:00:00Z"


def test_sync_is_per_device(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "STATE_DIR", str(tmp_path))
    push.save_last_sync("dev_a", "2026-01-01T00:00:00Z")
    assert push.get_last_sync("dev_b") == "1970-01-01T00:00:00Z"  # unaffected


def test_push_items_empty_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "STATE_DIR", str(tmp_path))
    result = push.push_items([], "dev")
    assert result == {"inserted": 0, "duplicates": 0, "errors": 0}
    # No payload file should be created for an empty push.
    assert list(tmp_path.iterdir()) == []
