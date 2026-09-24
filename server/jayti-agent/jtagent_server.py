"""
jtagent_server.py — the personal agent behind https://agent.jaytipargal.tech/agent/

nginx routes /agent/ -> 127.0.0.1:8000 and the jayti-dashboard Vercel project
proxies /agent/query -> ${AGENT_API_URL}/query, so this server keeps the
eka_agent_cloud.py contract the dashboard already speaks:

  GET  /health -> {status, retrieval_available, model, api_configured, auth_configured}
  POST /query  -> {query, response, sources, retrieval_time_ms, generation_time_ms,
                   total_time_ms, model}  (+ source_docs, mode, state when relevant)
  GET  /state  -> live Hub /status + per-device ingestion_queue counts

Grounding: every answer is built from the Jayti retrieval index
(/retrieve on :8444, 1.15M docs) and cites the documents it used. Generation
is GPT-2 + the newest LoRA adapter trained in Colab from the Hub queue; it is
loaded lazily and skipped when the adapter or RAM is missing, so a query
never fails just because the LLM is unavailable.

Env (/etc/jayti/agent.env):
  JTAGENT_API_KEY      X-API-Key clients must send (same value nginx checks)
  JAYTI_BOOTSTRAP_KEY  bearer for /retrieve on the local retrieval service
  JAYTI_PG_DSN         Postgres DSN for per-device queue counts
  RETRIEVAL_URL        default http://127.0.0.1:8444
  HUB_URL              default http://127.0.0.1:8443
  JTAGENT_ADAPTER      dir with adapter_config.json (LoRA on gpt2)
  JTAGENT_GENERATE     "1" to enable GPT-2+LoRA generation (default 1)
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

NGINX_KEY_FILE = os.environ.get("JTAGENT_NGINX_KEY_FILE", "/etc/nginx/conf.d/eka-agent-api-key.conf")


def _nginx_api_key(path: str) -> str:
    """The X-API-Key nginx already enforces for /agent/; reuse it so there is
    one key (the dashboard's AGENT_API_KEY) and no second secret file."""
    try:
        with open(path, encoding="utf-8") as fh:
            m = re.search(r'^\s*"([^"]+)"\s+1\s*;', fh.read(), re.M)
            return m.group(1) if m else ""
    except OSError:
        return ""


API_KEY = os.environ.get("JTAGENT_API_KEY") or _nginx_api_key(NGINX_KEY_FILE)
BOOTSTRAP_KEY = os.environ.get("JAYTI_BOOTSTRAP_KEY", "")
DSN = os.environ.get("JAYTI_PG_DSN", "")
RETRIEVAL_URL = os.environ.get("RETRIEVAL_URL", "http://127.0.0.1:8444").rstrip("/")
HUB_URL = os.environ.get("HUB_URL", "http://127.0.0.1:8443").rstrip("/")
ADAPTER_DIR = os.environ.get("JTAGENT_ADAPTER", "")
GENERATE = os.environ.get("JTAGENT_GENERATE", "1") == "1"
AGENT_NAME = "jtagent"

app = FastAPI(title="jtagent", version="0.1.0")

_STATE_INTENT = re.compile(r"\b(hub|device|devices|queue|status|ingest|ingested|rows|registered)\b", re.I)


class QueryRequest(BaseModel):
    query: str = ""
    question: str = ""
    top_k: int = Field(default=5, ge=1, le=20)
    max_tokens: int = Field(default=128, ge=16, le=1024)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    use_rag: bool = True


def _check_key(x_api_key: Optional[str], authorization: Optional[str]) -> None:
    if not API_KEY:
        raise HTTPException(503, "JTAGENT_API_KEY not configured")
    token = x_api_key or (authorization[7:] if authorization and authorization.startswith("Bearer ") else None)
    if token != API_KEY:
        raise HTTPException(401, "missing or invalid X-API-Key")


def _http_json(method: str, url: str, body: Optional[dict] = None, headers: Optional[dict] = None, timeout: int = 60):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --- retrieval -------------------------------------------------------------

def retrieve(query: str, k: int) -> list[dict]:
    body = _http_json(
        "POST", f"{RETRIEVAL_URL}/retrieve", {"query": query, "k": k},
        {"Authorization": f"Bearer {BOOTSTRAP_KEY}"},
    )
    return body.get("hits") or []


def retrieval_available() -> bool:
    try:
        return bool(_http_json("GET", f"{RETRIEVAL_URL}/healthz", timeout=5).get("ok"))
    except Exception:
        return False


# --- generation (lazy GPT-2 + LoRA) ------------------------------------------

_gen_lock = threading.Lock()
_gen = {"tok": None, "model": None, "name": "extractive", "error": None}


def _load_generator():
    """Load gpt2 + the LoRA adapter once. Any failure leaves extractive mode."""
    if _gen["model"] is not None or _gen["error"] is not None or not GENERATE:
        return
    with _gen_lock:
        if _gen["model"] is not None or _gen["error"] is not None:
            return
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            torch.set_num_threads(int(os.environ.get("JTAGENT_THREADS", "2")))
            base = "gpt2"
            if ADAPTER_DIR and os.path.isfile(os.path.join(ADAPTER_DIR, "adapter_config.json")):
                with open(os.path.join(ADAPTER_DIR, "adapter_config.json"), encoding="utf-8") as fh:
                    base = json.load(fh).get("base_model_name_or_path") or base
            tok = AutoTokenizer.from_pretrained(base)
            model = AutoModelForCausalLM.from_pretrained(base)
            name = base
            if ADAPTER_DIR and os.path.isdir(ADAPTER_DIR):
                from peft import PeftModel

                model = PeftModel.from_pretrained(model, ADAPTER_DIR)
                name = f"{base}+lora:{os.path.basename(os.path.dirname(ADAPTER_DIR.rstrip('/')))}"
            model.eval()
            _gen.update(tok=tok, model=model, name=name)
        except Exception as e:  # noqa: BLE001 - generation is optional
            _gen["error"] = f"{type(e).__name__}: {e}"
            print(f"[jtagent] generator unavailable, extractive mode: {_gen['error']}", flush=True)


def generate(prompt: str, max_new_tokens: int, temperature: float) -> Optional[str]:
    _load_generator()
    if _gen["model"] is None:
        return None
    import torch

    tok, model = _gen["tok"], _gen["model"]
    ids = tok(prompt, return_tensors="pt", truncation=True, max_length=768)
    with torch.no_grad():
        out = model.generate(
            **ids, max_new_tokens=max_new_tokens, do_sample=temperature > 0,
            temperature=max(temperature, 1e-3), top_p=0.9, pad_token_id=tok.eos_token_id,
        )
    text = tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)
    return text.strip()


