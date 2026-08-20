#!/usr/bin/env python3
"""
EKA Agent — Central Ingestion API Server
Runs on Vultr VPS port 8443

Endpoints:
  POST /ingest        — devices push data
  GET  /pull          — main PC pulls new data
  POST /pull/mark     — mark items as processed
  GET  /status        — check device health
  POST /register      — register/update device
  POST /audit         — log agent action
  GET  /audit/query   — query audit trail
  POST /correlation   — store correlation
  GET  /training/status — check training batch
  POST /training/status — update training batch
  GET  /health         — health check
"""
import os
import json
import hashlib
from datetime import datetime, timezone
from typing import Optional, List, Any

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from fastapi import FastAPI, HTTPException, Header, Request
from pydantic import BaseModel, Field

# ─── Config ───
DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "eka_agent"
DB_USER = "eka_agent"
DB_PASS = "YOUR_DB_PASSWORD_HERE"
API_KEY = "YOUR_API_KEY_HERE"

app = FastAPI(title="EKA Agent Central API", version="1.0.0")


# ─── DB Connection ───
def get_db():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS,
        cursor_factory=RealDictCursor
    )


# ─── Auth ───
def verify_api_key(x_api_key: Optional[str] = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


# ─── Models ───
class IngestItem(BaseModel):
    device: str
    source: str
    data_type: str
    content: dict
    device_time: str  # ISO 8601
    content_hash: Optional[str] = None
    priority: Optional[str] = None
    hidden_data: Optional[dict] = None

class IngestBatch(BaseModel):
    items: List[IngestItem]

class MarkProcessed(BaseModel):
    ids: List[int]

class DeviceRegister(BaseModel):
    device_id: str
    device_name: str
    device_type: str
    os: str
    location: Optional[str] = None
    agent_version: Optional[str] = None
    apps: Optional[List[dict]] = []
    credentials: Optional[List[dict]] = []

class AuditEntry(BaseModel):
    agent_session: Optional[str] = None
    device: Optional[str] = None
    action_type: str
    action_detail: Optional[str] = None
    input_ref: Optional[str] = None
    output_ref: Optional[str] = None
    data_affected: Optional[Any] = None
    priority: Optional[str] = None
    correlation_ids: Optional[Any] = None
    integrity_hash: Optional[str] = None
    duration_ms: Optional[int] = None
    status: str
    error: Optional[str] = None

class CorrelationEntry(BaseModel):
    correlation_id: str
    type: str
    devices: List[str]
    evidence: List[dict]
    relationship: str
    confidence: str
    timestamp_correlation: Optional[str] = None

class TrainingStatusUpdate(BaseModel):
    batch_id: str
    batch_date: str
    chunks_created: int = 0
    duplicates: int = 0
    p0_found: int = 0
    p1_found: int = 0
    lora_adapter: Optional[str] = None
    train_status: str = "pending"


# ─── Endpoints ───

@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/ingest")
async def ingest(batch: IngestBatch, x_api_key: Optional[str] = Header(None)):
    """Devices push data here. Dedup by content_hash."""
    verify_api_key(x_api_key)
    db = get_db()
    cur = db.cursor()
    inserted = 0
    duplicates = 0
    errors = 0

    for item in batch.items:
        # Compute hash if not provided
        content_hash = item.content_hash or hashlib.sha256(
            json.dumps(item.content, sort_keys=True, default=str).encode()
        ).hexdigest()

        try:
            cur.execute("""
                INSERT INTO ingestion_queue (device, source, data_type, content, content_hash, device_time, priority, hidden_data)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                item.device, item.source, item.data_type, Json(item.content),
                content_hash, item.device_time, item.priority,
                Json(item.hidden_data) if item.hidden_data else None
            ))
            inserted += 1
        except psycopg2.errors.UniqueViolation:
            db.rollback()
            duplicates += 1
        except Exception as e:
            db.rollback()
            errors += 1

    db.commit()
    cur.close()
    db.close()

    return {
        "status": "ok",
        "inserted": inserted,
        "duplicates": duplicates,
        "errors": errors,
        "total_received": len(batch.items)
    }


@app.get("/pull")
async def pull(status: str = "new", limit: int = 10000, x_api_key: Optional[str] = Header(None)):
    """Main PC pulls new/processed data."""
    verify_api_key(x_api_key)
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT id, device, source, data_type, content, content_hash, device_time,
               ingested_at, status, priority, hidden_data
        FROM ingestion_queue
        WHERE status = %s
        ORDER BY ingested_at ASC
        LIMIT %s
    """, (status, limit))
    rows = cur.fetchall()
    cur.close()
    db.close()

    return {
        "status": "ok",
        "count": len(rows),
        "items": [dict(r) for r in rows]
    }


@app.post("/pull/mark")
async def mark_processed(req: MarkProcessed, x_api_key: Optional[str] = Header(None)):
    """Mark items as processed after main PC has ingested them."""
    verify_api_key(x_api_key)
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        UPDATE ingestion_queue
        SET status = 'processed', processed_at = now()
        WHERE id = ANY(%s)
    """, (req.ids,))
    updated = cur.rowcount
    db.commit()
    cur.close()
    db.close()

    return {"status": "ok", "updated": updated}


@app.post("/register")
async def register_device(dev: DeviceRegister, x_api_key: Optional[str] = Header(None)):
    """Register or update a device in the registry."""
    verify_api_key(x_api_key)
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO device_registry (device_id, device_name, device_type, os, location, agent_version, apps, credentials, last_seen, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), now())
        ON CONFLICT (device_id) DO UPDATE SET
            device_name = EXCLUDED.device_name,
            device_type = EXCLUDED.device_type,
            os = EXCLUDED.os,
            location = EXCLUDED.location,
            agent_version = EXCLUDED.agent_version,
            apps = EXCLUDED.apps,
            credentials = EXCLUDED.credentials,
            last_seen = now(),
            updated_at = now()
    """, (
        dev.device_id, dev.device_name, dev.device_type, dev.os,
        dev.location, dev.agent_version, Json(dev.apps), Json(dev.credentials)
    ))
    db.commit()
    cur.close()
    db.close()

    return {"status": "ok", "device_id": dev.device_id}


