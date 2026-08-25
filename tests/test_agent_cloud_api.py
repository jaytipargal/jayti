"""eka_agent_cloud HTTP contract + SSE stream parser.

Endpoints are exercised via FastAPI's TestClient with the two network
boundaries (retrieve_context → retrieval server, call_claude → Anthropic API)
monkeypatched, so nothing leaves the process.
"""
import json

import pytest
from fastapi.testclient import TestClient

import eka_agent_cloud as agent


@pytest.fixture
def client():
    return TestClient(agent.app)


# ── /health ─────────────────────────────────────────────────────────────
def test_health_reports_retrieval_and_api_config(client, monkeypatch):
    monkeypatch.setattr(agent, "check_retrieval", lambda: True)
    monkeypatch.setattr(agent, "ANTHROPIC_API_KEY", "sk-test")
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["retrieval_available"] is True
    assert body["api_configured"] is True


def test_health_api_not_configured_when_key_blank(client, monkeypatch):
    monkeypatch.setattr(agent, "check_retrieval", lambda: False)
    monkeypatch.setattr(agent, "ANTHROPIC_API_KEY", "")
    body = client.get("/health").json()
    assert body["api_configured"] is False
    assert body["retrieval_available"] is False


# ── /query (RAG path) ───────────────────────────────────────────────────
def test_query_uses_retrieval_and_generation(client, monkeypatch):
    monkeypatch.setattr(agent, "retrieve_context",
                        lambda q, k: {"context": "some ctx", "sources": 3})
    monkeypatch.setattr(agent, "call_claude", lambda q, ctx, mt, temp: "the answer")
    body = client.post("/query", json={"query": "who?"}).json()
    assert body["response"] == "the answer"
    assert body["sources"] == 3
    assert body["model"] == agent.ANTHROPIC_MODEL
    assert body["total_time_ms"] >= 0


def test_query_degrades_when_retrieval_fails(client, monkeypatch):
    def _boom(q, k):
        raise RuntimeError("retrieval down")

    captured = {}

    def _capture(q, ctx, mt, temp):
        captured["ctx"] = ctx
        return "fallback answer"

    monkeypatch.setattr(agent, "retrieve_context", _boom)
    monkeypatch.setattr(agent, "call_claude", _capture)
    resp = client.post("/query", json={"query": "who?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["response"] == "fallback answer"
    assert body["sources"] == 0          # no RAG sources on failure
    assert captured["ctx"] is None        # generation proceeds without context


def test_query_skips_retrieval_when_use_rag_false(client, monkeypatch):
    def _fail(q, k):
        raise AssertionError("retrieval must not be called when use_rag=False")

    monkeypatch.setattr(agent, "retrieve_context", _fail)
    monkeypatch.setattr(agent, "call_claude", lambda q, ctx, mt, temp: "ok")
    body = client.post("/query", json={"query": "hi", "use_rag": False}).json()
    assert body["response"] == "ok"
    assert body["sources"] == 0


def test_query_rejects_out_of_bounds_top_k(client):
    resp = client.post("/query", json={"query": "hi", "top_k": 99})
    assert resp.status_code == 422  # Pydantic validation rejects before handler


# ── /raw (no RAG) ───────────────────────────────────────────────────────
def test_raw_query_bypasses_retrieval(client, monkeypatch):
    def _fail(q, k):
        raise AssertionError("retrieval must not be called on /raw")

    monkeypatch.setattr(agent, "retrieve_context", _fail)
    monkeypatch.setattr(agent, "call_claude", lambda q, ctx, mt, temp: "raw answer")
    body = client.post("/raw", json={"query": "hi"}).json()
    assert body["response"] == "raw answer"
    assert body["sources"] == 0
    assert body["retrieval_time_ms"] == 0.0


# ── SSE stream parser (call_claude_stream) ──────────────────────────────
def _sse(events):
    return "\n".join(f"data: {json.dumps(e)}" for e in events).encode("utf-8") + b"\n"


def test_stream_parser_yields_text_deltas(monkeypatch, fake_http_response):
    events = [
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " world"}},
        {"type": "message_stop"},
    ]
    payload = _sse(events)
    monkeypatch.setattr(agent.urllib.request, "urlopen",
                        lambda *a, **k: fake_http_response(b"", chunks=[payload, b""]))
    out = list(agent.call_claude_stream("q", None, 128, 0.7))
    assert "".join(out) == "Hello world"


def test_stream_parser_skips_malformed_lines(monkeypatch, fake_http_response):
    payload = (
        b"data: not-json-at-all\n"
        + _sse([
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}},
            {"type": "message_stop"},
        ])
    )
    monkeypatch.setattr(agent.urllib.request, "urlopen",
                        lambda *a, **k: fake_http_response(b"", chunks=[payload, b""]))
    out = list(agent.call_claude_stream("q", None, 128, 0.7))
    assert "".join(out) == "ok"  # malformed line skipped, valid delta still yielded


def test_stream_parser_stops_at_message_stop(monkeypatch, fake_http_response):
    payload = _sse([
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "A"}},
        {"type": "message_stop"},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "SHOULD-NOT-APPEAR"}},
    ])
    monkeypatch.setattr(agent.urllib.request, "urlopen",
                        lambda *a, **k: fake_http_response(b"", chunks=[payload, b""]))
    out = "".join(agent.call_claude_stream("q", None, 128, 0.7))
    assert out == "A"
