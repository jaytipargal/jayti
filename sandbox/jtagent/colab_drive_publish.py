#!/usr/bin/env python3
"""Colab-side Drive publish fallback for jtagent artifacts.

This is used by colab_operator when host-side rclone is unavailable or not
authenticated. It publishes from /content/TAN directly using drive_sync
inside the Colab runtime.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

FOLDER_ID = os.environ.get("JTAGENT_TAN_FOLDER_ID", "1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB")
REPO = Path("/content/jayti")
BRANCH = os.environ.get("JTAGENT_BRANCH", "main")
TAN = Path("/content/TAN")


def ensure_repo() -> None:
    target = REPO / "sandbox" / "jtagent" / "drive_sync.py"
    if target.is_file():
        return
    if REPO.exists():
        subprocess.check_call(["rm", "-rf", str(REPO)])
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


def main() -> int:
    ensure_repo()
    sandbox = REPO / "sandbox" / "jtagent"
    if str(sandbox) not in sys.path:
        sys.path.insert(0, str(sandbox))
    import drive_sync  # type: ignore

    TAN.mkdir(parents=True, exist_ok=True)
    mount_rc = drive_sync.cmd_mount(FOLDER_ID, TAN)
    push_rc = drive_sync.cmd_push(FOLDER_ID, TAN)
    report = {
        "ok": push_rc == 0,
        "folder_id": FOLDER_ID,
        "mount_rc": mount_rc,
        "push_rc": push_rc,
    }
    print(json.dumps(report, indent=2))
    print("COLAB_DRIVE_PUSH_DONE" if report["ok"] else "COLAB_DRIVE_PUSH_FAILED")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
