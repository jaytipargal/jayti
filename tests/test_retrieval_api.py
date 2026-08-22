"""FAISS retrieval servers: /search and /augment handler logic.

The handlers are called directly (not through TestClient) so the heavy startup
event — which would try to load a real model + index from disk — never runs.
The two module-global boundaries (embed_model, faiss_index, doc lookup) are
replaced with in-process fakes; this isolates the index-bounds filtering,
score-threshold filtering, and context-formatting logic that is the real value.
"""
import numpy as np
import pytest

import eka_retrieval_server_vps as vps
import eka_retrieval_server as local

MODULES = [vps, local]
IDS = ["vps", "local"]


class _FakeEmbed:
    def encode(self, queries, **kwargs):
        return np.zeros((len(queries), 4), dtype=np.float32)


class _FakeIndex:
    """Returns preset (scores, indices) regardless of the query vector."""

    def __init__(self, scores, indices, ntotal):
        self._scores = np.array([scores], dtype=np.float32)
        self._indices = np.array([indices], dtype=np.int64)
        self.ntotal = ntotal

    def search(self, emb, k):
        return self._scores[:, :k], self._indices[:, :k]


@pytest.fixture(params=MODULES, ids=IDS)
def mod(request, monkeypatch):
    m = request.param
    monkeypatch.setattr(m, "embed_model", _FakeEmbed())
    monkeypatch.setattr(
        m, "get_doc_by_index",
        lambda idx: {"id": f"d{idx}", "document": f"doc {idx}", "metadata": {"n": idx}, "line": idx},
    )
    return m


def _set_index(monkeypatch, m, scores, indices, ntotal, doc_count):
    monkeypatch.setattr(m, "faiss_index", _FakeIndex(scores, indices, ntotal))
    monkeypatch.setattr(m, "doc_count", doc_count)


# ── /search ─────────────────────────────────────────────────────────────
def test_search_returns_hits_for_valid_indices(mod, monkeypatch):
    _set_index(monkeypatch, mod, [0.9, 0.5, 0.1], [0, 1, 2], ntotal=3, doc_count=3)
    resp = mod.search(mod.SearchRequest(query="q", top_k=3))
    assert resp.total == 3
    assert [h.id for h in resp.hits] == ["d0", "d1", "d2"]
    assert resp.hits[0].score == pytest.approx(0.9)


def test_search_skips_out_of_bounds_indices(mod, monkeypatch):
    # -1 (FAISS "no match") and 99 (>= doc_count) must be dropped.
    _set_index(monkeypatch, mod, [0.9, 0.8, 0.7], [0, -1, 99], ntotal=3, doc_count=3)
    resp = mod.search(mod.SearchRequest(query="q", top_k=3))
    assert resp.total == 1
    assert resp.hits[0].id == "d0"


def test_search_applies_score_threshold(mod, monkeypatch):
    _set_index(monkeypatch, mod, [0.9, 0.5, 0.2], [0, 1, 2], ntotal=3, doc_count=3)
    resp = mod.search(mod.SearchRequest(query="q", top_k=3, score_threshold=0.6))
    assert resp.total == 1
    assert resp.hits[0].id == "d0"


# ── /augment ────────────────────────────────────────────────────────────
def test_augment_formats_numbered_context(mod, monkeypatch):
    _set_index(monkeypatch, mod, [0.9, 0.5], [0, 1], ntotal=2, doc_count=2)
    resp = mod.augment(mod.AugmentRequest(query="q", top_k=2))
    assert resp.sources == 2
    assert "[1] (score: 0.900)" in resp.context
    assert "[2] (score: 0.500)" in resp.context
    assert "doc 0" in resp.context
    assert "\n\n---\n\n" in resp.context  # chunk separator


def test_augment_fallback_when_no_hits(mod, monkeypatch):
    _set_index(monkeypatch, mod, [0.9, 0.8], [-1, -1], ntotal=2, doc_count=2)
    resp = mod.augment(mod.AugmentRequest(query="q", top_k=2))
    assert resp.sources == 0
    assert resp.context == "No relevant context found."
