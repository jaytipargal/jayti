#!/usr/bin/env python3
"""Host-side operator for jtagent Colab training + Drive publish."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HUB_URL = "https://agent.jaytipargal.tech"
SESSION = "jt-agent-gpu"
GPU = "T4"
REMOTE = "jtagent_tan"
REQUIRED_ADAPTERS = (
    "adapter_2026-09-23_sandbox_ops",
    "adapter_2026-09-23_whatsapp_chat",
    "adapter_2026-09-23_browser_data",
    "adapter_2026-09-23_infrastructure",
)

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
SEGMENT_SCRIPT = HERE / "colab_segment_e2e.py"
TAR_SCRIPT = HERE / "colab_tar_artifacts.py"
RESTORE_SCRIPT = HERE / "colab_restore_runtime.py"
DRIVE_PUSH_SCRIPT = HERE / "colab_drive_publish.py"
SIM_INGEST = HERE / "simulate_sandbox_ingest.py"
ENV_FILE = Path(
    os.environ.get(
        "JTAGENT_DEVICE_ENV",
        "/workspace/.config/jtagent-devices.env",
    )
)


def run(cmd: list[str], *, check: bool = True, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # colab-cli may stream unicode logs from remote cells; force UTF-8 so
    # Windows cp1252 consoles do not crash on characters like arrows.
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, env=env)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc


def _colab_cmd() -> list[str]:
    colab_bin = shutil.which("colab")
    if not colab_bin:
        raise FileNotFoundError("colab CLI not found in PATH")
    return [colab_bin, "--auth=oauth2"]


def colab_status(session: str) -> tuple[bool, str]:
    proc = run(_colab_cmd() + ["status", "-s", session], check=False)
    out = (proc.stdout + proc.stderr).strip()
    # Some CLI builds print "Session ... not found" with exit 0.
    if "not found" in out.lower():
        return False, out
    if proc.returncode == 0:
        return True, out
    raise RuntimeError(out)


def ensure_colab_session(session: str, gpu: str) -> tuple[bool, str]:
    exists, status = colab_status(session)
    if exists:
        return False, status
    run(_colab_cmd() + ["new", "-s", session, "--gpu", gpu], timeout=600)
    exists, status = colab_status(session)
    if not exists:
        raise RuntimeError("colab session creation failed")
    return True, status


def colab_upload(session: str, local: Path, remote: str) -> None:
    run(_colab_cmd() + ["upload", "-s", session, str(local), remote], timeout=300)


def colab_exec(session: str, script: Path, timeout: int) -> str:
    proc = run(_colab_cmd() + ["exec", "-s", session, "-f", str(script), "--timeout", str(timeout)], timeout=timeout + 120)
    return (proc.stdout + proc.stderr).strip()


def colab_download(session: str, remote: str, local: Path) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    run(_colab_cmd() + ["download", "-s", session, remote, str(local)], timeout=300)


def get_hub_status() -> dict:
    with urllib.request.urlopen(f"{HUB_URL}/status", timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def simulate_ingest() -> dict:
    if not ENV_FILE.is_file():
        raise FileNotFoundError(f"missing env file: {ENV_FILE}")
    env = os.environ.copy()
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env.setdefault(k.strip(), v.strip())
    proc = subprocess.run(["python3", str(SIM_INGEST)], text=True, capture_output=True, env=env, check=True)
    data = json.loads(proc.stdout)
    key = env.get("EKA_DEVICE_KEY", "")
    if key:
        data["key_suffix"] = key[-6:]
    return data


def build_restore_archive() -> Path | None:
    rclone = shutil.which("rclone")
    if not rclone:
        return None
    work = Path(tempfile.mkdtemp(prefix="jtagent-restore-"))
    adapters_dir = work / "adapters"
    adapters_dir.mkdir(parents=True, exist_ok=True)
    for adapter in REQUIRED_ADAPTERS:
        src = f"{REMOTE}:jtagent/adapters/{adapter}/final"
        dst = adapters_dir / adapter / "final"
        dst.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run([rclone, "copy", src, str(dst)], text=True, capture_output=True)
        if proc.returncode != 0:
            print(f"restore_copy_warn {adapter}: {proc.stderr.strip()[:120]}")
    if Path("/tmp/jt-hf").is_dir():
        shutil.copytree("/tmp/jt-hf", work / "jt-hf", dirs_exist_ok=True)
    archive = work / "jtagent-restore.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(adapters_dir, arcname="adapters")
        if (work / "jt-hf").exists():
            tar.add(work / "jt-hf", arcname="jt-hf")
    return archive


def restore_runtime(session: str) -> bool:
    archive = build_restore_archive()
    if not archive or archive.stat().st_size == 0:
        return False
    colab_upload(session, archive, "/content/jtagent-restore.tgz")
    colab_exec(session, RESTORE_SCRIPT, 300)
    return True


def run_training(session: str) -> str:
    if ENV_FILE.is_file():
        colab_upload(session, ENV_FILE, "/content/jtagent-dev.env")
    else:
        print(f"train_warn: env file missing ({ENV_FILE}); using fallback mode")
    # Execute checked-in script directly to avoid stale /tmp copies.
    out = colab_exec(session, SEGMENT_SCRIPT, 3600)
    return out


def push_drive_artifacts(session: str) -> dict:
    rclone = shutil.which("rclone")
    if rclone:
        try:
            colab_exec(session, TAR_SCRIPT, 300)
            local_tgz = Path("/tmp/jtagent-push/jtagent-artifacts-operator.tgz")
            colab_download(session, "/content/jtagent-artifacts.tgz", local_tgz)
            tree = Path("/tmp/jtagent-push/tree-operator")
            if tree.exists():
                shutil.rmtree(tree)
            tree.mkdir(parents=True, exist_ok=True)
            with tarfile.open(local_tgz, "r:gz") as tar:
                tar.extractall(tree)
            subprocess.run(
                [
                    rclone,
                    "--auto-confirm",
                    "copy",
                    str(tree / "jtagent"),
                    f"{REMOTE}:jtagent",
                ],
                check=True,
                text=True,
                timeout=180,
            )
            adapters = subprocess.run(
                [
                    rclone,
                    "--auto-confirm",
                    "lsf",
                    f"{REMOTE}:jtagent/adapters",
                    "--dirs-only",
                ],
                check=True,
                text=True,
                capture_output=True,
                timeout=120,
            ).stdout.splitlines()
            return {
                "mode": "host_rclone",
                "adapters": [a.strip() for a in adapters if a.strip()],
            }
        except Exception as exc:  # noqa: BLE001
            print(f"drive_push_warn: host_rclone failed ({type(exc).__name__}: {exc})")

    # Fallback: publish directly from Colab runtime using drive_sync.
    out = colab_exec(session, DRIVE_PUSH_SCRIPT, 1200)
    return {
        "mode": "colab_drive_sync",
        "done_marker": "COLAB_DRIVE_PUSH_DONE" in out,
        "tail": "\n".join(out.splitlines()[-40:]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Operate jtagent queue->colab->drive pipeline")
    parser.add_argument("--session", default=SESSION)
    parser.add_argument("--gpu", default=GPU)
    parser.add_argument("--simulate-ingest", action="store_true", help="Push 9 sandbox rows before queue check")
    parser.add_argument("--min-unprocessed", type=int, default=1)
    parser.add_argument(
        "--allow-seed-train",
        action="store_true",
        help="Run Colab segment/train even when Hub queue is empty (seed/pg fallback path).",
    )
    parser.add_argument("--skip-train", action="store_true")
    parser.add_argument("--skip-drive-push", action="store_true")
    args = parser.parse_args()

    report: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session": args.session,
        "hub_before": get_hub_status(),
    }

    if args.simulate_ingest:
        report["simulate_ingest"] = simulate_ingest()
        report["hub_after_ingest"] = get_hub_status()

    created, status = ensure_colab_session(args.session, args.gpu)
    report["colab_created"] = created
    report["colab_status"] = status

    if created:
        report["runtime_restored"] = restore_runtime(args.session)

    latest = get_hub_status()
    report["hub_before_train"] = latest
    unprocessed = int(latest.get("items_unprocessed") or 0)

    if args.skip_train or (unprocessed < args.min_unprocessed and not args.allow_seed_train):
        report["train"] = {"skipped": True, "items_unprocessed": unprocessed}
        print(json.dumps(report, indent=2))
        return 0

    train_out = run_training(args.session)
    report["train"] = {
        "skipped": False,
        "done_marker": "COLAB_SEGMENT_E2E_DONE" in train_out,
        "tail": "\n".join(train_out.splitlines()[-40:]),
    }
    report["hub_after_train"] = get_hub_status()

    if not args.skip_drive_push:
        report["drive_push"] = push_drive_artifacts(args.session)

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
