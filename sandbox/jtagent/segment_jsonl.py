#!/usr/bin/env python3
"""Pull Hub / Postgres ingestion into per-category JSONL under training/{category}/.

Uses CATEGORY_MAP / PRIORITY_MAP from eka_agent_pull.py. Runs eka_redact +
eka_scan_secrets before writing train-ready files. Falls back to seed_chunks
ONLY when the queue is empty. Skips chrome sqlite / encryption key-derivation /
live WhatsApp DBs.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

# Maps mirrored from scripts/eka_agent_pull.py (keep in sync)
CATEGORY_MAP = {
    "whatsapp_chat": "whatsapp_chat",
    "call_recordings": "call_recordings",
    "sms_messages": "call_recordings",
    "call_log": "call_recordings",
    "browser_data": "browser_data",
    "chrome_history": "browser_data",
    "file_change": "extracted_text",
    "drive_backup": "extracted_text",
    "system_logs": "infrastructure",
    "daemon_logs": "infrastructure",
    "access_logs": "infrastructure",
    "analytics_data": "extracted_text",
    "site_content": "extracted_text",
    "app_install": "infrastructure",
}

PRIORITY_MAP = {
    "whatsapp_chat": "P1",
    "call_recordings": "P1",
    "sms_messages": "P1",
    "call_log": "P1",
    "browser_data": "P2",
    "chrome_history": "P2",
    "file_change": "P2",
    "drive_backup": "P2",
    "system_logs": "P3",
    "daemon_logs": "P3",
    "access_logs": "P3",
    "analytics_data": "P2",
    "site_content": "P2",
    "app_install": "P1",
}

SKIP_DATA_TYPES = {
    "chrome_sqlite",
    "chrome_login_data",
    "encryption_key",
    "key_derivation",
    "whatsapp_db",
    "whatsapp_msgstore",
}
SKIP_SOURCE_FRAGMENTS = (
    "chrome-browser-data",
    "key-derivation",
    "msgstore.db",
    "Login Data",
    "wa.db",
)

DEFAULT_ROOT = Path(os.environ.get("JTAGENT_ROOT", "/content/TAN/jtagent"))
HUB_URL = os.environ.get("EKA_VPS_URL", "https://agent.jaytipargal.tech").rstrip("/")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(content) -> str:
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, default=str).encode()
    ).hexdigest()


def _scripts_dir() -> Path:
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent.parent / "scripts",
        Path("/content/jayti/scripts"),
        Path(__file__).resolve().parents[2] / "scripts",
    ]
    for c in candidates:
        if (c / "eka_redact.py").exists():
            return c
    return candidates[0]


def _import_redact():
    scripts = _scripts_dir()
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import eka_redact  # type: ignore
    import eka_scan_secrets  # type: ignore

    return eka_redact, eka_scan_secrets


def _should_skip_item(item: dict) -> bool:
    data_type = str(item.get("data_type") or "")
    source = str(item.get("source") or "")
    if data_type in SKIP_DATA_TYPES:
        return True
    blob = f"{data_type} {source} {json.dumps(item.get('content', {}), default=str)[:500]}".lower()
    return any(frag.lower() in blob for frag in SKIP_SOURCE_FRAGMENTS)


def _item_to_chunk(item: dict) -> dict | None:
    if _should_skip_item(item):
        return None
    content = item.get("content", {})
    data_type = item.get("data_type", "unknown")
    device = item.get("device", "unknown")
    source = item.get("source", "unknown")
    content_hash = item.get("content_hash") or _hash(content)
    device_time = item.get("device_time") or _now_iso()
    category = CATEGORY_MAP.get(data_type, "extracted_text")
    priority = PRIORITY_MAP.get(data_type, "P2")
    title = (
        content.get("filename")
        or content.get("title")
        or str(content.get("message", ""))[:80]
        or f"{data_type} from {device}"
    )
    input_prompt = f"What {category.replace('_', ' ')} data was found on {device}?"
    if data_type == "whatsapp_chat":
        input_prompt = f"Show WhatsApp messages from {device}"
    elif data_type in ("call_recordings", "call_log", "sms_messages"):
        input_prompt = f"Show call recordings and logs from {device}"
    elif data_type in ("browser_data", "chrome_history"):
        input_prompt = f"Show browser history from {device}"
    elif data_type == "file_change":
        input_prompt = f"What new files were found on {device}?"

    output_data = json.dumps(
        {
            "content": content,
            "source_device": device,
            "source": source,
            "data_type": data_type,
            "collected_timestamp": device_time,
            "content_hash": content_hash,
            "agent_visibility": "full",
        },
        ensure_ascii=False,
        default=str,
    )
    return {
        "id": f"seg_{date.today().isoformat()}_{content_hash[:12]}",
        "title": title if isinstance(title, str) else str(title),
        "category": category,
        "input": input_prompt,
        "output": output_data,
        "source_file": source,
        "metadata": {
            "priority": priority,
            "source_device": device,
            "data_type": data_type,
            "content_hash": content_hash,
            "collected_timestamp": device_time,
        },
    }


def _resolve_dsn() -> str | None:
    for key in ("JAYTI_PG_DSN", "EKA_PG_DSN"):
        v = os.environ.get(key, "")
        if v and "YOUR_" not in v:
            return v
    local = Path(__file__).resolve().parent / ".local" / "jayti_pg.dsn"
    if local.is_file():
        text = local.read_text(encoding="utf-8").strip()
        if text:
            return text
    return None


def pull_from_hub() -> list[dict]:
    """GET /pull when device credentials are present; else empty."""
    device_id = os.environ.get("EKA_DEVICE_ID") or os.environ.get("X_DEVICE_ID")
    api_key = os.environ.get("EKA_DEVICE_KEY") or os.environ.get("EKA_API_KEY")
    if not device_id or not api_key:
        print("hub pull skipped: set EKA_DEVICE_ID + EKA_DEVICE_KEY for /pull")
        return []
    url = f"{HUB_URL}/pull?status=new&limit=1000"
    req = urllib.request.Request(
        url,
        headers={
            "X-Device-Id": device_id,
            "X-Api-Key": api_key,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = data.get("items") or []
        print(f"hub pull: {len(items)} items")
        return items
    except urllib.error.HTTPError as exc:
        print(f"hub pull HTTP {exc.code}")
        return []
    except Exception as exc:  # noqa: BLE001
        print(f"hub pull error: {type(exc).__name__}: {exc}")
        return []


def pull_from_postgres(dsn: str) -> list[dict]:
    try:
        import psycopg
    except ImportError:
        print("psycopg missing; cannot read ingestion_queue")
        return []
    items = []
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, device, source, data_type, content, content_hash, "
                "device_time, ingested_at, status FROM ingestion_queue "
                "WHERE status = 'new' ORDER BY id ASC LIMIT 10000"
            )
            for row in cur.fetchall():
                content = row[4]
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except json.JSONDecodeError:
                        content = {"raw": content}
                items.append(
                    {
                        "id": row[0],
                        "device": row[1],
                        "source": row[2],
                        "data_type": row[3],
                        "content": content,
                        "content_hash": row[5],
                        "device_time": row[6].isoformat() if row[6] else None,
                        "ingested_at": row[7].isoformat() if row[7] else None,
                        "status": row[8],
                    }
                )
    print(f"postgres pull: {len(items)} items")
    return items


def redact_chunks(chunks: list[dict]) -> list[dict]:
    eka_redact, eka_scan_secrets = _import_redact()
    out = []
    hits_total = 0
    for chunk in chunks:
        redacted, n = eka_redact.redact_obj(chunk)
        hits_total += n
        # rescan critical string fields
        for field in ("input", "output", "title"):
            val = redacted.get(field)
            if isinstance(val, str) and eka_scan_secrets.scan_secrets(val):
                redacted[field], _ = eka_redact.redact_text(val)
        out.append(redacted)
    print(f"redacted {hits_total} secret hits across {len(chunks)} chunks")
    return out


def write_category_jsonl(root: Path, chunks: list[dict]) -> dict:
    by_cat: dict[str, list] = {}
    for c in chunks:
        by_cat.setdefault(c.get("category") or "extracted_text", []).append(c)
    training = root / "training"
    training.mkdir(parents=True, exist_ok=True)
    # pad lines for eka_train.load_training_data which skips first 10
    pad = [{"input": f"pad-{i}", "output": "ok", "category": "pad"} for i in range(10)]
    written = {}
    for cat, rows in sorted(by_cat.items()):
        dest = training / cat / f"{date.today().isoformat()}.jsonl"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w", encoding="utf-8") as fh:
            for row in pad + rows:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        written[cat] = {"path": str(dest), "chunks": len(rows)}
        print(f"wrote {len(rows)} → {dest}")
    manifest = {
        "date": date.today().isoformat(),
        "categories": written,
        "total_chunks": sum(v["chunks"] for v in written.values()),
        "fallback_seed": False,
    }
    (training / "SEGMENT_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def seed_fallback(root: Path) -> dict:
    sandbox = Path(__file__).resolve().parent
    if str(sandbox) not in sys.path:
        sys.path.insert(0, str(sandbox))
    from pack_devices import seed_chunks  # type: ignore

    chunks = redact_chunks(seed_chunks())
    manifest = write_category_jsonl(root, chunks)
    manifest["fallback_seed"] = True
    (root / "training" / "SEGMENT_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print("FALLBACK_SEED used (queue empty)")
    return manifest


def run(root: Path | None = None) -> dict:
    root = Path(root or DEFAULT_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    items = pull_from_hub()
    if not items:
        dsn = _resolve_dsn()
        if dsn:
            items = pull_from_postgres(dsn)
    chunks = []
    skipped = 0
    for item in items:
        chunk = _item_to_chunk(item)
        if chunk is None:
            skipped += 1
            continue
        chunks.append(chunk)
    print(f"chunks={len(chunks)} skipped={skipped}")
    if not chunks:
        return seed_fallback(root)
    chunks = redact_chunks(chunks)
    return write_category_jsonl(root, chunks)


def main() -> int:
    root = Path(os.environ.get("JTAGENT_ROOT", str(DEFAULT_ROOT)))
    if len(sys.argv) > 1:
        root = Path(sys.argv[1])
    manifest = run(root)
    print(json.dumps(manifest, indent=2))
    print("SEGMENT_JSONL_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
