"""
Jayti Agent - Retrieval API (FAISS + MiniLM on Vultr hub).

Endpoints:
    GET  /healthz          -> liveness, no auth
    POST /retrieve         -> top-k docs for a query (X-Device-Id/X-API-Key or Bearer bootstrap)
    GET  /retrieve/sample  -> fetch a doc by doc_id (bounded scan) or index_id (exact)

The FAISS index is memory-mapped read-only (1.77 GB file). doc_store.jsonl
is read via doc_offsets.bin (uint64 LE byte offsets), so the 2.2 GB store
is never fully loaded into RAM.

Index: all-MiniLM-L6-v2, dim 384, IndexFlatIP, normalized (cosine).
"""

import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request

import numpy as np

BASE = Path(os.environ.get("JAYTI_RETRIEVAL_DIR", "/opt/jayti/retrieval"))
INDEX_DIR = BASE / "faiss_index"
FAISS_FILE = INDEX_DIR / "faiss_index.bin"
OFFSETS_FILE = INDEX_DIR / "doc_offsets.bin"
STORE_FILE = INDEX_DIR / "doc_store.jsonl"
META_FILE = INDEX_DIR / "index_meta.json"

BOOTSTRAP_KEY = os.environ.get("JAYTI_BOOTSTRAP_KEY", "")

app = FastAPI(title="Jayti Retrieval API", version="1.1.0")

# --- heavy singletons, loaded once --------------------------------------

_encoder = None
_index = None
_offsets = None
_n_offsets = 0
_store_size = 0
_store_fh = None


def _load_encoder():
    global _encoder
    if _encoder is None:
        from sentence_transformers import SentenceTransformer
        _encoder = SentenceTransformer("all-MiniLM-L6-v2")
    return _encoder


def _load_index():
    global _index
    if _index is None:
        import faiss
        t0 = time.time()
        _index = faiss.read_index(str(FAISS_FILE), faiss.IO_FLAG_MMAP | faiss.IO_FLAG_READ_ONLY)
        print(f"[retrieval] index loaded in {time.time()-t0:.1f}s ntotal={_index.ntotal}", flush=True)
    return _index


def _load_offsets():
    global _offsets, _n_offsets, _store_size, _store_fh
    if _offsets is None:
        raw = OFFSETS_FILE.read_bytes()
        _offsets = np.frombuffer(raw, dtype="<u8")
        _n_offsets = int(_offsets.size)
        _store_size = STORE_FILE.stat().st_size
        _store_fh = open(STORE_FILE, "rb")
        print(f"[retrieval] offsets loaded: {_n_offsets} entries, store={_store_size} B", flush=True)
    return _offsets


def _doc_for_index_id(iid: int):
    """Map faiss index position -> doc_store entry (O(1) via offsets)."""
    offs = _load_offsets()
    if iid < 0 or iid >= _n_offsets:
        return None
    start = int(offs[iid])
    end = int(offs[iid + 1]) if iid + 1 < _n_offsets else _store_size
    if start >= end or start > _store_size:
        return None
    _store_fh.seek(start)
    line = _store_fh.readline()
    try:
        return json.loads(line.decode("utf-8", errors="replace"))
    except Exception:
        return None


# --- auth (mirrors jayti_hub_server) -------------------------------------

async def _auth(x_device_id, x_api_key, authorization):
    import asyncpg
    import argon2
    from argon2.exceptions import VerifyMismatchError

    dsn = os.environ.get("JAYTI_PG_DSN", "")
    if x_device_id and x_api_key:
        if not dsn:
            raise HTTPException(503, "auth backend not configured")
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        try:
            row = await pool.fetchrow(
                "SELECT device_id, key_hash FROM api_keys "
                "WHERE device_id = $1 AND revoked_at IS NULL "
                "ORDER BY issued_at DESC LIMIT 1",
                x_device_id,
            )
            if not row:
                raise HTTPException(401, "unknown or revoked device")
            ph = argon2.PasswordHasher()
            try:
                ph.verify(row["key_hash"], x_api_key)
            except VerifyMismatchError:
                raise HTTPException(401, "bad api key")
            await pool.execute(
                "UPDATE api_keys SET last_used_at = NOW() WHERE device_id = $1", x_device_id
            )
            return x_device_id
        finally:
            await pool.close()
    if authorization and authorization.startswith("Bearer "):
        if BOOTSTRAP_KEY and authorization[7:] == BOOTSTRAP_KEY:
            return "__bootstrap__"
    raise HTTPException(401, "missing credentials")


# --- endpoints -----------------------------------------------------------

@app.get("/healthz")
async def healthz():
    return {"ok": True, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


@app.post("/retrieve")
async def retrieve(
    request: Request,
    x_device_id: str = Header(None),
    x_api_key: str = Header(None),
    authorization: str = Header(None),
):
    actor = await _auth(x_device_id, x_api_key, authorization)
    body = await request.json()
    query = str(body.get("query", "")).strip()
    k = min(int(body.get("k", 5)), 50)
    if not query:
        raise HTTPException(422, "query is required")

    t0 = time.time()
    enc = _load_encoder()
    vec = enc.encode([query], normalize_embeddings=True)[0].astype(np.float32)
    t_embed = time.time() - t0

    idx = _load_index()
    t0 = time.time()
    D, I = idx.search(vec.reshape(1, -1), k)
    t_search = time.time() - t0

    hits = []
    for dist, iid in zip(D[0], I[0]):
        doc = _doc_for_index_id(int(iid))
        if doc is None:
            continue
        hits.append({
            "score": round(float(dist), 6),
            "index_id": int(iid),
            "doc_id": doc.get("id"),
            "metadata": doc.get("metadata"),
            "document": str(doc.get("document", ""))[:2000],
        })

    return {
        "query": query,
        "actor": actor,
        "k": len(hits),
        "latency": {"embed_ms": round(t_embed * 1000, 1), "search_ms": round(t_search * 1000, 1)},
        "hits": hits,
    }


@app.get("/retrieve/sample")
async def sample(
    doc_id: str = None,
    index_id: int = None,
    x_device_id: str = Header(None),
    x_api_key: str = Header(None),
    authorization: str = Header(None),
):
    await _auth(x_device_id, x_api_key, authorization)
    if index_id is not None:
        doc = _doc_for_index_id(index_id)
        if doc is None:
            raise HTTPException(404, "index_id not found")
        return {"index_id": index_id, "doc": doc}
    if doc_id:
        offs = _load_offsets()
        for i in range(0, _n_offsets):
            doc = _doc_for_index_id(i)
            if doc and doc.get("id") == doc_id:
                return {"index_id": i, "doc": doc}
        raise HTTPException(404, "doc id not found")
    raise HTTPException(422, "doc_id or index_id required")


@app.get("/retrieve/info")
async def info(
    x_device_id: str = Header(None),
    x_api_key: str = Header(None),
    authorization: str = Header(None),
):
    await _auth(x_device_id, x_api_key, authorization)
    idx = _load_index()
    offs = _load_offsets()
    meta = json.loads(META_FILE.read_text()) if META_FILE.exists() else {}
    return {
        "index": {"ntotal": idx.ntotal, "d": idx.d, "is_trained": idx.is_trained},
        "doc_store": {"entries": int(_n_offsets), "size_bytes": int(_store_size)},
        "index_meta": meta,
    }
