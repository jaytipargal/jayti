#!/usr/bin/env python3
"""Deploy jtagent on this host: segment, pack devices, push to Drive, optional Hub push.

Prints a device-wise JSON report. Never prints API keys or document bodies.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SCRIPTS = REPO / "scripts"
DEFAULT_ROOT = Path(os.environ.get("JTAGENT_ROOT", r"D:\tmp\jtagent-e2e"))
HUB = os.environ.get("EKA_VPS_URL", "https://agent.jaytipargal.tech").rstrip("/")
REMOTE = os.environ.get("JTAGENT_RCLONE_REMOTE", "jtagent_tan")
FOLDER_ID = "1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB"
DEVICES = ("samsung_s24_ultra", "asus_vivobook", "windows_pc_abcom")
ADAPTER_PREFIX = "adapter_2026-09-23_"
REQUIRED_ADAPTERS = (
    "sandbox_ops",
    "whatsapp_chat",
    "browser_data",
    "infrastructure",
    "extracted_text",
)


def _http_json(url: str, timeout: float = 20.0) -> tuple[int, dict | str]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        code = exc.code
    try:
        return code, json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return code, raw[:120]


def _ensure_device_env() -> bool:
    """Write ~/.eka_agent/device.env when both vars are in the environment."""
    device_id = os.environ.get("EKA_DEVICE_ID", "")
    device_key = os.environ.get("EKA_DEVICE_KEY", "")
    if not device_id or not device_key:
        return False
    dest = Path.home() / ".eka_agent" / "device.env"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        return True
    dest.write_text(
        f"EKA_DEVICE_ID={device_id}\n"
        f"EKA_DEVICE_KEY={device_key}\n"
        f"EKA_VPS_URL={HUB}\n",
        encoding="utf-8",
    )
    return True


def _list_drive_adapters() -> list[str]:
    rclone = shutil.which("rclone")
    if not rclone:
        return []
    conf = os.environ.get("RCLONE_CONFIG") or str(Path.home() / ".config" / "rclone" / "rclone.conf")
    proc = subprocess.run(
        [
            rclone,
            "lsf",
            f"{REMOTE}:jtagent/adapters",
            "--dirs-only",
            "--config",
            conf,
            "--drive-root-folder-id",
            FOLDER_ID,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if proc.returncode != 0:
        return []
    return [ln.strip().rstrip("/") for ln in proc.stdout.splitlines() if ln.strip()]


def _ingested_local() -> dict[str, dict]:
    root = Path(r"D:\training-data\daily_ingestion")
    out: dict[str, dict] = {}
    if not root.is_dir():
        return out
    for fp in root.rglob("*.jsonl"):
        by_type: Counter[str] = Counter()
        rows = 0
        with fp.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rows += 1
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    by_type["bad_json"] += 1
                    continue
                by_type[str(obj.get("data_type") or "unknown")] += 1
        out[fp.stem] = {"rows": rows, "by_type": dict(by_type), "file": str(fp)}
    return out


def _host_device() -> str:
    return os.environ.get("EKA_DEVICE_ID") or "windows_pc_abcom"


def _pending_push(device: str) -> dict:
    if device != _host_device():
        return {"pending": None, "by_type": {}, "last_sync": None, "skipped": "not this host"}

    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import eka_agent_push as push  # type: ignore

    collect_fn = {
        "windows_pc_abcom": push.collect_windows_pc_abcom,
        "samsung_s24_ultra": push.collect_samsung_s24_ultra,
        "asus_vivobook": push.collect_asus_vivobook,
    }.get(device)
    if collect_fn is None:
        return {"pending": 0, "by_type": {}, "last_sync": None}
    last_sync = push.get_last_sync(device)
    items = collect_fn(last_sync)
    by_type = Counter(str(i.get("data_type") or "unknown") for i in items)
    return {"pending": len(items), "by_type": dict(by_type), "last_sync": last_sync}


def _hub_push(device: str) -> dict:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import eka_agent_push as push  # type: ignore

    if not push.DEVICE_KEY:
        return {"ok": False, "skipped": "no EKA_DEVICE_KEY — register on VPS first"}
    collect_fn = {
        "windows_pc_abcom": push.collect_windows_pc_abcom,
    }.get(device)
    if collect_fn is None:
        return {"ok": False, "skipped": f"no collector on this host for {device}"}
    last_sync = push.get_last_sync(device)
    items = collect_fn(last_sync)
    if not items:
        return {"ok": True, "pushed": 0}
    result = push.push_items(items, device)
    if result.get("inserted", 0) > 0 or result.get("duplicates", 0) > 0:
        push.save_last_sync(device, datetime.now(timezone.utc).isoformat())
    return {"ok": result.get("errors", 0) == 0, **result}


def _segment(root: Path) -> dict:
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import segment_jsonl  # type: ignore

    return segment_jsonl.run(root)


def _pack(root: Path, adapters: list[str]) -> dict:
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import pack_devices  # type: ignore

    by_cat = {}
    for name in REQUIRED_ADAPTERS:
        match = next((a for a in adapters if a.endswith(name) or name in a), None)
        if match:
            by_cat[name] = f"jtagent/adapters/{match}/final"
    extra = {
        "adapters_by_category": by_cat,
        "adapter": by_cat.get("extracted_text") or (f"jtagent/adapters/{adapters[0]}/final" if adapters else None),
        "gpu": "T4",
        "colab_session": "Copy of jtagent_colab 2.ipynb",
    }
    layout = pack_devices.pack_devices(root, extra=extra)
    return {"layout": layout, "adapters_by_category": by_cat}


def _write_device_map(root: Path, ingested: dict, pending: dict[str, dict]) -> Path:
    from device_bind import ATTACHED_DEVICE_IDS, DRIVE_OWNER_EMAIL  # type: ignore

    route = {
        "agent": "jtagent",
        "hub": HUB,
        "drive_folder_id": FOLDER_ID,
        "auth": {
            "master_key_header": "X-Api-Key",
            "device_header": "X-Device-Id",
            "key_location": "device.env on that device only, not inside Drive files",
        },
        "detach_rule": {
            "drive_owner": DRIVE_OWNER_EMAIL,
            "attached_ids": list(ATTACHED_DEVICE_IDS),
            "strict": (
                "Only the Drive owner can remove an attached id, by an explicit "
                "Drive-owner action. The id owner cannot. The agent cannot remove "
                "an id by itself."
            ),
        },
        "devices": {},
    }
    for name in DEVICES:
        ing = ingested.get(name, {})
        pend = pending.get(name, {})
        route["devices"][name] = {
            "owns": {
                "windows_pc_abcom": ["Desktop", "Documents", "Downloads", "jtagent/training"],
                "samsung_s24_ultra": ["jtagent/devices/samsung_s24_ultra"],
                "asus_vivobook": ["jtagent/devices/asus_vivobook"],
            }[name],
            "ingested_rows": ing.get("rows", 0),
            "ingested_by_type": ing.get("by_type", {}),
            "pending_collect": pend.get("pending", 0),
            "pending_by_type": pend.get("by_type", {}),
            "last_sync": pend.get("last_sync"),
            "drive_pack": f"jtagent/devices/{name}/",
            "device_env": str(Path.home() / ".eka_agent" / "device.env"),
            "device_env_present": (Path.home() / ".eka_agent" / "device.env").is_file(),
        }
    dest = root / "devices" / "DEVICE_MAP.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(route, indent=2) + "\n", encoding="utf-8")
    return dest


def _push_drive(root: Path) -> dict:
    rclone = shutil.which("rclone")
    if not rclone:
        return {"ok": False, "skipped": "rclone missing"}
    conf = os.environ.get("RCLONE_CONFIG") or str(Path.home() / ".config" / "rclone" / "rclone.conf")
    rel_paths = ("jtagent/training", "jtagent/devices")
    results = []
    for rel in rel_paths:
        src = root / rel.replace("jtagent/", "")
        if rel == "jtagent/training":
            src = root / "training"
        if not src.exists():
            continue
        proc = subprocess.run(
            [
                rclone,
                "copy",
                str(src),
                f"{REMOTE}:{rel}",
                "--size-only",
                "--config",
                conf,
                "--drive-root-folder-id",
                FOLDER_ID,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        results.append({"path": rel, "rc": proc.returncode, "err": (proc.stderr or "")[:120]})
    ok = all(r["rc"] == 0 for r in results) if results else False
    return {"ok": ok, "copies": results}


def deploy(root: Path | None = None, *, push_hub: bool = True) -> dict:
    root = Path(root or DEFAULT_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "agent": "jtagent",
        "hub": HUB,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
    }

    code, health = _http_json(f"{HUB}/healthz")
    report["hub_healthz"] = {"http": code, "ok": isinstance(health, dict) and health.get("ok")}

    code, status = _http_json(f"{HUB}/status")
    report["hub_status"] = status if isinstance(status, dict) else {"http": code}

    code, agent_h = _http_json(f"{HUB}/agent/health")
    report["agent_health"] = agent_h if isinstance(agent_h, dict) else {"http": code}

    code, ret_h = _http_json(f"{HUB}/retrieve/healthz")
    report["jayti_retrieve"] = {"http": code, "ok": code == 200}

    _ensure_device_env()
    adapters = _list_drive_adapters()
    report["drive_adapters"] = adapters

    report["segment"] = _segment(root)
    report["pack"] = _pack(root, adapters)

    ingested = _ingested_local()
    pending = {d: _pending_push(d) for d in DEVICES}
    map_path = _write_device_map(root, ingested, pending)
    report["device_map"] = str(map_path)

    report["drive_push"] = _push_drive(root)

    if push_hub and (Path.home() / ".eka_agent" / "device.env").is_file():
        report["hub_push"] = _hub_push("windows_pc_abcom")
    else:
        report["hub_push"] = {
            "ok": False,
            "skipped": "device.env missing — run eka_register_device.sh on VPS, paste key here",
        }

    devices_report = {}
    for name in DEVICES:
        ing = ingested.get(name, {})
        pend = pending.get(name, {})
        devices_report[name] = {
            "drive_pack": f"jtagent/devices/{name}/",
            "ingested_rows": ing.get("rows", 0),
            "ingested_by_type": ing.get("by_type", {}),
            "pending_collect": pend.get("pending"),
            "pending_by_type": pend.get("by_type", {}),
            "last_sync": pend.get("last_sync"),
            "collect_on_this_host": name == _host_device(),
            "device_env_present": (Path.home() / ".eka_agent" / "device.env").is_file(),
            "skipped": pend.get("skipped"),
        }
    report["devices"] = devices_report

    report["ok"] = bool(
        report["hub_healthz"]["ok"]
        and report["segment"].get("total_chunks", 0) > 0
        and report["drive_push"].get("ok")
    )
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Deploy jtagent sandbox agent on this host")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--no-push-hub", action="store_true")
    args = parser.parse_args()
    root = Path(args.root)
    report = deploy(root, push_hub=not args.no_push_hub)
    out = root / "DEPLOY_REPORT.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print("DEPLOY_AGENT_DONE", out)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
