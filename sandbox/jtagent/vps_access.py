#!/usr/bin/env python3
"""Vultr + SSH helpers for jayti-agent-db (139.84.165.81).

Reads VULTR_API_KEY from the environment. Never prints keys or secrets.
Uses ~/.ssh/claude_key for SSH when present (Vultr SSH key name: claude key).
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

VPS_IP = "139.84.165.81"
VPS_HOST = "jayti-agent-db"
SSH_KEY = Path.home() / ".ssh" / "claude_key"
RECOVERY_SCRIPT = Path(__file__).resolve().parent / "vps_retrieval_recovery.sh"


def _vultr_request(path: str) -> tuple[int, dict | str]:
    key = os.environ.get("VULTR_API_KEY", "").strip()
    if not key:
        return 0, {"error": "VULTR_API_KEY not set"}
    req = urllib.request.Request(
        f"https://api.vultr.com/v2{path}",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = raw[:200]
        return exc.code, body


def vultr_status() -> dict:
    out: dict = {"vps_ip": VPS_IP}
    code, body = _vultr_request("/account")
    out["account"] = {"http": code, "ok": code == 200}
    if isinstance(body, dict) and body.get("account"):
        out["account"]["email_present"] = bool(body["account"].get("email"))
    elif isinstance(body, dict) and body.get("error"):
        out["account"]["error"] = body["error"]

    code, body = _vultr_request("/ssh-keys")
    keys = []
    if isinstance(body, dict):
        for item in body.get("ssh_keys") or []:
            keys.append({"name": item.get("name"), "id": item.get("id")})
    out["ssh_keys"] = {"http": code, "names": [k["name"] for k in keys], "has_claude_key": any(k["name"] == "claude key" for k in keys)}

    code, body = _vultr_request("/instances")
    inst = []
    if isinstance(body, dict):
        for item in body.get("instances") or []:
            inst.append(
                {
                    "id": item.get("id"),
                    "label": item.get("label"),
                    "main_ip": item.get("main_ip"),
                    "status": item.get("status"),
                }
            )
    out["instances"] = {"http": code, "items": inst}
    return out


def _key_kind(path: Path) -> str:
    if not path.is_file():
        return "missing"
    head = path.read_text(encoding="utf-8", errors="replace").lstrip()[:40]
    if head.startswith("-----BEGIN"):
        return "private"
    if "ssh-" in head or "ecdsa-" in head:
        return "public"
    return "unknown"


def ssh_probe() -> dict:
    ssh = shutil.which("ssh")
    if not ssh:
        return {"ok": False, "error": "ssh not found"}
    kind = _key_kind(SSH_KEY)
    if kind == "missing":
        return {
            "ok": False,
            "error": f"missing private key: {SSH_KEY}",
            "hint": "Save the Vultr SSH key 'claude key' private file to ~/.ssh/claude_key (mode 600)",
        }
    if kind == "public":
        return {
            "ok": False,
            "error": f"{SSH_KEY} is a public key, not a private key",
            "hint": (
                "Vultr shows/downloads the public half only. You need the private key "
                "that was created when 'claude key' was generated (starts with "
                "-----BEGIN OPENSSH PRIVATE KEY-----). None of the local private keys "
                "match this public key."
            ),
        }
    if kind != "private":
        return {"ok": False, "error": f"unrecognized key format at {SSH_KEY}"}
    cmd = [
        ssh,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=12",
        "-i",
        str(SSH_KEY),
        f"root@{VPS_IP}",
        "echo SSH_OK; hostname; systemctl is-active jayti-retrieval jayti-hub",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return {
        "ok": proc.returncode == 0,
        "rc": proc.returncode,
        "stdout": proc.stdout.strip()[:300],
        "stderr": proc.stderr.strip()[:200],
    }


def run_recovery() -> dict:
    probe = ssh_probe()
    if not probe.get("ok"):
        return {"ok": False, "ssh": probe}
    if not RECOVERY_SCRIPT.is_file():
        return {"ok": False, "error": f"missing {RECOVERY_SCRIPT}"}
    script = RECOVERY_SCRIPT.read_text(encoding="utf-8")
    ssh = shutil.which("ssh")
    cmd = [ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", "-i", str(SSH_KEY), f"root@{VPS_IP}", "bash -s"]
    proc = subprocess.run(cmd, input=script, capture_output=True, text=True, check=False, timeout=300)
    return {
        "ok": proc.returncode == 0,
        "rc": proc.returncode,
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-15:]),
        "stderr_tail": proc.stderr.strip()[:200],
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Vultr/SSH access for jayti VPS")
    parser.add_argument("--probe", action="store_true", help="SSH probe only")
    parser.add_argument("--recover", action="store_true", help="Run retrieval recovery over SSH")
    args = parser.parse_args()

    report: dict = {"ssh_key_path": str(SSH_KEY), "ssh_key_present": SSH_KEY.is_file()}
    report["vultr"] = vultr_status()
    report["ssh"] = ssh_probe()
    if args.recover:
        report["recovery"] = run_recovery()
        code, health = 0, ""
        try:
            with urllib.request.urlopen(
                f"https://agent.jaytipargal.tech/retrieve/healthz", timeout=20
            ) as resp:
                health = str(resp.status)
        except urllib.error.HTTPError as exc:
            health = str(exc.code)
        report["retrieve_healthz_after"] = health
    print(json.dumps(report, indent=2))
    ok = report["ssh"].get("ok") or report["vultr"]["account"].get("ok")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
