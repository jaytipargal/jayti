import json
from pathlib import Path
import sys

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox" / "jtagent"
sys.path.insert(0, str(SANDBOX))

from pack_devices import DEVICES, pack_devices, seed_chunks, write_jsonl  # noqa: E402
from device_bind import DRIVE_OWNER_EMAIL, DetachForbidden, detach_device  # noqa: E402
import drive_sync  # noqa: E402
import segment_jsonl  # noqa: E402


def test_seed_chunks_cover_devices_and_hub():
    titles = {c["title"] for c in seed_chunks()}
    assert "agent-name" in titles
    assert "hf-repos" in titles
    assert "device-map" in titles
    assert "tan-folder" in titles


def test_pack_devices_writes_three_packs(tmp_path: Path):
    layout = pack_devices(
        tmp_path,
        extra={"adapter": "/content/TAN/jtagent/adapters/adapter_demo/final"},
    )
    assert set(layout) == set(DEVICES)
    for name in DEVICES:
        pack = (tmp_path / "devices" / name / "pack.jsonl").read_text(encoding="utf-8")
        assert name in pack
        assert "1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB" in pack
        assert "adapter_demo" in pack
        readme = (tmp_path / "devices" / name / "README.md").read_text(encoding="utf-8")
        assert name in readme
    abcom = (tmp_path / "devices" / "windows_pc_abcom" / "pack.jsonl").read_text()
    assert "NOT_ASUS" in abcom or "Lenovo" in abcom
    assert '"physical_online": false' in abcom.lower() or '"physical_online": false' in abcom


def test_write_jsonl_roundtrip(tmp_path: Path):
    dest = tmp_path / "x.jsonl"
    write_jsonl(dest, [{"input": "a", "output": "b"}])
    lines = dest.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert '"input": "a"' in lines[0]


def test_drive_sync_mount_writes_folder_only_config(tmp_path: Path, monkeypatch):
    conf = tmp_path / "rclone.conf"
    monkeypatch.setenv("RCLONE_CONFIG", str(conf))
    local = tmp_path / "TAN"
    rc = drive_sync.cmd_mount(drive_sync.DEFAULT_FOLDER_ID, local)
    assert rc == 0
    text = conf.read_text(encoding="utf-8")
    assert "root_folder_id = 1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB" in text
    assert (local / ".jtagent_tan_mount.json").exists()
    marker = json_loads = __import__("json").loads(
        (local / ".jtagent_tan_mount.json").read_text(encoding="utf-8")
    )
    assert marker["full_drive_mount"] is False


def test_write_rclone_config_uses_service_account_when_set(tmp_path: Path, monkeypatch):
    conf = tmp_path / "rclone.conf"
    monkeypatch.setenv("RCLONE_CONFIG", str(conf))
    key = tmp_path / "sa.json"
    key.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("RCLONE_DRIVE_SERVICE_ACCOUNT_FILE", str(key))
    # A stale OAuth token must not be written alongside the service account.
    monkeypatch.setenv("RCLONE_DRIVE_TOKEN", '{"access_token":"stale"}')
    drive_sync.write_rclone_config(drive_sync.DEFAULT_FOLDER_ID)
    text = conf.read_text(encoding="utf-8")
    assert f"service_account_file = {key}" in text
    assert "token =" not in text
    assert "root_folder_id = 1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB" in text


def test_write_rclone_config_falls_back_to_oauth_token(tmp_path: Path, monkeypatch):
    conf = tmp_path / "rclone.conf"
    monkeypatch.setenv("RCLONE_CONFIG", str(conf))
    monkeypatch.delenv("RCLONE_DRIVE_SERVICE_ACCOUNT_FILE", raising=False)
    monkeypatch.setenv("RCLONE_DRIVE_TOKEN", '{"access_token":"t"}')
    drive_sync.write_rclone_config(drive_sync.DEFAULT_FOLDER_ID)
    text = conf.read_text(encoding="utf-8")
    assert 'token = {"access_token":"t"}' in text
    assert "service_account_file" not in text


def test_segment_jsonl_seed_fallback(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("EKA_DEVICE_ID", raising=False)
    monkeypatch.delenv("EKA_DEVICE_KEY", raising=False)
    monkeypatch.delenv("JAYTI_PG_DSN", raising=False)
    monkeypatch.delenv("EKA_PG_DSN", raising=False)
    # Ensure empty-queue path even if local DSN file exists
    monkeypatch.setattr(segment_jsonl, "_resolve_dsn", lambda: None)
    monkeypatch.setattr(segment_jsonl, "pull_from_hub", lambda: [])
    manifest = segment_jsonl.run(tmp_path)
    assert manifest["fallback_seed"] is True
    assert manifest["total_chunks"] >= 1
    cats = list((tmp_path / "training").glob("*/*.jsonl"))
    assert cats


def test_item_to_chunk_parses_json_string_content():
    item = {
        "device": "jtagent_sandbox",
        "data_type": "identity",
        "source": "jtagent-sandbox-seed",
        "content": json.dumps(
            {"category": "identity", "title": "agent-name", "input": "who", "output": "jtagent"}
        ),
        "content_hash": "abc123abc123",
    }
    chunk = segment_jsonl._item_to_chunk(item)
    assert chunk is not None
    assert chunk["category"] == "sandbox_ops"
    assert chunk["metadata"]["data_type"] == "identity"


def test_item_to_chunk_keeps_device_segments():
    item = {
        "device": "samsung_s24_ultra",
        "data_type": "whatsapp_chat",
        "source": "push",
        "content": {"title": "note", "message": "hello"},
        "content_hash": "def456def456",
    }
    chunk = segment_jsonl._item_to_chunk(item)
    assert chunk is not None
    assert chunk["category"] == "whatsapp_chat"
    assert chunk["metadata"]["priority"] == "P1"


def test_segment_skips_chrome_sqlite():
    item = {
        "device": "samsung_s24_ultra",
        "data_type": "chrome_sqlite",
        "source": "chrome-browser-data",
        "content": {"path": "/x/Login Data"},
    }
    assert segment_jsonl._item_to_chunk(item) is None


def test_id_owner_cannot_detach_even_if_same_email():
    with pytest.raises(DetachForbidden):
        detach_device(
            "windows_pc_abcom",
            actor_email=DRIVE_OWNER_EMAIL,
            owner_explicit=True,
            as_drive_owner=False,
        )


def test_drive_owner_can_detach_when_explicit():
    assert (
        detach_device(
            "windows_pc_abcom",
            actor_email=DRIVE_OWNER_EMAIL,
            owner_explicit=True,
            as_drive_owner=True,
        )
        == "windows_pc_abcom"
    )
