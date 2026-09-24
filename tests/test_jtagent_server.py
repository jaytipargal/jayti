"""Contract tests for server/jayti-agent/jtagent_server.py (offline).

The dashboard (jayti-dashboard, Backend/jp-live-agent.js) POSTs
{query, top_k, use_rag} with X-API-Key to ${AGENT_API_URL}/query and renders
`response`, `sources`, `total_time_ms`; GET /health renders `model` and
`retrieval_available`. These tests pin that contract with retrieval, the
generator and the Hub stubbed out.
"""
import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVER = Path(__file__).resolve().parent.parent / "server" / "jayti-agent" / "jtagent_server.py"


@pytest.fixture()
def agent(tmp_path, monkeypatch):
    key_file = tmp_path / "eka-agent-api-key.conf"
    key_file.write_text('map $http_x_api_key $eka_agent_key_ok {\n default 0;\n "k-test-123" 1;\n}\n')
    monkeypatch.setenv("JTAGENT_NGINX_KEY_FILE", str(key_file))
    monkeypatch.delenv("JTAGENT_API_KEY", raising=False)
    monkeypatch.setenv("JAYTI_BOOTSTRAP_KEY", "boot")
    monkeypatch.setenv("JTAGENT_GENERATE", "0")
    spec = importlib.util.spec_from_file_location("jtagent_server_under_test", SERVER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    hits = [
        {"score": 0.91, "index_id": 7, "doc_id": "doc-7", "metadata": {"source": "jayti_reports"},
         "document": "Go4Garage services brake pads in Pune.\nSecond line."},
        {"score": 0.80, "index_id": 9, "doc_id": "doc-9", "metadata": None, "document": "Another record"},
    ]
    calls = {"retrieve": [], "state": 0}

    def fake_retrieve(q, k):
        calls["retrieve"].append((q, k))
        return hits

    async def fake_state():
        calls["state"] += 1
        return {"hub": "http://hub", "status": {"ok": True, "devices_active": 4, "items_total": 904,
                                                "items_24h": 904, "items_unprocessed": 0},
                "devices": [{"device_id": "windows_pc_abcom", "device_type": "laptop", "os": "windows",
                             "is_active": True, "items": 901, "unprocessed": 0, "last_ingest": "2026-09-24T06:53:08"}]}

    monkeypatch.setattr(mod, "retrieve", fake_retrieve)
    monkeypatch.setattr(mod, "hub_state", fake_state)
    monkeypatch.setattr(mod, "retrieval_available", lambda: True)
    mod.calls = calls
    yield mod
    sys.modules.pop(spec.name, None)


def test_api_key_comes_from_nginx_map(agent):
    assert agent.API_KEY == "k-test-123"


def test_query_requires_key(agent):
    c = TestClient(agent.app)
    assert c.post("/query", json={"query": "x"}).status_code == 401
    assert c.post("/query", json={"query": "x"}, headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.get("/state").status_code == 401


def test_health_contract(agent):
    r = TestClient(agent.app).get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["retrieval_available"] is True
    assert body["auth_configured"] is True
    assert body["model"].startswith("jtagent:")


def test_grounded_query_cites_sources(agent):
    c = TestClient(agent.app)
    r = c.post("/query", json={"query": "where does go4garage service brakes", "top_k": 2},
               headers={"X-API-Key": "k-test-123"})
    assert r.status_code == 200
    body = r.json()
    assert body["sources"] == 2
    assert body["source_docs"][0]["doc_id"] == "doc-7"
    assert "[1]" in body["response"] and "doc doc-7" in body["response"]
    assert "Pune" in body["response"]
    assert body["mode"] == "extractive"
    assert body["model"] == "jtagent:extractive"
    assert isinstance(body["total_time_ms"], float)
    assert agent.calls["retrieve"] == [("where does go4garage service brakes", 2)]
    assert agent.calls["state"] == 0


def test_state_intent_reports_hub_and_devices(agent):
    c = TestClient(agent.app)
    r = c.post("/query", json={"query": "what is the hub status and device rows?"},
               headers={"X-API-Key": "k-test-123"})
    body = r.json()
    assert agent.calls["state"] == 1
    assert body["state"]["status"]["devices_active"] == 4
    assert "devices_active=4" in body["response"]
    assert "windows_pc_abcom" in body["response"] and "901 rows" in body["response"]


def test_question_alias_and_empty_query(agent):
    c = TestClient(agent.app)
    assert c.post("/query", json={"question": "brakes"}, headers={"X-API-Key": "k-test-123"}).status_code == 200
    assert c.post("/query", json={"query": "  "}, headers={"X-API-Key": "k-test-123"}).status_code == 400


def test_retrieval_failure_is_reported_not_fatal(agent, monkeypatch):
    def boom(q, k):
        raise RuntimeError("retrieval down")
    monkeypatch.setattr(agent, "retrieve", boom)
    r = TestClient(agent.app).post("/query", json={"query": "brakes"}, headers={"X-API-Key": "k-test-123"})
    assert r.status_code == 200
    assert r.json()["sources"] == 0
    assert "retrieval down" in r.json()["retrieval_error"]
