#!/usr/bin/env python3
"""Run the Drive notebook body on Colab CLI (no userdata / drive.mount UI)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

FOLDER_ID = "1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB"
REPO = Path("/content/jayti")
SHARDS = [f"model-0000{i}-of-00006.safetensors" for i in range(1, 7)]
NEW6 = [
    ("jtagent/Qwen3.8-27B-Uncensored-MLX-bucket", "model", "Qwen3.8-27B-Uncensored-MLX-bucket"),
    ("jtagent/jt-agent-model", "model", "jtagent-jt-agent-model"),
    ("jtagent/jt-agent-data", "model", "jt-agent-data"),
    ("jtagent/jt-agent-dataset", "dataset", "jt-agent-dataset"),
    ("jtagent/chrome-browser-data", "dataset", "chrome-browser-data"),
    ("jtagent/jt-agent-data", "dataset", "jt-agent-data"),
]


def first_dir(*cands):
    for p in cands:
        if p.is_dir():
            return p
    my = Path("/content/drive/MyDrive")
    if my.is_dir():
        hits = sorted(my.glob("TAN*"))[:4] + sorted(my.glob("**/jtagent"))[:8]
        for p in hits:
            if p.is_dir():
                return p if p.name.strip() == "TAN" else p.parent
    return None


def find_named(name, tan):
    roots = [tan, Path("/content/drive/MyDrive/HF_Downloads"), Path("/content/drive/MyDrive"), Path("/content/TAN")]
    for root in roots:
        if root is None or not Path(root).exists():
            continue
        for p in [Path(root) / name, Path(root) / "jtagent" / "hf" / "full" / name]:
            if p.is_dir():
                return p
        hits = sorted(Path(root).glob(f"**/{name}"))[:6]
        if hits:
            return hits[0]
    return None


def main() -> int:
    print("NOTEBOOK_BODY_START", flush=True)
    print("drive_mydrive", (Path("/content/drive") / "MyDrive").exists(), flush=True)

    if not (REPO / "sandbox" / "jtagent" / "segment_jsonl.py").is_file():
        if REPO.exists():
            shutil.rmtree(REPO)
        subprocess.check_call(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "-b",
                "main",
                "https://github.com/jaytipargal/jayti.git",
                str(REPO),
            ]
        )
    sandbox = REPO / "sandbox" / "jtagent"
    if str(sandbox) not in sys.path:
        sys.path.insert(0, str(sandbox))

    import drive_sync
    import segment_jsonl
    import segment_train

    TAN = first_dir(
        Path("/content/drive/MyDrive/TAN"),
        *sorted(Path("/content/drive/MyDrive").glob("TAN*"))[:4] if Path("/content/drive/MyDrive").exists() else [],
        Path("/content/drive/Shareddrives/TAN"),
        Path("/content/TAN"),
    )
    drive_sync.cmd_mount(FOLDER_ID, Path("/content/TAN"))
    if TAN is None and Path("/content/TAN").is_dir():
        TAN = Path("/content/TAN")
    print("TAN", TAN, flush=True)

    MODEL = find_named("jt-agent-model", TAN)
    DATA = find_named("jt-agent-data", TAN)
    print("MODEL", MODEL, flush=True)
    print("DATA", DATA, flush=True)
    missing = []
    if MODEL:
        for s in SHARDS:
            p = MODEL / s
            print(s, p.exists(), p.stat().st_size if p.exists() else 0, flush=True)
        missing = [s for s in SHARDS if not (MODEL / s).is_file() or (MODEL / s).stat().st_size < 1_000_000_000]
    print("SHARDS_PENDING" if missing else "SHARDS_OK", missing or 6, flush=True)

    rows = []
    for repo, kind, folder in NEW6:
        path = find_named(folder, TAN)
        files = 0
        total = 0
        if path is not None:
            for f in path.rglob("*"):
                if f.is_file() and ".cache" not in f.parts:
                    files += 1
                    total += f.stat().st_size
        row = {
            "repo": repo,
            "type": kind,
            "path": str(path) if path else None,
            "files": files,
            "bytes": total,
            "status": "ok" if files else "PENDING",
        }
        rows.append(row)
        print(repo, kind, row["status"], files, total, path, flush=True)
    print("NEW6_PENDING", [f"{r['repo']} ({r['type']})" for r in rows if r["status"] == "PENDING"], flush=True)

    ROOT = (Path(TAN) / "jtagent") if TAN is not None else Path("/content/TAN/jtagent")
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "hf").mkdir(parents=True, exist_ok=True)
    (ROOT / "hf" / "HUB_POINTER.md").write_text(
        json.dumps({"model": str(MODEL), "dataset": str(DATA), "root": str(ROOT), "new6": rows}, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = segment_jsonl.run(ROOT)
    print(json.dumps(manifest, indent=2), flush=True)
    report = segment_train.train_categories(ROOT)
    push_rc = drive_sync.cmd_push(FOLDER_ID, Path(TAN) if TAN else Path("/content/TAN"))
    final = {"train": report, "drive_push_rc": push_rc, "root": str(ROOT), "new6": rows}
    (ROOT / "RUN_REPORT.md").write_text(json.dumps(final, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(final, indent=2), flush=True)
    print("JTAGENT_NOTEBOOK_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
