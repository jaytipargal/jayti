#!/usr/bin/env python3
"""
EKA Agent — Full Deployment Server
====================================
Integrates:
  1. FAISS retrieval (1.15M knowledge chunks via eka_retrieval_server on :8100)
  2. LoRA-tuned Qwen2.5-0.5B-Instruct (local inference with knowledge context)
  3. FastAPI endpoints for agent queries

Endpoints:
  GET  /health         — server + model + retrieval status
  POST /query          — full agent query (RAG + LLM generation)
  POST /query/stream   — streaming version of /query
  POST /raw            — direct LLM query (no RAG, for comparison)

Usage:
  1. Start retrieval server:  python eka_retrieval_server.py   (port 8100)
  2. Start agent server:      python eka_agent_deploy.py       (port 8000)
  3. Query:  curl -X POST http://localhost:8000/query -d '{"query":"What data was found?"}'
"""
import os
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import json
import time
import logging
import urllib.request
from pathlib import Path
from typing import Optional

import torch
from fastapi import FastAPI
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("eka-agent")

# ── Config ─────────────────────────────────────────────────────────────
RETRIEVAL_URL = "http://localhost:8100"
ADAPTER_PATH = r"D:\training-data\adapters\adapter_fullbatch_2026-08-20\final"
BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
AGENT_PORT = 8000
MAX_NEW_TOKENS = 128
TEMPERATURE = 0.7
TOP_K_RETRIEVAL = 3

# ── Globals ────────────────────────────────────────────────────────────
model = None
tokenizer = None
device = None

# ── Request / Response models ──────────────────────────────────────────
class QueryRequest(BaseModel):
    query: str
    top_k: int = Field(default=TOP_K_RETRIEVAL, ge=1, le=20)
    max_tokens: int = Field(default=MAX_NEW_TOKENS, ge=32, le=2048)
    temperature: float = Field(default=TEMPERATURE, ge=0.0, le=2.0)
    use_rag: bool = Field(default=True)


class QueryResponse(BaseModel):
    query: str
    response: str
    sources: int
    retrieval_time_ms: float
    generation_time_ms: float
    total_time_ms: float


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    retrieval_available: bool
    adapter_path: str
    base_model: str


# ── App ────────────────────────────────────────────────────────────────
app = FastAPI(title="EKA Agent Server", version="1.0.0")


@app.on_event("startup")
def load_model():
    global model, tokenizer, device
    from transformers import AutoTokenizer, AutoModelForCausalLM
    from peft import PeftModel

    logger.info("Loading tokenizer from base model: %s", BASE_MODEL)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info("Device: %s", device)

    # Load tokenizer from base model (adapter tokenizer.json is incompatible)
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    logger.info("Loading base model: %s (float16, low_cpu_mem_usage)", BASE_MODEL)
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        trust_remote_code=True,
        torch_dtype=torch.float16,
        low_cpu_mem_usage=True,
        device_map=device if device == "cuda" else None,
    )

    logger.info("Loading LoRA adapter: %s", ADAPTER_PATH)
    model = PeftModel.from_pretrained(model, ADAPTER_PATH)
    model.eval()

    logger.info("Agent model ready on %s", device)


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


def build_prompt(query: str, context: Optional[str] = None) -> str:
    """Build the instruction prompt for the LoRA-tuned model."""
    if context and context != "No relevant context found.":
        return (
            f"### Instruction:\n"
            f"Use the following knowledge context to answer the question.\n\n"
            f"Knowledge Context:\n{context}\n\n"
            f"Question: {query}\n\n"
            f"### Response:\n"
        )
    return f"### Instruction:\n{query}\n\n### Response:\n"


def generate(prompt: str, max_tokens: int, temperature: float) -> str:
    """Generate text using the local LoRA model."""
    inputs = tokenizer(prompt, return_tensors="pt")
    if device == "cuda":
        inputs = {k: v.to(model.device) for k, v in inputs.items()}

    with torch.inference_mode():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_tokens,
            temperature=temperature if temperature > 0 else 1.0,
            do_sample=temperature > 0,
            pad_token_id=tokenizer.eos_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )

    response = tokenizer.decode(
        outputs[0][inputs["input_ids"].shape[1]:],
        skip_special_tokens=True,
    )
    return response.strip()


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(
        status="ok" if model is not None else "loading",
        model_loaded=model is not None,
        retrieval_available=check_retrieval(),
        adapter_path=ADAPTER_PATH,
        base_model=BASE_MODEL,
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

    # Phase 2: Generate
    prompt = build_prompt(req.query, context)
    t_gen = time.time()
    response_text = generate(prompt, req.max_tokens, req.temperature)
    gen_ms = (time.time() - t_gen) * 1000

    total_ms = (time.time() - t_total) * 1000
    return QueryResponse(
        query=req.query,
        response=response_text,
        sources=sources,
        retrieval_time_ms=round(retrieval_ms, 2),
        generation_time_ms=round(gen_ms, 2),
        total_time_ms=round(total_ms, 2),
    )


@app.post("/raw", response_model=QueryResponse)
def raw_query(req: QueryRequest):
    """Direct LLM query without RAG."""
    t_total = time.time()
    prompt = build_prompt(req.query, None)
    t_gen = time.time()
    response_text = generate(prompt, req.max_tokens, req.temperature)
    gen_ms = (time.time() - t_gen) * 1000
    total_ms = (time.time() - t_total) * 1000
    return QueryResponse(
        query=req.query,
        response=response_text,
        sources=0,
        retrieval_time_ms=0.0,
        generation_time_ms=round(gen_ms, 2),
        total_time_ms=round(total_ms, 2),
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

    prompt = build_prompt(req.query, context)
    inputs = tokenizer(prompt, return_tensors="pt")
    if device == "cuda":
        inputs = {k: v.to(model.device) for k, v in inputs.items()}

    from transformers import TextIteratorStreamer
    streamer = TextIteratorStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True)

    gen_kwargs = {
        **inputs,
        "max_new_tokens": req.max_tokens,
        "temperature": req.temperature,
        "do_sample": req.temperature > 0,
        "pad_token_id": tokenizer.eos_token_id,
        "eos_token_id": tokenizer.eos_token_id,
        "streamer": streamer,
    }

    import threading
    thread = threading.Thread(target=model.generate, kwargs=gen_kwargs)
    thread.start()

    def event_stream():
        for text in streamer:
            yield f"data: {json.dumps({'text': text})}\n\n"
        thread.join()
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=AGENT_PORT, log_level="info")