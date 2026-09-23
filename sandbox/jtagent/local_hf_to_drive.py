#!/usr/bin/env python3
"""Upload completed local HF model/dataset files to the TAN Drive folder.

Looks in D:\\training-data (user download path) and D:\\tmp\\jt-hf-full.
Does not mount Colab. Run this after local download is complete.

Dedup clause: one local winner per filename (largest complete copy).
rclone --size-only skips any Drive file that already has the same size.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

FOLDER_ID = "1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB"
REMOTE = "jtagent_tan"
CONF = Path.home() / ".config" / "rclone" / "rclone.conf"
MODEL_FILES = (
    ".gitattributes",
    "README.md",
    "config.json",
    "generation_config.json",
    "hf_filter.txt",
    "knowledge_base.txt",
    "main.py",
    "model-00001-of-00006.safetensors",
    "model-00002-of-00006.safetensors",
    "model-00003-of-00006.safetensors",
    "model-00004-of-00006.safetensors",
    "model-00005-of-00006.safetensors",
    "model-00006-of-00006.safetensors",
    "model.safetensors.index.json",
    "preprocessor_config.json",
    "requirements.txt",
    "tokenizer.json",
    "tokenizer_config.json",
    "video_preprocessor_config.json",
    "vocab.json",
)
# Incomplete shard threshold: smallest finished shard is ~2.6GB.
MIN_SHARD_BYTES = 2 * 1024 * 1024 * 1024
SEARCH_ROOTS = (
    Path(r"D:\training-data\data"),
    Path(r"D:\training-data\target_directory"),
    Path(r"D:\training-data"),
    Path(r"D:\tmp\jt-hf-full\hf-complete"),
    Path(r"D:\tmp\jt-hf-full\jt-agent-model"),
    Path(r"D:\tmp\jt-hf-full\jt-agent-model-http"),
)
DATA_ROOTS = (
    Path(r"D:\tmp\jt-hf-full\jt-agent-data"),
    Path(r"D:\training-data\jt-agent-data"),
)


def rclone(*args: str) -> subprocess.CompletedProcess[str]:
    cmd = [
        shutil.which("rclone") or "rclone",
        "--auto-confirm",
        *args,
        "--config",
        str(CONF),
        "--drive-root-folder-id",
        FOLDER_ID,
    ]
    print("+", " ".join(cmd))
    return subprocess.run(cmd, text=True)


def find_model_file(name: str) -> Path | None:
    """Keep the largest complete copy. Drop duplicate paths of the same name."""
    best: Path | None = None
    best_size = -1
    for root in SEARCH_ROOTS:
        p = root / name
        if not p.is_file():
            continue
        size = p.stat().st_size
        if name.endswith(".safetensors") and size < MIN_SHARD_BYTES:
            continue
        if size > best_size:
            best = p
            best_size = size
    return best


def inventory() -> dict:
    found = {}
    missing = []
    incomplete = []
    for name in MODEL_FILES:
        p = find_model_file(name)
        if p is None:
            missing.append(name)
            continue
        size = p.stat().st_size
        found[name] = {"path": str(p), "bytes": size}
        if name.endswith(".safetensors") and size < MIN_SHARD_BYTES:
            incomplete.append(name)
    data_dir = next((d for d in DATA_ROOTS if d.is_dir()), None)
    return {
        "found": found,
        "missing": missing,
        "incomplete_shards": incomplete,
        "data_dir": str(data_dir) if data_dir else None,
        "ready": not missing and not incomplete and data_dir is not None,
    }


def upload(inv: dict) -> dict:
    staging = Path(r"D:\tmp\jt-hf-full\drive-stage\jt-agent-model")
    staging.mkdir(parents=True, exist_ok=True)
    for name, meta in inv["found"].items():
        dest = staging / name
        src = Path(meta["path"])
        if dest.exists() and dest.stat().st_size == src.stat().st_size:
            continue
        print(f"stage {src} -> {dest}")
        shutil.copy2(src, dest)
    rc_model = rclone(
        "copy",
        "--size-only",
        str(staging),
        f"{REMOTE}:jtagent/hf/full/jt-agent-model",
    ).returncode
    rc_model2 = rclone(
        "copy",
        "--size-only",
        str(staging),
        f"{REMOTE}:HF_Downloads/jt-agent-model",
    ).returncode
    rc_data = 0
    rc_data2 = 0
    if inv["data_dir"]:
        rc_data = rclone(
            "copy",
            "--size-only",
            inv["data_dir"],
            f"{REMOTE}:jtagent/hf/full/jt-agent-data",
            "--exclude",
            ".cache/**",
        ).returncode
        rc_data2 = rclone(
            "copy",
            "--size-only",
            inv["data_dir"],
            f"{REMOTE}:HF_Downloads/jt-agent-data",
            "--exclude",
            ".cache/**",
        ).returncode
    return {
        "model_rc": [rc_model, rc_model2],
        "data_rc": [rc_data, rc_data2],
        "ok": max(rc_model, rc_model2, rc_data, rc_data2) == 0,
    }


def main() -> int:
    check_only = "--check" in sys.argv
    inv = inventory()
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **inv, "check_only": check_only}, indent=2))
    if check_only:
        return 0 if inv["ready"] else 2
    if not inv["ready"]:
        print("NOT_READY: finish local HF download first")
        return 2
    result = upload(inv)
    print(json.dumps(result, indent=2))
    print("LOCAL_HF_TO_DRIVE_DONE" if result["ok"] else "LOCAL_HF_TO_DRIVE_FAILED")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
