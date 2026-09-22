from pathlib import Path
import sys

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox" / "jtagent"
sys.path.insert(0, str(SANDBOX))

from pack_devices import DEVICES, pack_devices, seed_chunks, write_jsonl  # noqa: E402
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


def test_segment_skips_chrome_sqlite():
    item = {
        "device": "samsung_s24_ultra",
        "data_type": "chrome_sqlite",
        "source": "chrome-browser-data",
        "content": {"path": "/x/Login Data"},
    }
    assert segment_jsonl._item_to_chunk(item) is None
