#!/usr/bin/env python3
"""Bidirectional sync for the shared TAN Drive folder only.

Folder id (default): 1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB
Local / Colab path:  /content/TAN  (override with --local-root)

Commands:
  mount  — configure rclone remote rooted at the folder id (no full My Drive)
  pull   — download folder contents → local root
  push   — upload jtagent/{hf,training,adapters,devices,RUN_REPORT.md} back

Never mounts all of My Drive. Skips chrome sqlite, encryption key-derivation,
and live WhatsApp DBs. After push, flushes rclone cache so Drive is not
VM-cache-only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_FOLDER_ID = "1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB"
DEFAULT_LOCAL = Path(os.environ.get("TAN_ROOT", "/content/TAN"))
REMOTE_NAME = os.environ.get("JTAGENT_RCLONE_REMOTE", "jtagent_tan")
PUSH_REL_PATHS = (
    "jtagent/hf",
    "jtagent/training",
    "jtagent/adapters",
    "jtagent/devices",
    "jtagent/RUN_REPORT.md",
)
SKIP_NAME_FRAGMENTS = (
    "chrome-browser-data",
    "key-derivation",
    "Login Data",
    "Cookies",
    "msgstore.db",
    "wa.db",
    "encryption",
)


def _rclone_bin() -> str:
    return shutil.which("rclone") or "rclone"


def _conf_dir() -> Path:
    return Path(os.environ.get("RCLONE_CONFIG_DIR", Path.home() / ".config" / "rclone"))


def _conf_path() -> Path:
    env = os.environ.get("RCLONE_CONFIG")
    if env:
        return Path(env)
    return _conf_dir() / "rclone.conf"


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, check=check, text=True, capture_output=False)


def _should_skip(path: Path | str) -> bool:
    s = str(path).replace("\\", "/").lower()
    return any(frag.lower() in s for frag in SKIP_NAME_FRAGMENTS)


def write_rclone_config(folder_id: str) -> Path:
    """Write a Drive remote rooted at folder_id only."""
    conf = _conf_path()
    conf.parent.mkdir(parents=True, exist_ok=True)
    # Prefer existing token from env / Colab; otherwise leave for `rclone config reconnect`
    token = os.environ.get("RCLONE_DRIVE_TOKEN", "").strip()
    client_id = os.environ.get("RCLONE_DRIVE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("RCLONE_DRIVE_CLIENT_SECRET", "").strip()

    lines = [
        f"[{REMOTE_NAME}]",
        "type = drive",
        "scope = drive",
        f"root_folder_id = {folder_id}",
        "team_drive =",
    ]
    if client_id:
        lines.append(f"client_id = {client_id}")
    if client_secret:
        lines.append(f"client_secret = {client_secret}")
    if token:
        # token must be JSON on one line for rclone
        lines.append(f"token = {token}")
    conf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(conf, 0o600)
    print(f"wrote rclone config → {conf} (root_folder_id={folder_id})")
    return conf


def cmd_mount(folder_id: str, local_root: Path) -> int:
    """Configure folder-only remote and optionally FUSE-mount if rclone mount works."""
    write_rclone_config(folder_id)
    local_root.mkdir(parents=True, exist_ok=True)
    marker = local_root / ".jtagent_tan_mount.json"
    marker.write_text(
        json.dumps(
            {
                "folder_id": folder_id,
                "remote": REMOTE_NAME,
                "local_root": str(local_root),
                "full_drive_mount": False,
                "mode": "rclone-root-folder-id",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    # Try FUSE mount in background when available; fall back to copy-based pull/push.
    mountpoint = local_root / "_fuse"
    if os.environ.get("JTAGENT_RCLONE_FUSE", "").lower() in {"1", "true", "yes"}:
        mountpoint.mkdir(parents=True, exist_ok=True)
        conf = _conf_path()
        try:
            _run(
                [
                    _rclone_bin(),
                    "mount",
                    f"{REMOTE_NAME}:",
                    str(mountpoint),
                    "--config",
                    str(conf),
                    "--daemon",
                    "--vfs-cache-mode",
                    "writes",
                    "--drive-root-folder-id",
                    folder_id,
                ]
            )
            print(f"fuse mount at {mountpoint}")
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(f"fuse mount unavailable ({exc}); use pull/push copy mode")
    print("MOUNT_OK")
    return 0


def cmd_pull(folder_id: str, local_root: Path) -> int:
    write_rclone_config(folder_id)
    local_root.mkdir(parents=True, exist_ok=True)
    conf = _conf_path()
    # Exclude secrets / live DBs
    excludes = [
        "--exclude",
        "**/*chrome-browser-data*/**",
        "--exclude",
        "**/*key-derivation*",
        "--exclude",
        "**/msgstore.db*",
        "--exclude",
        "**/wa.db*",
        "--exclude",
        "**/*Login Data*",
        "--exclude",
        "**/*.mp4",
    ]
    try:
        _run(
            [
                _rclone_bin(),
                "copy",
                f"{REMOTE_NAME}:",
                str(local_root),
                "--config",
                str(conf),
                "--drive-root-folder-id",
                folder_id,
                "--create-empty-src-dirs",
                "-v",
                *excludes,
            ]
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"rclone pull failed: {exc}")
        print(
            "Need Drive OAuth: set RCLONE_DRIVE_TOKEN or run "
            f"`rclone config reconnect {REMOTE_NAME}:` once on this VM / Colab."
        )
        return 1
    # Drop any skipped leftovers if copied somehow
    for p in local_root.rglob("*"):
        if p.is_file() and _should_skip(p):
            try:
                p.unlink()
                print(f"removed skipped file {p}")
            except OSError:
                pass
    print("PULL_OK", local_root)
    return 0


def cmd_push(folder_id: str, local_root: Path) -> int:
    write_rclone_config(folder_id)
    conf = _conf_path()
    pushed = []
    errors = []
    for rel in PUSH_REL_PATHS:
        src = local_root / rel
        if not src.exists():
            print(f"skip missing {src}")
            continue
        # Destination path under the folder root mirrors local relative path
        dest = f"{REMOTE_NAME}:{rel}"
        try:
            if src.is_dir():
                _run(
                    [
                        _rclone_bin(),
                        "copy",
                        str(src),
                        dest,
                        "--config",
                        str(conf),
                        "--drive-root-folder-id",
                        folder_id,
                        "--create-empty-src-dirs",
                        "-v",
                    ]
                )
            else:
                parent = str(Path(rel).parent).replace("\\", "/")
                dest_dir = f"{REMOTE_NAME}:{parent}" if parent != "." else f"{REMOTE_NAME}:"
                _run(
                    [
                        _rclone_bin(),
                        "copy",
                        str(src),
                        dest_dir,
                        "--config",
                        str(conf),
                        "--drive-root-folder-id",
                        folder_id,
                        "-v",
                    ]
                )
            pushed.append(rel)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            errors.append({"path": rel, "error": str(exc)})
    # Flush: about + lsd forces rclone to talk to Drive (not VM-cache-only).
    try:
        _run(
            [
                _rclone_bin(),
                "about",
                f"{REMOTE_NAME}:",
                "--config",
                str(conf),
                "--drive-root-folder-id",
                folder_id,
            ],
            check=False,
        )
        _run(
            [
                _rclone_bin(),
                "lsd",
                f"{REMOTE_NAME}:jtagent",
                "--config",
                str(conf),
                "--drive-root-folder-id",
                folder_id,
            ],
            check=False,
        )
    except Exception:  # noqa: BLE001
        pass

    report = {"pushed": pushed, "errors": errors, "folder_id": folder_id}
    print(json.dumps(report, indent=2))
    if errors and not pushed:
        print("PUSH_FAILED")
        return 1
    print("PUSH_OK")
    return 0 if not errors else 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="jtagent TAN Drive sync (folder id only)")
    p.add_argument("command", choices=("mount", "pull", "push"))
    p.add_argument("--folder-id", default=DEFAULT_FOLDER_ID)
    p.add_argument("--local-root", type=Path, default=DEFAULT_LOCAL)
    args = p.parse_args(argv)

    if args.folder_id != DEFAULT_FOLDER_ID:
        print(
            f"warning: non-default folder id {args.folder_id} "
            f"(expected {DEFAULT_FOLDER_ID} for TAN)",
            file=sys.stderr,
        )

    if args.command == "mount":
        return cmd_mount(args.folder_id, args.local_root)
    if args.command == "pull":
        return cmd_pull(args.folder_id, args.local_root)
    if args.command == "push":
        return cmd_push(args.folder_id, args.local_root)
    return 2


if __name__ == "__main__":
    sys.exit(main())
