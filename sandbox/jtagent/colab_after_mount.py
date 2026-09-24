#!/usr/bin/env python3
"""Single Colab run after Drive is mounted and HF files are already on Drive.

Order:
  1. Verify Drive mount + TAN + full HF model/dataset
  2. Segment Hub queue or seed fallback
  3. Train GPT-2 LoRA per category
  4. Pack device folders
  5. Push adapters/training/devices back to TAN
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DRIVE = Path("/content/drive")
TAN = Path("/content/TAN")
ROOT = Path("/content/TAN/jtagent")
FOLDER_ID = "1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB"
REPO = Path("/content/jayti")
BRANCH = os.environ.get("JTAGENT_BRANCH", "main")
REQUIRED_SHARDS = tuple(f"model-0000{i}-of-00006.safetensors" for i in range(1, 7))


def ensure_repo() -> Path:
    target = REPO / "sandbox" / "jtagent" / "segment_jsonl.py"
    if not target.is_file():
        if REPO.exists():
            shutil.rmtree(REPO)
        subprocess.check_call(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "-b",
                BRANCH,
                "https://github.com/jaytipargal/jayti.git",
                str(REPO),
            ]
        )
    sandbox = REPO / "sandbox" / "jtagent"
    if str(sandbox) not in sys.path:
        sys.path.insert(0, str(sandbox))
    return sandbox


def find_hf_model() -> Path | None:
    candidates = [
        Path("/content/drive/MyDrive/HF_Downloads/jt-agent-model"),
        Path("/content/drive/MyDrive/HF_Downloads/jtagent-jt-agent-model"),
        Path("/content/drive/MyDrive/TAN/jtagent/hf/full/jt-agent-model"),
        Path("/content/drive/MyDrive/TAN/jtagent/hf/full/jtagent-jt-agent-model"),
        ROOT / "hf" / "full" / "jt-agent-model",
        ROOT / "hf" / "full" / "jtagent-jt-agent-model",
        Path("/content/TAN/jtagent/hf/full/jt-agent-model"),
        Path("/content/TAN/jtagent/hf/full/jtagent-jt-agent-model"),
    ]
    my = Path("/content/drive/MyDrive")
    if my.is_dir():
        for name in ("jt-agent-model", "jtagent-jt-agent-model"):
            candidates.extend(sorted(my.glob(f"**/{name}"))[:8])
    for p in candidates:
        if p.is_dir() and all((p / s).is_file() and (p / s).stat().st_size > 1_000_000_000 for s in REQUIRED_SHARDS):
            return p
    return None


def find_hf_data() -> Path | None:
    candidates = [
        Path("/content/drive/MyDrive/HF_Downloads/jt-agent-data"),
        Path("/content/drive/MyDrive/HF_Downloads/jtagent-jt-agent-data"),
        Path("/content/drive/MyDrive/TAN/jtagent/hf/full/jt-agent-data"),
        Path("/content/drive/MyDrive/TAN/jtagent/hf/full/jtagent-jt-agent-data"),
        ROOT / "hf" / "full" / "jt-agent-data",
        ROOT / "hf" / "full" / "jtagent-jt-agent-data",
    ]
    my = Path("/content/drive/MyDrive")
    if my.is_dir():
        for name in ("jt-agent-data", "jtagent-jt-agent-data"):
            candidates.extend(sorted(my.glob(f"**/{name}"))[:8])
    for p in candidates:
        if p.is_dir() and any(p.rglob("*.json")):
            return p
    return None


def main() -> int:
    sandbox = ensure_repo()
    import drive_sync  # type: ignore
    import segment_jsonl  # type: ignore
    import segment_train  # type: ignore

    mounted = (DRIVE / "MyDrive").exists() or TAN.exists()
    drive_sync.cmd_mount(FOLDER_ID, TAN)
    model = find_hf_model()
    data = find_hf_data()
    ROOT.mkdir(parents=True, exist_ok=True)
    pointer = {
        "model_path": str(model) if model else None,
        "dataset_path": str(data) if data else None,
        "mounted": mounted,
        "folder_id": FOLDER_ID,
        "train_mode": "gpt2-lora-per-category (not full 8B SFT)",
    }
    (ROOT / "hf").mkdir(parents=True, exist_ok=True)
    (ROOT / "hf" / "HUB_POINTER.md").write_text(json.dumps(pointer, indent=2) + "\n", encoding="utf-8")

    if not model or not data:
        report = {
            "ok": False,
            "error": "hf_full_not_on_drive",
            "pointer": pointer,
        }
        print(json.dumps(report, indent=2))
        print("AFTER_MOUNT_E2E_BLOCKED")
        return 2

    manifest = segment_jsonl.run(ROOT)
    train = segment_train.train_categories(ROOT)
    push_rc = drive_sync.cmd_push(FOLDER_ID, TAN)
    ok = push_rc == 0
    report = {
        "ok": ok,
        "ts": datetime.now(timezone.utc).isoformat(),
        "pointer": pointer,
        "segment": manifest,
        "train": train,
        "drive_push_rc": push_rc,
        "sandbox": str(sandbox),
    }
    (ROOT / "RUN_REPORT.md").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print("AFTER_MOUNT_E2E_DONE" if ok else "AFTER_MOUNT_E2E_FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
