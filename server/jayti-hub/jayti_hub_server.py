"""
jayti_hub_server.py — FastAPI hub-of-record server.

Implements the device-push contract against the REAL schema in
eka_agent DB on Vultr (`jayti-agent-db`, 139.84.165.81).

Tables (matching production schema):
  - device_registry(device_id PK, device_name, device_type, os,
                    location, agent_version, last_seen, is_active,
                    apps jsonb, credentials jsonb, created_at, updated_at)
  - ingestion_queue(id PK, device FK, source, data_type, content jsonb,
                   content_hash varchar(64) UNIQUE, device_time,
                   ingested_at default now(), status default 'new',
                   processed_at, priority, hidden_data)
  - mobility_map(email PK, owner, email_type, devices jsonb,
                 created_at, updated_at)
  - audit_trail(log_id PK, timestamp, device, action_type, action_detail,
                input_ref, output_ref, data_affected jsonb,
                priority, correlation_ids jsonb, integrity_hash,
                duration_ms, status, error)
  - correlations(correlation_id PK, type, devices jsonb, evidence jsonb,
                 relationship, confidence, timestamp_correlation, created_at)
  - training_status(batch_id PK, batch_date, chunks_created, duplicates,
                   p0_found, p1_found, lora_adapter, train_status,
                   trained_at, created_at)
  - api_keys(key_id PK, device_id FK, key_hash, key_prefix, issued_at,
             expires_at, revoked_at, last_used_at)

Endpoints:
  POST /ingest          - device pushes one item (or batch)
  GET  /status          - hub liveness + counts
  GET  /pull            - Main-PC pull (returns items since cursor)
  POST /pull/mark       - Main-PC acks a batch
  POST /register_device - admin: register a new device + key
  GET  /healthz         - liveness probe (no auth)

Auth: device endpoints accept either:
  - X-Device-Id + X-Api-Key header (per-device key, see api_keys table)
  - Authorization: Bearer <shared-bootstrap-key>  (week 1 only)

Run with:
  uvicorn jayti_hub_server:app --host 0.0.0.0 --port 8443 \
      --ssl-keyfile /etc/jayti/tls/key.pem \
      --ssl-certfile /etc/jayti/tls/cert.pem

DB connection via env: JAYTI_PG_DSN (e.g. postgresql://jayti_writer:PW@127.0.0.1:5432/eka_agent)
"""

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional, Union

import asyncpg
from argon2 import PasswordHasher
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

DSN = os.environ.get("JAYTI_PG_DSN", "postgresql://jayti_writer@127.0.0.1:5432/eka_agent")
BOOTSTRAP_KEY = os.environ.get("JAYTI_BOOTSTRAP_KEY")  # week-1 shared key, optional

ph = PasswordHasher()

app = FastAPI(title="Jayti Hub", version="0.2.0")
_pool: Optional[asyncpg.Pool] = None


