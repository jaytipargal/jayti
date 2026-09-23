#!/usr/bin/env python3
"""Restore Colab runtime files from /content/jtagent-restore.tgz."""

from __future__ import annotations

import shutil
import subprocess
import tarfile
from pathlib import Path

ARCHIVE = Path("/content/jtagent-restore.tgz")
EXTRACT = Path("/tmp/jtagent-restore")
REPO = Path("/content/jayti")
BRANCH = "cursor/hub-json-content-coerce-7af4"


def ensure_repo() -> None:
    target = REPO / "scripts" / "eka_train.py"
    if target.is_file():
        return
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


def restore_archive() -> None:
    if not ARCHIVE.is_file():
        print("archive_missing", str(ARCHIVE))
        return
    if EXTRACT.exists():
        shutil.rmtree(EXTRACT)
    EXTRACT.mkdir(parents=True, exist_ok=True)
    with tarfile.open(ARCHIVE, "r:gz") as tar:
        tar.extractall(EXTRACT)
    src_adapters = EXTRACT / "adapters"
    dst_adapters = Path("/content/TAN/jtagent/adapters")
    dst_adapters.mkdir(parents=True, exist_ok=True)
    if src_adapters.is_dir():
        shutil.copytree(src_adapters, dst_adapters, dirs_exist_ok=True)

    src_seed = EXTRACT / "jt-hf"
    if src_seed.is_dir():
        dst_seed = Path("/content/jtagent-hf-seed")
        shutil.copytree(src_seed, dst_seed, dirs_exist_ok=True)


def main() -> int:
    ensure_repo()
    restore_archive()
    root = Path("/content/TAN/jtagent/adapters")
    for item in sorted(root.glob("adapter_*/final/adapter_model.safetensors")):
        print("adapter_final", item)
    print("repo_ok", (REPO / "scripts" / "eka_train.py").is_file())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