@app.get("/status")
async def device_status(x_api_key: Optional[str] = Header(None)):
    """Check all device health — last seen, pending items."""
    verify_api_key(x_api_key)
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT d.device_id, d.device_name, d.last_seen, d.is_active,
               (SELECT count(*) FROM ingestion_queue q WHERE q.device = d.device_id AND q.status = 'new') as pending_items
        FROM device_registry d
        ORDER BY d.device_id
    """)
    rows = cur.fetchall()
    cur.close()
    db.close()

    return {"status": "ok", "devices": [dict(r) for r in rows]}


@app.post("/audit")
async def log_audit(entry: AuditEntry, x_api_key: Optional[str] = Header(None)):
    """Log an agent action to the audit trail."""
    verify_api_key(x_api_key)
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO audit_trail (agent_session, device, action_type, action_detail,
            input_ref, output_ref, data_affected, priority, correlation_ids,
            integrity_hash, duration_ms, status, error)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        entry.agent_session, entry.device, entry.action_type, entry.action_detail,
        entry.input_ref, entry.output_ref, Json(entry.data_affected) if entry.data_affected else None,
        entry.priority, Json(entry.correlation_ids) if entry.correlation_ids else None,
        entry.integrity_hash, entry.duration_ms, entry.status, entry.error
    ))
    db.commit()
    cur.close()
    db.close()

    return {"status": "ok"}


@app.get("/audit/query")
async def query_audit(
    device: Optional[str] = None,
    action_type: Optional[str] = None,
    priority: Optional[str] = None,
    status: Optional[str] = None,
    hours: int = 24,
    limit: int = 1000,
    x_api_key: Optional[str] = Header(None)
):
    """Query audit trail with filters."""
    verify_api_key(x_api_key)
    db = get_db()
    cur = db.cursor()

    query = "SELECT * FROM audit_trail WHERE timestamp > now() - interval '%s hours'"
    params = [hours]

    if device:
        query += " AND device = %s"
        params.append(device)
    if action_type:
        query += " AND action_type = %s"
        params.append(action_type)
    if priority:
        query += " AND priority = %s"
        params.append(priority)
    if status:
        query += " AND status = %s"
        params.append(status)

    query += " ORDER BY timestamp DESC LIMIT %s"
    params.append(limit)

    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    db.close()

    return {"status": "ok", "count": len(rows), "entries": [dict(r) for r in rows]}


@app.post("/correlation")
async def store_correlation(corr: CorrelationEntry, x_api_key: Optional[str] = Header(None)):
    """Store a cross-device correlation."""
    verify_api_key(x_api_key)
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO correlations (correlation_id, type, devices, evidence, relationship, confidence, timestamp_correlation)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (correlation_id) DO UPDATE SET
            type = EXCLUDED.type,
            devices = EXCLUDED.devices,
            evidence = EXCLUDED.evidence,
            relationship = EXCLUDED.relationship,
            confidence = EXCLUDED.confidence
    """, (
        corr.correlation_id, corr.type, Json(corr.devices), Json(corr.evidence),
        corr.relationship, corr.confidence, corr.timestamp_correlation
    ))
    db.commit()
    cur.close()
    db.close()

    return {"status": "ok", "correlation_id": corr.correlation_id}


@app.get("/training/status")
async def get_training_status(x_api_key: Optional[str] = Header(None)):
    """Get latest training batch status."""
    verify_api_key(x_api_key)
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT * FROM training_status ORDER BY batch_date DESC LIMIT 30
    """)
    rows = cur.fetchall()
    cur.close()
    db.close()

    return {"status": "ok", "batches": [dict(r) for r in rows]}


@app.post("/training/status")
async def update_training_status(update: TrainingStatusUpdate, x_api_key: Optional[str] = Header(None)):
    """Update or create a training batch status."""
    verify_api_key(x_api_key)
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO training_status (batch_id, batch_date, chunks_created, duplicates,
            p0_found, p1_found, lora_adapter, train_status, trained_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (batch_id) DO UPDATE SET
            chunks_created = EXCLUDED.chunks_created,
            duplicates = EXCLUDED.duplicates,
            p0_found = EXCLUDED.p0_found,
            p1_found = EXCLUDED.p1_found,
            lora_adapter = EXCLUDED.lora_adapter,
            train_status = EXCLUDED.train_status,
            trained_at = now()
    """, (
        update.batch_id, update.batch_date, update.chunks_created, update.duplicates,
        update.p0_found, update.p1_found, update.lora_adapter, update.train_status
    ))
    db.commit()
    cur.close()
    db.close()

    return {"status": "ok", "batch_id": update.batch_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8443)