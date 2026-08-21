#!/usr/bin/env python3
"""
EKA Agent — FAISS Retrieval Server (memory-efficient)
Loads the FAISS index + a line-offset index for doc_store.
Documents are read on-demand by seeking to byte offsets (not loaded into RAM).

Endpoints:
  GET  /health         — server status
  POST /search         — semantic search (query -> top-k chunks)
  POST /augment        — RAG: retrieve + format context for the LLM

Index:  D:/training-data/faiss_index/faiss_index.bin  (1.15M vectors, 384-dim, IndexFlatIP)
Store:  D:/training-data/faiss_index/doc_store.jsonl  (1.15M doc entries)
Model:  all-MiniLM-L6-v2 (normalized embeddings, cosine similarity)
"""
import os
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import json
import time
import logging
import struct
from pathlib import Path
from typing import List, Dict, Any, Optional

import numpy as np
import faiss
from sentence_transformers import SentenceTransformer
from fastapi import FastAPI
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("eka-retrieval")

# ── Paths ──────────────────────────────────────────────────────────────
INDEX_DIR = Path(r"D:\training-data\faiss_index")
INDEX_PATH = INDEX_DIR / "faiss_index.bin"
DOC_STORE_PATH = INDEX_DIR / "doc_store.jsonl"
OFFSET_PATH = INDEX_DIR / "doc_offsets.bin"
META_PATH = INDEX_DIR / "index_meta.json"
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
EMBED_DIM = 384

# ── Globals ─────────────────────────────────────────────────────────────
embed_model: Optional[SentenceTransformer] = None
faiss_index: Optional[faiss.IndexFlatIP] = None
doc_offsets: Optional[np.ndarray] = None  # int64 byte offsets per line
doc_count: int = 0
doc_file_handle = None
index_meta: Dict[str, Any] = {}


# ── Request / Response models ──────────────────────────────────────────
class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=50)
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0)


class SearchHit(BaseModel):
    id: str
    document: str
    score: float
    metadata: Dict[str, Any]
    line: int


class SearchResponse(BaseModel):
    query: str
    total: int
    hits: List[SearchHit]
    search_time_ms: float


class AugmentRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=20)


class AugmentResponse(BaseModel):
    query: str
    context: str
    sources: int
    retrieval_time_ms: float


# ── App ─────────────────────────────────────────────────────────────────
app = FastAPI(title="EKA Agent Retrieval Server", version="1.0.0")


def build_offset_index():
    """Scan doc_store.jsonl once, record byte offset of each line.
    Saves to doc_offsets.bin as int64 array (~9MB for 1.15M lines)."""
    logger.info("Building line-offset index for doc_store...")
    t0 = time.time()
    offsets = []
    offset = 0
    with open(DOC_STORE_PATH, "rb") as f:
        for line in f:
            offsets.append(offset)
            offset += len(line)
    arr = np.array(offsets, dtype=np.int64)
    arr.tofile(str(OFFSET_PATH))
    logger.info("Offset index built: %s lines in %.1fs (%.1f MB)",
                f"{len(arr):,}", time.time() - t0, arr.nbytes / 1e6)
    return arr


def load_offset_index() -> np.ndarray:
    """Load prebuilt offset index, or rebuild if missing/stale."""
    if OFFSET_PATH.exists():
        arr = np.fromfile(str(OFFSET_PATH), dtype=np.int64)
        logger.info("Loaded offset index: %s lines from %s", f"{len(arr):,}", OFFSET_PATH.name)
        return arr
    return build_offset_index()


def get_doc_by_index(idx: int) -> Dict[str, Any]:
    """Read a single doc_store entry by its line index using byte offset."""
    pos = int(doc_offsets[idx])
    doc_file_handle.seek(pos)
    line = doc_file_handle.readline()
    return json.loads(line)


@app.on_event("startup")
def load_resources():
    global embed_model, faiss_index, doc_offsets, doc_count, doc_file_handle, index_meta
    t0 = time.time()

    logger.info("Loading embedding model: %s", EMBED_MODEL_NAME)
    embed_model = SentenceTransformer(EMBED_MODEL_NAME)

    logger.info("Loading FAISS index: %s", INDEX_PATH)
    faiss_index = faiss.read_index(str(INDEX_PATH))
    logger.info("FAISS index loaded: %s vectors", f"{faiss_index.ntotal:,}")

    doc_offsets = load_offset_index()
    doc_count = len(doc_offsets)

    # Open file handle for on-demand reads (binary mode, will decode per line)
    doc_file_handle = open(DOC_STORE_PATH, "rb")

    if META_PATH.exists():
        with open(META_PATH, "r") as f:
            index_meta = json.load(f)

    elapsed = time.time() - t0
    logger.info("Retrieval server ready in %.1fs — %s vectors, %s docs (offset-index mode)",
                elapsed, f"{faiss_index.ntotal:,}", f"{doc_count:,}")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "index_vectors": faiss_index.ntotal if faiss_index else 0,
        "doc_count": doc_count,
        "model": EMBED_MODEL_NAME,
        "dim": EMBED_DIM,
        "index_type": index_meta.get("type", "IndexFlatIP"),
        "mode": "offset-index",
    }


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest):
    t0 = time.time()

    query_emb = embed_model.encode(
        [req.query],
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype(np.float32)

    k = min(req.top_k, faiss_index.ntotal)
    scores, indices = faiss_index.search(query_emb, k)

    hits = []
    for i, idx in enumerate(indices[0]):
        if idx < 0 or idx >= doc_count:
            continue
        score = float(scores[0][i])
        if score < req.score_threshold:
            continue
        entry = get_doc_by_index(int(idx))
        hits.append(SearchHit(
            id=entry["id"],
            document=entry["document"],
            score=score,
            metadata=entry.get("metadata", {}),
            line=entry.get("line", -1),
        ))

    elapsed_ms = (time.time() - t0) * 1000
    return SearchResponse(
        query=req.query,
        total=len(hits),
        hits=hits,
        search_time_ms=round(elapsed_ms, 2),
    )


@app.post("/augment", response_model=AugmentResponse)
def augment(req: AugmentRequest):
    t0 = time.time()

    query_emb = embed_model.encode(
        [req.query],
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype(np.float32)

    k = min(req.top_k, faiss_index.ntotal)
    scores, indices = faiss_index.search(query_emb, k)

    chunks = []
    for i, idx in enumerate(indices[0]):
        if idx < 0 or idx >= doc_count:
            continue
        entry = get_doc_by_index(int(idx))
        score = float(scores[0][i])
        chunks.append({
            "document": entry["document"],
            "score": score,
            "metadata": entry.get("metadata", {}),
        })

    context_parts = []
    for j, c in enumerate(chunks, 1):
        context_parts.append(f"[{j}] (score: {c['score']:.3f})\n{c['document']}")
    context = "\n\n---\n\n".join(context_parts) if context_parts else "No relevant context found."

    elapsed_ms = (time.time() - t0) * 1000
    return AugmentResponse(
        query=req.query,
        context=context,
        sources=len(chunks),
        retrieval_time_ms=round(elapsed_ms, 2),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100, log_level="info")