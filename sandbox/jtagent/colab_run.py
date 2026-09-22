#!/usr/bin/env python3
"""Colab entry: HF metadata → TAN/jtagent → GPT-2 LoRA → per-device packs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path("/content/TAN/jtagent")
HF_MODEL = "go4garage01/jt-agent-model"
HF_DATA = "go4garage01/jt-agent-data"


def _pip(pkgs: list[str]) -> None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *pkgs])


def sync_hf(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    seed = Path("/content/jtagent-hf-seed")
    if (seed / "model").exists():
        import shutil

        if (seed / "model").exists():
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
    (dest / "HUB_POINTER.md").write_text(
        json.dumps(pointer, indent=2) + "\n", encoding="utf-8"
    )


def train_gpt2_lora(chunks_path: Path, adapter_dir: Path) -> str | None:
    _pip(["torch", "transformers", "peft", "datasets", "accelerate", "torchao>=0.16"])
    sys.path.insert(0, "/content/google-colab-cli")  # unused; jayti scripts next
    jayti_scripts = Path("/content/jayti/scripts")
    if jayti_scripts.exists():
        sys.path.insert(0, str(jayti_scripts))
    try:
        import eka_train
    except ImportError:
        print("eka_train not on VM; skip LoRA (pack still written)")
        return None

    eka_train.TRAINING_FILE = str(chunks_path)
    eka_train.ADAPTER_DIR = str(adapter_dir)
    eka_train.NUM_EPOCHS = 1
    eka_train.BATCH_SIZE = 1
    eka_train.MAX_SEQ_LEN = 128
    chunks = eka_train.load_training_data(date_str=None, batch_size=20)
    return eka_train.train_lora(chunks, date.today().isoformat())


def main() -> None:
    print("jtagent sandbox colab_run")
    print("NO_FULL_DRIVE_MOUNT")
    ROOT.mkdir(parents=True, exist_ok=True)
    sandbox_src = Path("/content/jayti/sandbox/jtagent")
    if not sandbox_src.exists():
        sandbox_src = Path("/content/sandbox/jtagent")
    sys.path.insert(0, str(sandbox_src if sandbox_src.exists() else "/content"))
    from pack_devices import pack_devices, seed_chunks, write_jsonl  # type: ignore

    sync_hf(ROOT / "hf")
    chunks_path = ROOT / "training" / "sandbox_chunks.jsonl"
    # First 10 lines skipped by eka_train.load_training_data
    pad = [{"input": f"pad-{i}", "output": "ok"} for i in range(10)]
    write_jsonl(chunks_path, pad + seed_chunks())
    adapter = None
    try:
        adapter = train_gpt2_lora(chunks_path, ROOT / "adapters")
    except Exception as exc:
        print("train_error", type(exc).__name__, exc)
    layout = pack_devices(ROOT, extra={"adapter": adapter})
    report = {
        "root": str(ROOT),
        "adapter": adapter,
        "devices": layout,
        "files": sorted(str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file())[:80],
    }
    (ROOT / "RUN_REPORT.md").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print("JTAGENT_COLAB_DONE")


if __name__ == "__main__":
    main()
