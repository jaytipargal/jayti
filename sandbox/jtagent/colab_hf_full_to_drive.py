#!/usr/bin/env python3
"""Download complete HF model + dataset onto the mounted Colab Drive.

Does not skip shards. Does not invent extra repos. Writes to:
  /content/drive/MyDrive/HF_Downloads/{jt-agent-model,jt-agent-data}
and, if the TAN shared folder is visible, also:
  <TAN>/jtagent/hf/full/{jt-agent-model,jt-agent-data}
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DRIVE_MOUNT = Path("/content/drive")
HF_DOWNLOADS = DRIVE_MOUNT / "MyDrive" / "HF_Downloads"
TAN_FOLDER_ID = "1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB"
# Live jtagent Hub sources (private). Do not stage the full set on Colab disk
# (~207GB free). Stream one repo/file onto Drive, then delete the local copy.
# chrome-browser-data is skipped (browser DBs / credentials).
MODELS = [
    "jtagent/jt-agent-model",
    "go4garage01/jt-agent-model",
]
DATASETS = [
    "jtagent/jt-agent-data",
    "jtagent/jt-agent-dataset",
]
BUCKETS = [
    "jtagent/jt-agent-bucket",
    "jtagent/Qwen3.8-27B-Uncensored-MLX-bucket",
]
SKIP_REPOS = (
    "jtagent/chrome-browser-data",
)
TOKEN_CANDIDATES = (
    Path("/content/hf.token"),
    Path("/content/jtagent-hf.token"),
)


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd)


def mount_drive() -> None:
    from google.colab import drive  # type: ignore

    # Never mkdir /content/drive first. A local folder there makes
    # drive.mount fail with "Mountpoint must not already contain files".
    my = DRIVE_MOUNT / "MyDrive"
    if my.exists():
        print("DRIVE_ALREADY_MOUNTED", DRIVE_MOUNT, flush=True)
        return
    if DRIVE_MOUNT.exists():
        stale = Path("/tmp/drive_stale_local")
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)
        shutil.move(str(DRIVE_MOUNT), str(stale))
        shutil.rmtree(stale, ignore_errors=True)
        print("REMOVED_STALE_LOCAL_DRIVE", flush=True)
    drive.mount(str(DRIVE_MOUNT), force_remount=False)
    if not my.exists():
        raise RuntimeError("Drive mount completed but MyDrive is missing")
    print("DRIVE_MOUNTED", DRIVE_MOUNT, flush=True)


def find_tan() -> Path | None:
    candidates = [
        Path("/content/drive/MyDrive/TAN"),
        Path("/content/TAN"),
        Path("/content/drive/Shareddrives/TAN"),
    ]
    my = Path("/content/drive/MyDrive")
    shared = Path("/content/drive/Shareddrives")
    if my.is_dir():
        candidates.extend(sorted(my.glob("TAN"))[:5])
        candidates.extend(sorted(my.glob("**/jtagent"))[:5])
    if shared.is_dir():
        candidates.extend(sorted(shared.glob("*/TAN"))[:5])
        candidates.extend(sorted(shared.glob("TAN"))[:5])
    for p in candidates:
        if p.is_dir() and p.name == "jtagent":
            print("TAN_CANDIDATE", p.parent, flush=True)
            return p.parent
        if p.is_dir():
            print("TAN_CANDIDATE", p, flush=True)
            return p
    return None


def load_hf_token() -> str:
    env = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if env:
        return env.strip()
    for path in TOKEN_CANDIDATES:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    raise FileNotFoundError("HF token missing: upload /content/hf.token or set HF_TOKEN")


def _same_size(src: Path, dest: Path) -> bool:
    return dest.is_file() and src.is_file() and dest.stat().st_size == src.stat().st_size


def copy_missing(src: Path, dest: Path) -> dict:
    """Copy src -> dest, skipping files that already exist with the same size."""
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    skipped = 0
    for path in src.rglob("*"):
        if not path.is_file() or ".cache" in path.parts:
            continue
        target = dest / path.relative_to(src)
        if _same_size(path, target):
            skipped += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    print(f"DEDUP_COPY {src} -> {dest} copied={copied} skipped_dup={skipped}", flush=True)
    return {"copied": copied, "skipped_dup": skipped}


def snapshot(repo_id: str, repo_type: str, dest: Path) -> dict:
    from huggingface_hub import snapshot_download

    dest.mkdir(parents=True, exist_ok=True)
    print(f"DOWNLOAD_START {repo_type} {repo_id} -> {dest}", flush=True)
    path = snapshot_download(
        repo_id=repo_id,
        repo_type=repo_type,
        local_dir=str(dest),
        local_dir_use_symlinks=False,
        max_workers=2,
    )
    files = sorted(p for p in Path(path).rglob("*") if p.is_file() and ".cache" not in p.parts)
    total = sum(p.stat().st_size for p in files)
    report = {
        "repo_id": repo_id,
        "repo_type": repo_type,
        "path": str(path),
        "file_count": len(files),
        "bytes": total,
        "files": [str(p.relative_to(path)) for p in files],
    }
    print(json.dumps({"download_done": report}, indent=2), flush=True)
    return report


def main() -> int:
    print("HF_FULL_TO_DRIVE_START", datetime.now(timezone.utc).isoformat(), flush=True)
    run([sys.executable, "-m", "pip", "install", "-q", "huggingface_hub"])
    mount_drive()

    from huggingface_hub import login

    login(token=load_hf_token(), add_to_git_credential=False)
    HF_DOWNLOADS.mkdir(parents=True, exist_ok=True)

    tan = find_tan()
    reports = []
    for repo, rtype in [(m, "model") for m in MODELS] + [(d, "dataset") for d in DATASETS]:
        dest = HF_DOWNLOADS / repo.split("/")[-1]
        report = snapshot(repo, rtype, dest)
        reports.append(report)
        if tan is None:
            continue
        extra = tan / "jtagent" / "hf" / "full" / repo.split("/")[-1]
        if extra.resolve() == dest.resolve():
            continue
        extra.parent.mkdir(parents=True, exist_ok=True)
        print(f"COPY_TO_TAN {dest} -> {extra}", flush=True)
        report["tan_copy"] = str(extra)
        report["tan_dedup"] = copy_missing(dest, extra)

    final = {
        "ok": True,
        "drive_mount": str(DRIVE_MOUNT),
        "hf_downloads": str(HF_DOWNLOADS),
        "tan": str(tan) if tan else None,
        "tan_folder_id": TAN_FOLDER_ID,
        "reports": reports,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    marker = HF_DOWNLOADS / "HF_FULL_TRANSFER.json"
    marker.write_text(json.dumps(final, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(final, indent=2), flush=True)
    print("HF_FULL_TO_DRIVE_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