# Tables/columns the hub writes. Best-effort: missing audit_trail used to abort
# register/ingest transactions even when HTTP returned 200.
# One execute() per statement: Postgres runs a multi-statement query as one
# implicit transaction, so any error rolls back the whole batch. The hub's role
# doesn't own tables that postgres created, so the first ALTER failed and took
# the CREATEs down with it, leaving audit_trail missing. Run on their own, each
# statement commits by itself and a failed ALTER can't undo a CREATE.
_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS device_registry (
    device_id VARCHAR(50) PRIMARY KEY, device_name VARCHAR(100) NOT NULL,
    device_type VARCHAR(30) NOT NULL, os VARCHAR(30) NOT NULL, location VARCHAR(100),
    agent_version VARCHAR(50), last_seen TIMESTAMPTZ, is_active BOOLEAN NOT NULL DEFAULT true,
    apps JSONB NOT NULL DEFAULT '[]'::jsonb, credentials JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS ingestion_queue (
    id BIGSERIAL PRIMARY KEY, device VARCHAR(50) NOT NULL, source VARCHAR(200) NOT NULL,
    data_type VARCHAR(50) NOT NULL, content JSONB NOT NULL, content_hash VARCHAR(64) NOT NULL UNIQUE,
    device_time TIMESTAMPTZ NOT NULL, ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status VARCHAR(20) NOT NULL DEFAULT 'new', processed_at TIMESTAMPTZ,
    priority VARCHAR(5) DEFAULT NULL, hidden_data JSONB DEFAULT NULL)""",
    """CREATE TABLE IF NOT EXISTS audit_trail (
    log_id BIGSERIAL PRIMARY KEY, timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    agent_session VARCHAR(100), device VARCHAR(50), action_type VARCHAR(30) NOT NULL,
    action_detail TEXT, input_ref TEXT, output_ref TEXT, data_affected JSONB, priority VARCHAR(5),
    correlation_ids JSONB, integrity_hash VARCHAR(64), duration_ms INTEGER,
    status VARCHAR(15) NOT NULL, error TEXT)""",
    """CREATE TABLE IF NOT EXISTS api_keys (
    key_id UUID PRIMARY KEY, device_id VARCHAR(50) NOT NULL REFERENCES device_registry(device_id),
    key_hash TEXT NOT NULL, key_prefix VARCHAR(16),
    issued_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ, last_used_at TIMESTAMPTZ)""",
    "ALTER TABLE device_registry ADD COLUMN IF NOT EXISTS apps JSONB NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE device_registry ADD COLUMN IF NOT EXISTS credentials JSONB NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE device_registry ADD COLUMN IF NOT EXISTS agent_version VARCHAR(50)",
    "ALTER TABLE device_registry ADD COLUMN IF NOT EXISTS location VARCHAR(100)",
    "ALTER TABLE device_registry ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ",
    "ALTER TABLE device_registry ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true",
    "ALTER TABLE device_registry ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
    "ALTER TABLE device_registry ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
    "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS key_prefix VARCHAR(16)",
    "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS issued_at TIMESTAMPTZ NOT NULL DEFAULT now()",
    "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ",
    "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ",
    "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ",
)


async def _ensure_schema(pool: asyncpg.Pool) -> None:
    """Create hub tables/columns if a reinstall left them missing. Never raise."""
    # flush=True: stdout is block-buffered under systemd, so an unflushed line
    # would sit unwritten until the worker exits.
    failed = []
    try:
        async with pool.acquire() as conn:
            # An ALTER on a busy table waits for its ACCESS EXCLUSIVE lock and
            # queues live traffic behind it; cap the wait so all 13 together stay
            # under the old single 30s timeout. Pool release runs RESET ALL, so
            # the setting doesn't leak to other requests.
            await conn.execute("SET lock_timeout = '2s'")
            for stmt in _SCHEMA_STATEMENTS:
                try:
                    await conn.execute(stmt)
                except Exception as e:
                    failed.append(e)
    except Exception as e:
        print(f"schema repair: connection error: {type(e).__name__}: {e}", flush=True)
    if failed:
        print(f"schema repair: {len(failed)} of {len(_SCHEMA_STATEMENTS)} statements failed; "
              f"first: {type(failed[0]).__name__}: {failed[0]}", flush=True)


@app.on_event("startup")
async def _startup():
    global _pool
    _pool = await asyncpg.create_pool(DSN, min_size=1, max_size=8, command_timeout=30)
    await _ensure_schema(_pool)


@app.on_event("shutdown")
async def _shutdown():
    if _pool:
        await _pool.close()


# --- helpers -------------------------------------------------------------

def _utcnow():
    return datetime.now(timezone.utc)


def _parse_dt(value: Union[str, datetime, None]) -> Optional[datetime]:
    """Accept ISO-8601 strings or datetime objects; return None if missing/invalid."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            # Python 3.11+ accepts "Z" suffix; older needs explicit tz
            v = value.replace('Z', '+00:00')
            return datetime.fromisoformat(v)
        except Exception:
            return None
    return None


def _canonical_hash(device: str, data_type: str, payload) -> str:
    """SHA-256 hex of canonicalized (device, data_type, payload) — matches the
    existing `content_hash` column (varchar(64), all rows in production already
    populated by prior ingestion scripts)."""
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    h = hashlib.sha256()
    h.update(f"{device}|{data_type}|{canon}".encode("utf-8"))
    return h.hexdigest()


async def _auth(
    pool: asyncpg.Pool,
    x_device_id: Optional[str],
    x_api_key: Optional[str],
    authorization: Optional[str],
):
    """Returns actor string on success, raises 401 on failure."""
    if x_device_id and x_api_key:
        row = await pool.fetchrow(
            "SELECT device_id, key_hash FROM api_keys "
            "WHERE device_id = $1 AND revoked_at IS NULL "
            "ORDER BY issued_at DESC LIMIT 1",
            x_device_id,
        )
        if not row:
            raise HTTPException(401, "unknown or revoked device")
        try:
            ph.verify(row["key_hash"], x_api_key)
        except Exception:
            raise HTTPException(401, "bad api key")
        await pool.execute(
            "UPDATE api_keys SET last_used_at = NOW() WHERE device_id = $1", x_device_id
        )
        return x_device_id
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        if BOOTSTRAP_KEY and token == BOOTSTRAP_KEY:
            return "__bootstrap__"
    raise HTTPException(401, "missing credentials")


async def _audit(pool, actor: str, action: str, target: Optional[str], request: Request, details=None):
    """Append to audit_trail. The production audit_trail PK is log_id bigserial;
    column names follow the production schema.

    Never call this inside an open transaction: the error is swallowed, but a
    failed INSERT still aborts that transaction and its COMMIT rolls back.
    """
    try:
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        await pool.execute(
            "INSERT INTO audit_trail "
            "(timestamp, device, action_type, action_detail, status, integrity_hash) "
            "VALUES (NOW(), $1, $2, $3, 'ok', $4)",
            actor, action,
            json.dumps(details or {}, ensure_ascii=False),
            rid,
        )
    except Exception:
        # Audit must not block the request
        pass


# --- endpoints -----------------------------------------------------------

@app.get("/healthz")
async def healthz():
    return {"ok": True, "ts": _utcnow().isoformat()}


@app.get("/status")
async def status_endpoint():
    assert _pool is not None
    async with _pool.acquire() as conn:
        devices = await conn.fetchval("SELECT count(*) FROM device_registry WHERE is_active = TRUE")
        items = await conn.fetchval("SELECT count(*) FROM ingestion_queue")
        last24 = await conn.fetchval(
            "SELECT count(*) FROM ingestion_queue WHERE ingested_at > NOW() - interval '24 hours'"
        )
        unprocessed = await conn.fetchval(
            "SELECT count(*) FROM ingestion_queue WHERE status = 'new'"
        )
    return {
        "ok": True,
        "ts": _utcnow().isoformat(),
        "devices_active": devices,
        "items_total": items,
        "items_24h": last24,
        "items_unprocessed": unprocessed,
    }


@app.post("/ingest")
async def ingest(
    request: Request,
    x_device_id: Optional[str] = Header(None, alias="X-Device-Id"),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
    authorization: Optional[str] = Header(None),
):
    """Device pushes one item or a batch.

    Body shape (single):
      { "device": "<id>", "data_type": "<type>", "content": {...},
        "device_time": "ISO-8601", "source": "<source>", "run_id": "<optional>" }

    Body shape (batch):
      { "device": "<id>", "items": [<item>, ...] }
    """
    assert _pool is not None
    body = await request.json()

    async with _pool.acquire() as pool:
        actor = await _auth(pool, x_device_id, x_api_key, authorization)
        device = x_device_id or body.get("device")
        if not device:
            raise HTTPException(400, "device missing")

        # Verify device is registered
        exists = await pool.fetchval("SELECT 1 FROM device_registry WHERE device_id = $1", device)
        if not exists:
            raise HTTPException(400, f"unknown device: {device}")

        items = body.get("items") or [_strip_envelope(body)]
        if not isinstance(items, list):
            raise HTTPException(400, "items must be list")

        inserted = 0
        duplicate = 0
        for it in items:
            data_type = it.get("data_type") or it.get("type")
            content = it.get("content", it)
            device_time = it.get("device_time")
            source = it.get("source") or "device-push"
            if not data_type:
                continue
            ch = _canonical_hash(device, data_type, content)
            dt = _parse_dt(device_time)
            try:
                result = await pool.execute(
                    "INSERT INTO ingestion_queue "
                    "(device, source, data_type, content, content_hash, device_time, status) "
                    "VALUES ($1, $2, $3, $4::jsonb, $5, COALESCE($6::timestamptz, NOW()), 'new') "
                    "ON CONFLICT (content_hash) DO NOTHING",
                    device, source, data_type, json.dumps(content, ensure_ascii=False),
                    ch, dt,
                )
                # asyncpg returns "INSERT 0 1" or "INSERT 0 0"
                if result.endswith("1"):
                    inserted += 1
                else:
                    duplicate += 1
            except Exception as e:
                import traceback
                traceback.print_exc()
                raise HTTPException(500, f"insert failed: {type(e).__name__}: {e}")
        await _audit(pool, actor, "ingest", device, request,
                     {"inserted": inserted, "duplicate": duplicate, "items": len(items)})
    return JSONResponse(
        status_code=201,
        content={"ok": True, "inserted": inserted, "duplicate": duplicate, "device": device},
    )


def _strip_envelope(body: dict) -> dict:
    """Allow single-item POSTs that mirror the batch-item shape."""
    return {
        "data_type": body.get("data_type") or body.get("type"),
        "content": body.get("content", {k: v for k, v in body.items()
                                        if k not in ("device", "data_type", "type", "content",
                                                     "device_time", "source", "run_id")}),
        "device_time": body.get("device_time"),
        "source": body.get("source", "device-push"),
        "run_id": body.get("run_id"),
    }


@app.get("/pull")
async def pull(
    request: Request,
    since: Optional[int] = None,
    device: Optional[str] = None,
    limit: int = 100,
    status: Optional[str] = None,
    x_device_id: Optional[str] = Header(None, alias="X-Device-Id"),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
    authorization: Optional[str] = Header(None),
):
    """Main-PC pulls items since cursor (row-id). Status filter defaults to
    'new' (unprocessed) but can be overridden."""
    assert _pool is not None
    async with _pool.acquire() as pool:
        actor = await _auth(pool, x_device_id, x_api_key, authorization)
        q = ("SELECT id, device, source, data_type, content, content_hash, "
             "device_time, ingested_at, status FROM ingestion_queue WHERE TRUE")
        args = []
        if since:
            q += f" AND id > ${len(args)+1}"
            args.append(int(since))
        if device:
            q += f" AND device = ${len(args)+1}"
            args.append(device)
        if status:
            q += f" AND status = ${len(args)+1}"
            args.append(status)
        q += f" ORDER BY id ASC LIMIT ${len(args)+1}"
        args.append(min(max(limit, 1), 1000))
        rows = await pool.fetch(q, *args)
        await _audit(pool, actor, "pull", device or "*", request,
                     {"count": len(rows), "since": since})
    return {
        "ok": True,
        "items": [
            {
                "id": r["id"],
                "device": r["device"],
                "source": r["source"],
                "data_type": r["data_type"],
                "content": r["content"],
                "content_hash": r["content_hash"],
                "device_time": r["device_time"].isoformat() if r["device_time"] else None,
                "ingested_at": r["ingested_at"].isoformat() if r["ingested_at"] else None,
                "status": r["status"],
            }
            for r in rows
        ],
        "next_since": rows[-1]["id"] if rows else since,
    }


@app.post("/pull/mark")
async def pull_mark(
    request: Request,
    x_device_id: Optional[str] = Header(None, alias="X-Device-Id"),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
    authorization: Optional[str] = Header(None),
):
    """Main-PC acks a batch — marks rows up to `cursor` as processed."""
    assert _pool is not None
    body = await request.json()
    cursor = body.get("cursor")
    if not isinstance(cursor, int):
        raise HTTPException(400, "cursor must be integer row-id")
    async with _pool.acquire() as pool:
        actor = await _auth(pool, x_device_id, x_api_key, authorization)
        n = await pool.execute(
            "UPDATE ingestion_queue SET status = 'processed', processed_at = NOW() "
            "WHERE id <= $1 AND status = 'new'",
            cursor,
        )
        await _audit(pool, actor, "pull_mark", None, request, {"cursor": cursor, "rows": n})
    return {"ok": True, "cursor": cursor, "rows_marked": n}


@app.post("/register_device")
async def register_device(request: Request, authorization: Optional[str] = Header(None)):
    """Admin: register a new device + generate initial API key.

    Auth: only the bootstrap key can call this in week 1.
    Body: { "device_id": "...", "device_name": "...", "device_type": "...",
            "os": "...", "agent_version": "...", "location": "..." }
    Returns: { "device_id": "...", "api_key": "<one-time plaintext>" }
    """
    assert _pool is not None
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bootstrap key")
    if not BOOTSTRAP_KEY or authorization[7:] != BOOTSTRAP_KEY:
        raise HTTPException(401, "bad bootstrap key")

    body = await request.json()
    device_id = body.get("device_id")
    if not device_id:
        raise HTTPException(400, "device_id required")

    # Generate the device API key
    api_key = "jt_" + uuid.uuid4().hex + uuid.uuid4().hex[:8]
    prefix = api_key[:8]
    key_hash = ph.hash(api_key)

    async with _pool.acquire() as pool:
        async with pool.transaction():
            await pool.execute(
                "INSERT INTO device_registry "
                "(device_id, device_name, device_type, os, agent_version, location, is_active, apps, credentials) "
                "VALUES ($1, $2, $3, $4, $5, $6, TRUE, '[]'::jsonb, '[]'::jsonb) "
                "ON CONFLICT (device_id) DO UPDATE SET "
                "  device_name = EXCLUDED.device_name, "
                "  device_type = EXCLUDED.device_type, "
                "  os = EXCLUDED.os, "
                "  agent_version = EXCLUDED.agent_version, "
                "  location = EXCLUDED.location, "
                "  is_active = TRUE, "
                "  updated_at = NOW()",
                device_id,
                body.get("device_name", device_id),
                body.get("device_type", "phone"),
                body.get("os", "android"),
                body.get("agent_version", "unknown"),
                body.get("location", ""),
            )
            await pool.execute(
                "INSERT INTO api_keys (key_id, device_id, key_hash, key_prefix, issued_at) "
                "VALUES ($1, $2, $3, $4, NOW())",
                str(uuid.uuid4()), device_id, key_hash, prefix,
            )
        # Audit AFTER the registry/key txn commits. Never call _audit inside the
        # write transaction while it swallows errors: a failed audit INSERT
        # aborts the PG transaction, COMMIT then rolls back the device rows,
        # and this handler would still return the plaintext api_key.
        await _audit(pool, "__bootstrap__", "register_device", device_id, request,
                     {"prefix": prefix})

    return {"ok": True, "device_id": device_id, "api_key": api_key, "prefix": prefix}
