#!/usr/bin/env python3
"""Push realistic sandbox ingest rows to Jayti Hub (jtagent_sandbox device).

Loads credentials from the environment or /workspace/.config/jtagent-devices.env.
Builds sample whatsapp_chat, browser_data, and system_logs items, redacts with
eka_redact / eka_scan_secrets, then POST /ingest. Prints counts only — never keys.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HUB_URL = os.environ.get("EKA_VPS_URL", "https://agent.jaytipargal.tech").rstrip("/")
ENV_CANDIDATES = (
    Path("/workspace/.config/jtagent-devices.env"),
    Path.home() / ".config" / "jtagent-devices.env",
)
MIN_PER_TYPE = 3


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


def _scripts_dir() -> Path:
    here = Path(__file__).resolve().parent
    for c in (here.parent.parent / "scripts", Path("/content/jayti/scripts")):
        if (c / "eka_redact.py").exists():
            return c
    return here.parent.parent / "scripts"


def _import_redact():
    scripts = _scripts_dir()
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import eka_redact  # type: ignore
    import eka_scan_secrets  # type: ignore

    return eka_redact, eka_scan_secrets


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(content: dict) -> str:
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, default=str).encode()
    ).hexdigest()


def _raw_samples(run_tag: str) -> list[dict]:
    """Synthetic rows with fake PII/secrets — redacted before push."""
    ts = _now_iso()
    return [
        # whatsapp_chat (→ category whatsapp_chat)
        {
            "data_type": "whatsapp_chat",
            "source": "sandbox-sim-whatsapp",
            "content": {
                "type": "whatsapp_message",
                "text": f"Meeting at 3pm — contact +91-9876543210 run={run_tag}",
                "from_me": 0,
                "sender_jid": "friend@s.whatsapp.net",
                "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            },
        },
        {
            "data_type": "whatsapp_chat",
            "source": "sandbox-sim-whatsapp",
            "content": {
                "type": "whatsapp_message",
                "text": f"Drive link shared run={run_tag}",
                "from_me": 1,
                "key_id": f"ABC{run_tag[:8]}",
                "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000) + 1,
            },
        },
        {
            "data_type": "whatsapp_chat",
            "source": "sandbox-sim-whatsapp",
            "content": {
                "type": "whatsapp_message",
                "text": f"OTP was sk-proj-FAKEKEY0123456789abcdef run={run_tag}",
                "from_me": 0,
                "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000) + 2,
            },
        },
        # browser_data (→ category browser_data)
        {
            "data_type": "browser_data",
            "source": "sandbox-sim-chrome",
            "content": {
                "type": "chrome_history",
                "url": f"https://example.com/docs?q={run_tag}",
                "title": "Jayti sandbox docs",
                "visit_count": 3,
            },
        },
        {
            "data_type": "browser_data",
            "source": "sandbox-sim-chrome",
            "content": {
                "type": "chrome_history",
                "url": "https://github.com/go4garage01/jt-agent-model",
                "title": "HF model repo",
                "visit_count": 1,
            },
        },
        {
            "data_type": "browser_data",
            "source": "sandbox-sim-chrome",
            "content": {
                "type": "chrome_history",
                "url": f"https://agent.jaytipargal.tech/status#{run_tag}",
                "title": "Hub status",
                "visit_count": 2,
            },
        },
        # system_logs (→ category infrastructure)
        {
            "data_type": "system_logs",
            "source": "sandbox-sim-syslog",
            "content": {
                "type": "log_change",
                "path": "/var/log/syslog",
                "filename": "syslog",
                "modified": ts,
                "snippet": f"systemd[1]: eka-agent push ok run={run_tag}",
            },
        },
        {
            "data_type": "system_logs",
            "source": "sandbox-sim-syslog",
            "content": {
                "type": "log_change",
                "path": "/var/log/auth.log",
                "filename": "auth.log",
                "modified": ts,
                "snippet": "Accepted publickey for ubuntu from 10.0.0.5",
            },
        },
        {
            "data_type": "system_logs",
            "source": "sandbox-sim-syslog",
            "content": {
                "type": "log_change",
                "path": "/var/log/kern.log",
                "filename": "kern.log",
                "modified": ts,
                "snippet": f"wlan0: associated run={run_tag}",
            },
        },
    ]


def _redact_item(item: dict, eka_redact, eka_scan_secrets) -> tuple[dict, int]:
    content = item["content"]
    redacted, n = eka_redact.redact_obj(content)
    blob = json.dumps(redacted, default=str)
    if eka_scan_secrets.scan_secrets(blob):
        redacted, extra = eka_redact.redact_obj(redacted)
        n += extra
    out = dict(item)
    out["content"] = redacted
    out["device_time"] = _now_iso()
    out["content_hash"] = _content_hash(redacted)
    return out, n


def build_items(run_tag: str | None = None) -> tuple[list[dict], dict]:
    run_tag = run_tag or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    eka_redact, eka_scan_secrets = _import_redact()
    raw = _raw_samples(run_tag)
    items = []
    hits = 0
    for row in raw:
        red, n = _redact_item(row, eka_redact, eka_scan_secrets)
        hits += n
        items.append(red)
    by_type: dict[str, int] = {}
    for it in items:
        by_type[it["data_type"]] = by_type.get(it["data_type"], 0) + 1
    assert all(by_type.get(t, 0) >= MIN_PER_TYPE for t in ("whatsapp_chat", "browser_data", "system_logs"))
    return items, {"run_tag": run_tag, "redaction_hits": hits, "by_data_type": by_type}


def push_items(device_id: str, api_key: str, items: list[dict]) -> dict:
    payload = {"device": device_id, "items": items}
    req = urllib.request.Request(
        f"{HUB_URL}/ingest",
        data=json.dumps(payload, default=str).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Device-Id": device_id,
            "X-Api-Key": api_key,
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            body["http"] = resp.status
            return body
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"error": raw[:200]}
        body["http"] = exc.code
        return body


def main() -> int:
    _load_env()
    device_id = os.environ.get("EKA_DEVICE_ID", "")
    api_key = os.environ.get("EKA_DEVICE_KEY", "")
    if not device_id or not api_key:
        print("missing EKA_DEVICE_ID or EKA_DEVICE_KEY", file=sys.stderr)
        return 1

    items, meta = build_items()
    result = push_items(device_id, api_key, items)
    report = {
        "device_id": device_id,
        "hub": HUB_URL,
        "prepared": meta,
        "ingest": {
            "http": result.get("http"),
            "inserted": result.get("inserted"),
            "duplicate": result.get("duplicate"),
            "ok": result.get("ok"),
        },
    }
    if result.get("error"):
        report["ingest"]["error"] = str(result.get("error"))[:120]
    print(json.dumps(report, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
