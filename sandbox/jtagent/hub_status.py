#!/usr/bin/env python3
"""Read-only jtagent Hub / Postgres status.

Prints ingestion_queue counts by device / data_type / status and latest
training_status. Prefers JAYTI_PG_DSN (or sandbox/.local/jayti_pg.dsn).
Falls back to public Hub /status + /healthz when DSN is unavailable.
Never prints connection strings or API keys.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HUB_URL = os.environ.get("EKA_VPS_URL", "https://agent.jaytipargal.tech").rstrip("/")
HUB_HTTP_FALLBACK = os.environ.get("EKA_VPS_HTTP", "http://139.84.165.81").rstrip("/")
SANDBOX_DEVICES = ("samsung_s24_ultra", "asus_vivobook", "windows_pc_abcom")
DSN_CANDIDATES = (
    os.environ.get("JAYTI_PG_DSN", ""),
    os.environ.get("EKA_PG_DSN", ""),
)


def _resolve_dsn() -> str | None:
    for dsn in DSN_CANDIDATES:
        if dsn and "YOUR_" not in dsn:
            return dsn
    local = Path(__file__).resolve().parent / ".local" / "jayti_pg.dsn"
    if local.is_file():
        text = local.read_text(encoding="utf-8").strip()
        if text and "YOUR_" not in text:
            return text
    return None


def _http_json(url: str, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def hub_public_status() -> dict:
    # Canonical Hub is HTTPS. HTTP :80 stays as fallback (no force-redirect).
    bases = []
    for u in (HUB_URL, HUB_HTTP_FALLBACK):
        if u and u not in bases:
            bases.append(u)
    out: dict = {"hub_url": bases[0] if bases else HUB_URL, "hub_url_tried": bases}
    primary = bases[0] if bases else HUB_URL
    extra = [b for b in bases[1:]]
    for path in ("/healthz", "/status", "/agent/health", "/retrieval/health"):
        last_err = None
        order = [primary] + extra if path in ("/healthz", "/status") else [HUB_URL] + [
            b for b in bases if b != HUB_URL
        ]
        for base in order:
            try:
                out[path] = _http_json(f"{base}{path}", timeout=6.0)
                if base != primary:
                    out[f"{path}_via"] = base
                last_err = None
                break
            except Exception as exc:  # noqa: BLE001 — status tool must not crash
                last_err = exc
                continue
        if last_err is not None and path not in out:
            out[path] = {"error": f"{type(last_err).__name__}: {last_err}"}
    return out


def postgres_status(dsn: str) -> dict:
    try:
        import psycopg
    except ImportError as exc:
        return {"error": f"psycopg not installed: {exc}"}

    report: dict = {"source": "postgres", "dsn_configured": True}
    try:
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT device_id, device_name, device_type, os, is_active, last_seen "
                    "FROM device_registry ORDER BY device_id"
                )
                cols = [d.name for d in cur.description]
                devices = [dict(zip(cols, row)) for row in cur.fetchall()]
                for d in devices:
                    if d.get("last_seen") is not None:
                        d["last_seen"] = d["last_seen"].isoformat()
                report["device_registry"] = devices
                report["sandbox_devices_present"] = {
                    name: any(d["device_id"] == name for d in devices)
                    for name in SANDBOX_DEVICES
                }

                cur.execute(
                    "SELECT device, data_type, status, count(*) AS n "
                    "FROM ingestion_queue "
                    "GROUP BY device, data_type, status "
                    "ORDER BY device, data_type, status"
                )
                report["ingestion_queue_counts"] = [
                    {
                        "device": r[0],
                        "data_type": r[1],
                        "status": r[2],
                        "count": int(r[3]),
                    }
                    for r in cur.fetchall()
                ]
                cur.execute("SELECT count(*) FROM ingestion_queue")
                report["ingestion_queue_total"] = int(cur.fetchone()[0])

                cur.execute(
                    "SELECT batch_id, batch_date, chunks_created, duplicates, "
                    "p0_found, p1_found, lora_adapter, train_status, trained_at "
                    "FROM training_status ORDER BY created_at DESC LIMIT 5"
                )
                tcols = [d.name for d in cur.description]
                latest = []
                for row in cur.fetchall():
                    item = dict(zip(tcols, row))
                    for k in ("batch_date", "trained_at"):
                        if item.get(k) is not None:
                            item[k] = item[k].isoformat()
                    latest.append(item)
                report["training_status_latest"] = latest
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
    return report


def main() -> int:
    report: dict = {
        "agent": "jtagent",
        "note": "Read-only. Secrets never printed. Live VPS preferred; local/Neon DSN is sandbox fallback.",
    }
    report["hub"] = hub_public_status()
    dsn = _resolve_dsn()
    if dsn:
        report["postgres"] = postgres_status(dsn)
    else:
        report["postgres"] = {
            "dsn_configured": False,
            "hint": (
                "Set JAYTI_PG_DSN or place DSN in sandbox/jtagent/.local/jayti_pg.dsn "
                "(gitignored). Live Hub /status above still works without DSN."
            ),
        }
    print(json.dumps(report, indent=2, default=str))
    hub_ok = bool((report.get("hub") or {}).get("/healthz", {}).get("ok"))
    pg = report.get("postgres") or {}
    devices_ok = all((pg.get("sandbox_devices_present") or {}).values()) if pg.get("dsn_configured") else hub_ok
    return 0 if hub_ok or devices_ok else 1


if __name__ == "__main__":
    sys.exit(main())