# --- hub state -------------------------------------------------------------

async def hub_state() -> dict:
    state: dict = {"hub": HUB_URL}
    try:
        state["status"] = _http_json("GET", f"{HUB_URL}/status", timeout=10)
    except Exception as e:  # noqa: BLE001
        state["status_error"] = f"{type(e).__name__}: {e}"
    if DSN:
        try:
            import asyncpg

            conn = await asyncpg.connect(DSN, timeout=10)
            try:
                rows = await conn.fetch(
                    "SELECT d.device_id, d.device_type, d.os, d.is_active, "
                    "  count(q.id) AS items, "
                    "  count(q.id) FILTER (WHERE q.status = 'new') AS unprocessed, "
                    "  max(q.ingested_at) AS last_ingest "
                    "FROM device_registry d LEFT JOIN ingestion_queue q ON q.device = d.device_id "
                    "GROUP BY 1,2,3,4 ORDER BY 1"
                )
            finally:
                await conn.close()
            state["devices"] = [
                {
                    "device_id": r["device_id"], "device_type": r["device_type"], "os": r["os"],
                    "is_active": r["is_active"], "items": r["items"], "unprocessed": r["unprocessed"],
                    "last_ingest": r["last_ingest"].isoformat() if r["last_ingest"] else None,
                }
                for r in rows
            ]
        except Exception as e:  # noqa: BLE001
            state["devices_error"] = f"{type(e).__name__}: {e}"
    return state


