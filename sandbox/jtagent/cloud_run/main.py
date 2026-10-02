"""Private Cloud Run inference service for the Colab-trained JTAGENT adapter."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

APP_NAME = "jtagent-agent"
ADAPTER_DIR = os.environ.get("JTAGENT_ADAPTER_DIR", "/app/adapter")
FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE", "eka-agent")
FIRESTORE_COLLECTION = "jtagent_agent_conversations"

app = FastAPI(title=APP_NAME, version="1.0.0")
_model_lock = threading.Lock()
_model_state: dict[str, object] = {"tokenizer": None, "model": None, "name": None, "error": None}


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    max_tokens: int = Field(default=128, ge=16, le=256)
    temperature: float = Field(default=0.4, ge=0.0, le=1.5)


def _load_model() -> tuple[object, object, str]:
    if _model_state["model"] is not None:
        return _model_state["tokenizer"], _model_state["model"], str(_model_state["name"])
    if _model_state["error"] is not None:
        raise RuntimeError(str(_model_state["error"]))

    with _model_lock:
        if _model_state["model"] is not None:
            return _model_state["tokenizer"], _model_state["model"], str(_model_state["name"])
        try:
            import json
            from pathlib import Path

            import torch
            from peft import PeftModel
            from transformers import AutoModelForCausalLM, AutoTokenizer

            config = json.loads((Path(ADAPTER_DIR) / "adapter_config.json").read_text(encoding="utf-8"))
            base_model = config["base_model_name_or_path"]
            tokenizer = AutoTokenizer.from_pretrained(base_model)
            model = AutoModelForCausalLM.from_pretrained(base_model, low_cpu_mem_usage=True)
            model = PeftModel.from_pretrained(model, ADAPTER_DIR)
            model.eval()
            _model_state.update(tokenizer=tokenizer, model=model, name=f"{base_model}+LoRA")
        except Exception as exc:  # keep later failures concise and deterministic
            _model_state["error"] = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(str(_model_state["error"])) from exc
    return _model_state["tokenizer"], _model_state["model"], str(_model_state["name"])


def _store(query: str, response: str, model_name: str, elapsed_ms: float) -> None:
    from google.cloud import firestore

    client = firestore.Client(database=FIRESTORE_DATABASE)
    client.collection(FIRESTORE_COLLECTION).add(
        {
            "created_at": datetime.now(timezone.utc),
            "query": query,
            "response": response,
            "model": model_name,
            "elapsed_ms": round(elapsed_ms, 2),
        }
    )


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "model_loaded": _model_state["model"] is not None,
        "model": _model_state["name"],
        "database": FIRESTORE_DATABASE,
        "collection": FIRESTORE_COLLECTION,
    }


@app.post("/query")
def query(request: QueryRequest) -> dict[str, object]:
    started = time.monotonic()
    try:
        tokenizer, model, model_name = _load_model()
        import torch

        messages = [
            {"role": "system", "content": "You are JTAGENT, a careful personal assistant."},
            {"role": "user", "content": request.query},
        ]
        prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(prompt, return_tensors="pt")
        with torch.inference_mode():
            output = model.generate(
                **inputs,
                max_new_tokens=request.max_tokens,
                do_sample=request.temperature > 0,
                temperature=max(request.temperature, 0.01),
                top_p=0.9,
                pad_token_id=tokenizer.eos_token_id,
            )
        response = tokenizer.decode(output[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True).strip()
        elapsed_ms = (time.monotonic() - started) * 1000
        _store(request.query, response, model_name, elapsed_ms)
        return {"response": response, "model": model_name, "elapsed_ms": round(elapsed_ms, 2)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"JTAGENT unavailable: {type(exc).__name__}: {exc}") from exc
