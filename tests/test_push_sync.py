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


# ── hub auth contract ───────────────────────────────────────────────────
# The hub authenticates the PAIR (X-Device-Id selects the api_keys row,
# X-Api-Key is verified against its argon2 hash) and rejects anything else
# with 401, so a push that sends only one of them silently loses every item.
def test_push_without_device_key_does_not_call_curl(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(push, "DEVICE_KEY", "")
    called = []
    monkeypatch.setattr(push.subprocess, "run", lambda *a, **k: called.append(a))
    result = push.push_items([{"data_type": "x", "content": {}}], "dev")
    assert called == []
    assert result["errors"] == 1


def test_push_sends_device_id_and_key_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(push, "DEVICE_KEY", "jt_secret")
    seen = {}

    class _Done:
        stdout = '{"inserted": 1, "duplicates": 0, "errors": 0}'
        stderr = ""
        returncode = 0

    def fake_run(cmd, *a, **k):
        seen["cmd"] = cmd
        return _Done()

    monkeypatch.setattr(push.subprocess, "run", fake_run)
    push.push_items([{"data_type": "x", "content": {}}], "windows_pc_abcom")
    cmd = seen["cmd"]
    assert "X-Device-Id: windows_pc_abcom" in cmd
    assert "X-Api-Key: jt_secret" in cmd
    assert not any(h.startswith("X-API-Key:") for h in cmd)  # the old, rejected header


def test_push_payload_names_the_device(tmp_path, monkeypatch):
    import json as _json
    monkeypatch.setattr(push, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(push, "DEVICE_KEY", "jt_secret")
    captured = {}

    class _Done:
        stdout = '{"inserted": 1}'
        stderr = ""
        returncode = 0

    def fake_run(cmd, *a, **k):
        payload_file = [c for c in cmd if isinstance(c, str) and c.startswith("@")][0][1:]
        captured["payload"] = _json.loads(open(payload_file, encoding="utf-8").read())
        return _Done()

    monkeypatch.setattr(push.subprocess, "run", fake_run)
    push.push_items([{"data_type": "x", "content": {}}], "asus_vivobook")
    assert captured["payload"]["device"] == "asus_vivobook"


def test_device_env_file_is_read(tmp_path, monkeypatch):
    env = tmp_path / "device.env"
    env.write_text("# comment\nEKA_DEVICE_KEY=jt_from_file\nEKA_VPS_URL=https://example.test/\n",
                   encoding="utf-8")
    monkeypatch.setenv("EKA_DEVICE_ENV", str(env))
    monkeypatch.delenv("EKA_DEVICE_KEY", raising=False)
    monkeypatch.setattr(push, "DEVICE_ENV_CANDIDATES", [str(env)])
    values = push._load_device_env()
    assert values["EKA_DEVICE_KEY"] == "jt_from_file"
    assert values["EKA_VPS_URL"] == "https://example.test/"
