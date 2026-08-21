#!/usr/bin/env python3
"""
EKA Agent — Cloud LLM Agent Server (VPS deployment)
====================================================
Uses Anthropic Claude API for generation instead of local Qwen model.
VPS (3.8GB RAM) can run FAISS retrieval + embedding model but NOT a local LLM.

Endpoints:
  GET  /health         — server + retrieval status
  POST /query          — full agent query (RAG + Claude generation)
  POST /query/stream   — streaming version (SSE)
  POST /raw            — direct Claude query (no RAG)

Usage:
  1. Start retrieval server:  python eka_retrieval_server_vps.py   (port 8100)
  2. Start agent server:      python eka_agent_cloud.py            (port 8000)
  3. Query:  curl -X POST http://localhost:8000/query -d '{"query":"What data was found?"}'
"""
import os
os.environ.setdefault("OMP_NUM_THREADS", "2")

import json
import time
import logging
import urllib.request
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("eka-agent")

# ── Config ─────────────────────────────────────────────────────────────
RETRIEVAL_URL = os.environ.get("RETRIEVAL_URL", "http://localhost:8100")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
AGENT_PORT = 8000
MAX_TOKENS = 1024
TEMPERATURE = 0.7
TOP_K_RETRIEVAL = 5

# ── Request / Response models ──────────────────────────────────────────
class QueryRequest(BaseModel):
    query: str
    top_k: int = Field(default=TOP_K_RETRIEVAL, ge=1, le=20)
    max_tokens: int = Field(default=MAX_TOKENS, ge=32, le=8192)
    temperature: float = Field(default=TEMPERATURE, ge=0.0, le=2.0)
    use_rag: bool = Field(default=True)


class QueryResponse(BaseModel):
    query: str
    response: str
    sources: int
    retrieval_time_ms: float
    generation_time_ms: float
    total_time_ms: float
    model: str


class HealthResponse(BaseModel):
    status: str
    retrieval_available: bool
    model: str
    api_configured: bool


# ── App ────────────────────────────────────────────────────────────────
app = FastAPI(title="EKA Agent Server (Cloud LLM)", version="2.0.0")


def retrieve_context(query: str, top_k: int) -> dict:
    """Call the retrieval server for RAG context."""
    payload = json.dumps({"query": query, "top_k": top_k}).encode("utf-8")
    req = urllib.request.Request(
        f"{RETRIEVAL_URL}/augment",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def check_retrieval() -> bool:
    """Check if retrieval server is available."""
    try:
        req = urllib.request.Request(f"{RETRIEVAL_URL}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return data.get("status") == "ok"
    except Exception:
        return False


def build_system_prompt(context: Optional[str] = None) -> str:
    """Build the system prompt with optional RAG context."""
    if context and context != "No relevant context found.":
        return (
            "You are EKA Agent, a forensic intelligence assistant. "
            "Use the following knowledge context retrieved from a database of "
            "1.15M evidence chunks to answer the user's question accurately. "
            "If the context doesn't contain the answer, say so clearly.\n\n"
            f"Knowledge Context:\n{context}"
        )
    return (
        "You are EKA Agent, a forensic intelligence assistant. "
        "Answer the user's question based on your knowledge."
    )


def call_claude(query: str, context: Optional[str], max_tokens: int, temperature: float) -> str:
    """Call Anthropic Claude API for generation."""
    system_prompt = build_system_prompt(context)

    payload = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": query}
        ]
    }).encode("utf-8")

    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
        # Claude returns content as a list of content blocks
        texts = [block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"]
        return "".join(texts).strip()


def call_claude_stream(query: str, context: Optional[str], max_tokens: int, temperature: float):
    """Call Anthropic Claude API and yield text chunks for SSE streaming."""
    system_prompt = build_system_prompt(context)

    payload = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": query}
        ],
        "stream": True
    }).encode("utf-8")

    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
        buffer = b""
        for chunk in iter(lambda: resp.read(4096), b""):
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line or not line.startswith(b"data: "):
                    continue
                data_str = line[6:].decode("utf-8")
                try:
                    evt = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                if evt.get("type") == "content_block_delta":
                    delta = evt.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield text
                elif evt.get("type") == "message_stop":
                    return


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(
        status="ok",
        retrieval_available=check_retrieval(),
        model=ANTHROPIC_MODEL,
        api_configured=bool(ANTHROPIC_API_KEY),
    )


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    t_total = time.time()

    # Phase 1: Retrieval
    retrieval_ms = 0.0
    sources = 0
    context = None
    if req.use_rag:
        try:
            t_ret = time.time()
            ret = retrieve_context(req.query, req.top_k)
            retrieval_ms = (time.time() - t_ret) * 1000
            context = ret.get("context", "")
            sources = ret.get("sources", 0)
        except Exception as e:
            logger.warning("Retrieval failed: %s — continuing without RAG", e)

    # Phase 2: Generate via Claude
    t_gen = time.time()
    response_text = call_claude(req.query, context, req.max_tokens, req.temperature)
    gen_ms = (time.time() - t_gen) * 1000

    total_ms = (time.time() - t_total) * 1000
    return QueryResponse(
        query=req.query,
        response=response_text,
        sources=sources,
        retrieval_time_ms=round(retrieval_ms, 2),
        generation_time_ms=round(gen_ms, 2),
        total_time_ms=round(total_ms, 2),
        model=ANTHROPIC_MODEL,
    )


@app.post("/raw", response_model=QueryResponse)
def raw_query(req: QueryRequest):
    """Direct Claude query without RAG."""
    t_total = time.time()
    t_gen = time.time()
    response_text = call_claude(req.query, None, req.max_tokens, req.temperature)
    gen_ms = (time.time() - t_gen) * 1000
    total_ms = (time.time() - t_total) * 1000
    return QueryResponse(
        query=req.query,
        response=response_text,
        sources=0,
        retrieval_time_ms=0.0,
        generation_time_ms=round(gen_ms, 2),
        total_time_ms=round(total_ms, 2),
        model=ANTHROPIC_MODEL,
    )


@app.post("/query/stream")
async def query_stream(req: QueryRequest):
    """Streaming query endpoint using SSE."""
    from fastapi.responses import StreamingResponse

    context = None
    if req.use_rag:
        try:
            ret = retrieve_context(req.query, req.top_k)
            context = ret.get("context", "")
        except Exception:
            pass

    def event_stream():
        for text in call_claude_stream(req.query, context, req.max_tokens, req.temperature):
            yield f"data: {json.dumps({'text': text})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=AGENT_PORT, log_level="info")