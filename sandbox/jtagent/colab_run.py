#!/usr/bin/env python3
"""Colab entry: Drive sync → segment JSONL → GPT-2 LoRA per category → packs → push.

Paths stay under /content/TAN/jtagent (folder id only). Does not full-SFT the
~29.5GB Hub model shards. Falls back to seed_chunks only when ingestion is empty.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path("/content/TAN/jtagent")
TAN = Path("/content/TAN")
FOLDER_ID = "1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB"
HF_MODEL = "go4garage01/jt-agent-model"
HF_DATA = "go4garage01/jt-agent-data"


def _pip(pkgs: list[str]) -> None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *pkgs])


def _sandbox_dir() -> Path:
    for p in (
        Path("/content/jayti/sandbox/jtagent"),
        Path("/content/sandbox/jtagent"),
        Path(__file__).resolve().parent,
    ):
        if (p / "pack_devices.py").exists():
            return p
    return Path("/content/jayti/sandbox/jtagent")


def sync_hf(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    seed = Path("/content/jtagent-hf-seed")
    pointer = {
        "model": HF_MODEL,
        "dataset": HF_DATA,
        "weights": "not copied (six safetensor shards, ~29.5GB); train GPT-2 LoRA here",
        "skipped": [
            "config/encryption/key-derivation.json",
            "config/sources/chrome.json",
            "config/sources/whatsapp.json",
            "jtagent/chrome-browser-data sqlite",
        ],
    }
    if (seed / "model").exists():
        import shutil

        (dest / "model-meta").mkdir(parents=True, exist_ok=True)
        for p in (seed / "model").iterdir():
            if p.is_file() and p.suffix in {".md", ".txt", ".json"}:
                shutil.copy2(p, dest / "model-meta" / p.name)
        if (seed / "data").exists():
            (dest / "dataset-card").mkdir(parents=True, exist_ok=True)
            for p in (seed / "data").iterdir():
                if p.is_file():
                    shutil.copy2(p, dest / "dataset-card" / p.name)
        print("hf_seed_copied", dest)
    else:
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            _pip(["huggingface_hub"])
            from huggingface_hub import snapshot_download

        try:
            snapshot_download(
                HF_MODEL,
                local_dir=str(dest / "model-meta"),
                allow_patterns=[
                    "README.md",
                    "config.json",
                    "generation_config.json",
                    "knowledge_base.txt",
                    "hf_filter.txt",
                    "requirements.txt",
                ],
            )
            snapshot_download(
                HF_DATA,
                repo_type="dataset",
                local_dir=str(dest / "dataset-card"),
                allow_patterns=["README.md", ".gitattributes"],
            )
        except Exception as exc:  # noqa: BLE001 — metadata is optional for GPT-2 LoRA
            pointer["hf_meta_error"] = f"{type(exc).__name__}: {exc}"
            print("hf_meta_skip", type(exc).__name__)
            (dest / "model-meta").mkdir(parents=True, exist_ok=True)
            (dest / "model-meta" / "README.md").write_text(
                f"# {HF_MODEL}\n\nMetadata download skipped: {type(exc).__name__}\n",
                encoding="utf-8",
            )
    (dest / "HUB_POINTER.md").write_text(
        json.dumps(pointer, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    print("jtagent sandbox colab_run")
    print("NO_FULL_DRIVE_MOUNT")
    ROOT.mkdir(parents=True, exist_ok=True)
    sandbox = _sandbox_dir()
    sys.path.insert(0, str(sandbox))
    sys.path.insert(0, str(sandbox.parent.parent / "scripts"))

    import drive_sync  # type: ignore
    import segment_jsonl  # type: ignore
    import segment_train  # type: ignore

    drive_sync.cmd_mount(FOLDER_ID, TAN)
    pull_rc = drive_sync.cmd_pull(FOLDER_ID, TAN)
    print("drive_pull_rc", pull_rc)

    sync_hf(ROOT / "hf")
    manifest = segment_jsonl.run(ROOT)
    print("segment_manifest", json.dumps(manifest))

    report = segment_train.train_categories(ROOT)
    push_rc = drive_sync.cmd_push(FOLDER_ID, TAN)
    print("drive_push_rc", push_rc)

    final = {
        "root": str(ROOT),
        "folder_id": FOLDER_ID,
        "segment": manifest,
        "train": report,
        "drive_pull_rc": pull_rc,
        "drive_push_rc": push_rc,
    }
    (ROOT / "RUN_REPORT.md").write_text(json.dumps(final, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(final, indent=2))
    print("JTAGENT_COLAB_DONE")


if __name__ == "__main__":
    main()