def _format_state(state: dict) -> str:
    s = state.get("status") or {}
    lines = [
        f"Hub {state['hub']}: ok={s.get('ok')} devices_active={s.get('devices_active')} "
        f"items_total={s.get('items_total')} items_24h={s.get('items_24h')} "
        f"unprocessed={s.get('items_unprocessed')}"
    ]
    for d in state.get("devices") or []:
        lines.append(
            f"- {d['device_id']} ({d['device_type']}/{d['os']}, active={d['is_active']}): "
            f"{d['items']} rows, {d['unprocessed']} unprocessed, last ingest {d['last_ingest'] or 'never'}"
        )
    for k in ("status_error", "devices_error"):
        if state.get(k):
            lines.append(f"({k}: {state[k]})")
    return "\n".join(lines)


# --- endpoints -------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "retrieval_available": retrieval_available(),
        "model": f"{AGENT_NAME}:{_gen['name']}",
        "api_configured": bool(BOOTSTRAP_KEY),
        "auth_configured": bool(API_KEY),
        "adapter": os.path.basename(os.path.dirname(ADAPTER_DIR.rstrip("/"))) if ADAPTER_DIR else None,
    }


@app.get("/state")
async def state(x_api_key: Optional[str] = Header(default=None), authorization: Optional[str] = Header(default=None)):
    _check_key(x_api_key, authorization)
    return await hub_state()


@app.post("/query")
async def query(
    req: QueryRequest,
    x_api_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
):
    _check_key(x_api_key, authorization)
    q = (req.query or req.question or "").strip()
    if not q:
        raise HTTPException(400, "query is required")
    t_total = time.time()

    parts: list[str] = []
    result: dict = {"query": q, "agent": AGENT_NAME}

    # Live Hub/device state when the question is about it.
    if _STATE_INTENT.search(q):
        st = await hub_state()
        result["state"] = st
        parts.append("Current Hub state:\n" + _format_state(st))

    # Grounding: retrieval hits become the evidence and the citations.
    hits: list[dict] = []
    retrieval_ms = 0.0
    if req.use_rag:
        t0 = time.time()
        try:
            hits = retrieve(q, req.top_k)
        except Exception as e:  # noqa: BLE001
            result["retrieval_error"] = f"{type(e).__name__}: {e}"
        retrieval_ms = (time.time() - t0) * 1000

    source_docs = []
    for i, h in enumerate(hits, 1):
        doc = (h.get("document") or "").strip().replace("\n", " ")
        meta = h.get("metadata") or {}
        source_docs.append({
            "n": i, "doc_id": h.get("doc_id"), "index_id": h.get("index_id"),
            "score": h.get("score"), "source": meta.get("source") if isinstance(meta, dict) else None,
            "snippet": doc[:300],
        })

    # Generation: GPT-2 + LoRA over the evidence (optional).
    gen_ms = 0.0
    draft = None
    if hits or not req.use_rag:
        context = "\n".join(f"[{s['n']}] {s['snippet']}" for s in source_docs[:3])
        prompt = f"Jayti data:\n{context}\n\nQuestion: {q}\nAnswer:"
        t0 = time.time()
        try:
            draft = generate(prompt, req.max_tokens, req.temperature)
        except Exception as e:  # noqa: BLE001
            result["generation_error"] = f"{type(e).__name__}: {e}"
        gen_ms = (time.time() - t0) * 1000

    if source_docs:
        parts.append(
            f"Evidence from {len(source_docs)} Jayti record(s):\n"
            + "\n".join(f"[{s['n']}] {s['snippet']} (doc {s['doc_id']}, score {s['score']})" for s in source_docs)
        )
    elif req.use_rag and "state" not in result:
        parts.append("No matching Jayti records were found for this question.")
    if draft:
        parts.append(f"{AGENT_NAME} ({_gen['name']}): {draft}")

    result.update(
        response="\n\n".join(parts) if parts else "(no answer)",
        sources=len(source_docs),
        source_docs=source_docs,
        mode="generate" if draft else "extractive",
        retrieval_time_ms=round(retrieval_ms, 2),
        generation_time_ms=round(gen_ms, 2),
        total_time_ms=round((time.time() - t_total) * 1000, 2),
        model=f"{AGENT_NAME}:{_gen['name']}",
    )
    return result
