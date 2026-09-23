#!/usr/bin/env python3
"""Smoke Hub, retrieval, and the TAN Drive adapter path.

Uses EKA_DEVICE_ID / EKA_DEVICE_KEY from the environment or
/workspace/.config/jtagent-devices.env. Prints HTTP status and counts only.
Never prints the device key or document bodies.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

HUB = os.environ.get("EKA_VPS_URL", "https://agent.jaytipargal.tech").rstrip("/")
REMOTE = os.environ.get("JTAGENT_RCLONE_REMOTE", "jtagent_tan")
ENV_CANDIDATES = (
    Path("/workspace/.config/jtagent-devices.env"),
    Path.home() / ".config" / "jtagent-devices.env",
)


def _load_env() -> None:
    if os.environ.get("EKA_DEVICE_ID") and os.environ.get("EKA_DEVICE_KEY"):
        return
    for path in ENV_CANDIDATES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
        return


def _request(method: str, url: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict | str]:
    data = None if body is None else json.dumps(body).encode()
    hdrs = {"Accept": "application/json"}
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        code = exc.code
    try:
        return code, json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return code, raw[:180]


def _device_headers() -> dict[str, str]:
    return {
        "X-Device-Id": os.environ.get("EKA_DEVICE_ID", ""),
        "X-Api-Key": os.environ.get("EKA_DEVICE_KEY", ""),
    }


def smoke() -> dict:
    _load_env()
    report: dict = {"hub": HUB, "device_id": os.environ.get("EKA_DEVICE_ID", "")}
    code, body = _request("GET", f"{HUB}/healthz")
    report["hub_healthz"] = {"http": code, "ok": isinstance(body, dict) and body.get("ok") is True}
    code, body = _request("GET", f"{HUB}/status")
    if isinstance(body, dict):
        report["hub_status"] = {
            "http": code,
            "items_total": body.get("items_total"),
            "items_unprocessed": body.get("items_unprocessed"),
            "devices_active": body.get("devices_active"),
        }
    else:
        report["hub_status"] = {"http": code}

    code, body = _request("GET", f"{HUB}/retrieval/health")
    report["retrieval_health"] = {
        "http": code,
        "ok": isinstance(body, dict) and body.get("status") == "ok",
        "doc_count": body.get("doc_count") if isinstance(body, dict) else None,
    }
    code, body = _request("GET", f"{HUB}/retrieve/healthz")
    report["jayti_retrieve_healthz"] = {"http": code}

    headers = _device_headers()
    if headers["X-Device-Id"] and headers["X-Api-Key"]:
        code, body = _request(
            "POST",
            f"{HUB}/retrieve/retrieve",
            {"query": "jtagent sandbox", "k": 1},
            headers,
        )
        k = body.get("k") if isinstance(body, dict) else None
        report["retrieve"] = {"http": code, "k": k}
    else:
        report["retrieve"] = {"http": None, "skipped": "no device key"}

    rclone = shutil.which("rclone")
    if rclone:
        proc = subprocess.run(
            [rclone, "lsf", f"{REMOTE}:jtagent/adapters", "--dirs-only"],
            capture_output=True,
            text=True,
            check=False,
        )
        names = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        report["drive_adapters"] = {"rc": proc.returncode, "dirs": names[:20]}
    else:
        report["drive_adapters"] = {"skipped": "rclone missing"}

    drive_dirs = report.get("drive_adapters", {}).get("dirs") or []
    report["ok"] = bool(
        report["hub_healthz"]["ok"]
        and report["retrieval_health"]["ok"]
        and any("sandbox_ops" in name for name in drive_dirs)
    )
    return report


def main() -> int:
    report = smoke()
    print(json.dumps(report, indent=2))
    return 0 if report.get("hub_healthz", {}).get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
