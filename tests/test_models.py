"""Pydantic request-model validation across the FastAPI services.

These bounds are the servers' first line of input validation, so they deserve
explicit tests: they enforce the API contract before any handler runs.
"""
import pytest
from pydantic import ValidationError

import eka_agent_cloud
import eka_agent_deploy
import eka_retrieval_server_vps as retrieval
import eka_agent_server


# ── eka_agent_cloud.QueryRequest ────────────────────────────────────────
def test_cloud_query_defaults():
    q = eka_agent_cloud.QueryRequest(query="hi")
    assert q.top_k == 5 and q.max_tokens == 1024 and q.use_rag is True


@pytest.mark.parametrize("field,value", [
    ("top_k", 0), ("top_k", 21),
    ("max_tokens", 31), ("max_tokens", 8193),
    ("temperature", -0.1), ("temperature", 2.1),
])
def test_cloud_query_rejects_out_of_bounds(field, value):
    with pytest.raises(ValidationError):
        eka_agent_cloud.QueryRequest(query="hi", **{field: value})


def test_cloud_query_requires_query():
    with pytest.raises(ValidationError):
        eka_agent_cloud.QueryRequest()


# ── eka_agent_deploy.QueryRequest (max_tokens caps at 2048) ──────────────
def test_deploy_query_max_tokens_upper_bound():
    eka_agent_deploy.QueryRequest(query="hi", max_tokens=2048)  # ok
    with pytest.raises(ValidationError):
        eka_agent_deploy.QueryRequest(query="hi", max_tokens=2049)


# ── retrieval SearchRequest / AugmentRequest ────────────────────────────
def test_search_request_top_k_bounds():
    retrieval.SearchRequest(query="q", top_k=50)  # ok (search allows up to 50)
    with pytest.raises(ValidationError):
        retrieval.SearchRequest(query="q", top_k=51)


def test_search_request_score_threshold_bounds():
    retrieval.SearchRequest(query="q", score_threshold=1.0)  # ok
    with pytest.raises(ValidationError):
        retrieval.SearchRequest(query="q", score_threshold=1.5)


def test_augment_request_top_k_capped_at_20():
    retrieval.AugmentRequest(query="q", top_k=20)  # ok
    with pytest.raises(ValidationError):
        retrieval.AugmentRequest(query="q", top_k=21)


# ── eka_agent_server ingest models (required fields) ─────────────────────
def test_ingest_item_requires_core_fields():
    with pytest.raises(ValidationError):
        eka_agent_server.IngestItem(device="d")  # missing source/data_type/content/device_time


def test_ingest_item_accepts_minimal_valid():
    item = eka_agent_server.IngestItem(
        device="d", source="s", data_type="file_change",
        content={"a": 1}, device_time="2026-08-22T00:00:00Z",
    )
    assert item.content_hash is None  # optional, defaults to None


def test_device_register_defaults_apps_empty():
    dev = eka_agent_server.DeviceRegister(
        device_id="id", device_name="n", device_type="phone", os="android",
    )
    assert dev.apps == [] and dev.credentials == []
