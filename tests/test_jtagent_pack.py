from pathlib import Path

from pack_devices import DEVICES, pack_devices, seed_chunks, write_jsonl


def test_seed_chunks_cover_devices_and_hub():
    titles = {c["title"] for c in seed_chunks()}
    assert "agent-name" in titles
    assert "hf-repos" in titles
    assert "device-map" in titles
    assert "tan-folder" in titles


def test_pack_devices_writes_three_packs(tmp_path: Path):
    layout = pack_devices(tmp_path)
    assert set(layout) == set(DEVICES)
    for name in DEVICES:
        pack = (tmp_path / "devices" / name / "pack.jsonl").read_text(encoding="utf-8")
        assert name in pack
        assert "1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB" in pack
        readme = (tmp_path / "devices" / name / "README.md").read_text(encoding="utf-8")
        assert name in readme
    abcom = (tmp_path / "devices" / "windows_pc_abcom" / "pack.jsonl").read_text()
    assert "NOT_ASUS" in abcom or "Lenovo" in abcom


def test_write_jsonl_roundtrip(tmp_path: Path):
    dest = tmp_path / "x.jsonl"
    write_jsonl(dest, [{"input": "a", "output": "b"}])
    lines = dest.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert '"input": "a"' in lines[0]
