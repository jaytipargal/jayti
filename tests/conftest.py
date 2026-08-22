"""Shared pytest fixtures and import-time dependency stubs for the EKA Agent suite.

The production modules import heavy/optional runtime dependencies at module
scope (``chromadb``, ``torch``, ``faiss``, ``sentence_transformers``,
``psycopg2``, …). None of that stack is needed to exercise the pure logic,
file-I/O helpers, or API contracts these tests target, so we install
lightweight stand-ins into ``sys.modules`` *before* any target module is
imported. This keeps the suite fully offline: no network, no database, no GPU,
no multi-hundred-MB ML wheels.

``numpy`` is the one heavy dependency we do NOT stub — the FAISS offset-index
logic genuinely round-trips ``np.int64`` arrays through ``tofile``/``fromfile``,
so a real numpy is required for those tests to mean anything.
"""
import sys
import types
from pathlib import Path

import pytest

# ── Make the target modules importable ──────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
for p in (REPO_ROOT, SCRIPTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


# ── Install stub modules for heavy/optional deps ────────────────────────
def _install_stub(name, **attrs):
    """Register a stub module (and any dotted submodules) in sys.modules."""
    if name in sys.modules:
        mod = sys.modules[name]
    else:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    for attr, value in attrs.items():
        setattr(mod, attr, value)
    return mod


class _Stub:
    """A permissive placeholder that swallows construction/attribute access.

    Any test that actually needs one of these boundaries to *behave* should
    monkeypatch the concrete attribute (e.g. the module-global ``faiss_index``)
    with its own fake rather than relying on this stub.
    """

    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return self

    def __getattr__(self, _name):
        return _Stub()


def _install_all_stubs():
    # chromadb (eka_vector_db)
    _install_stub("chromadb", PersistentClient=_Stub)

    # torch (eka_agent_deploy top-level, eka_train lazy)
    _install_stub("torch")

    # faiss (retrieval servers) — needs read_index() + IndexFlatIP type
    _install_stub("faiss", read_index=lambda *a, **k: _Stub(), IndexFlatIP=_Stub)

    # sentence_transformers (retrieval servers)
    _install_stub("sentence_transformers", SentenceTransformer=_Stub)

    # transformers / peft / datasets (eka_train + eka_agent_deploy, lazy)
    _install_stub(
        "transformers",
        AutoTokenizer=_Stub,
        AutoModelForCausalLM=_Stub,
        TrainingArguments=_Stub,
        Trainer=_Stub,
        TextIteratorStreamer=_Stub,
    )
    _install_stub("peft", LoraConfig=_Stub, get_peft_model=lambda *a, **k: _Stub(),
                  TaskType=_Stub, PeftModel=_Stub)
    _install_stub("datasets", Dataset=_Stub)

    # psycopg2 (eka_agent_server) — connect + errors.UniqueViolation + extras
    class _UniqueViolation(Exception):
        pass

    errors_mod = _install_stub("psycopg2.errors", UniqueViolation=_UniqueViolation)
    extras_mod = _install_stub("psycopg2.extras", RealDictCursor=_Stub, Json=lambda x: x)
    _install_stub(
        "psycopg2",
        connect=lambda *a, **k: _Stub(),
        errors=errors_mod,
        extras=extras_mod,
    )


_install_all_stubs()


# ── Shared data fixtures ────────────────────────────────────────────────
@pytest.fixture
def sample_chunk():
    """A representative training chunk as produced by the pull pipeline."""
    return {
        "id": "daily_2026-08-22_abc123def456",
        "title": "report.pdf",
        "category": "documents",
        "input": "What documents data was found on windows_pc_abcom?",
        "output": '{"content": {"filename": "report.pdf"}, "source_device": "windows_pc_abcom"}',
        "source_file": "windows_pc_abcom",
        "metadata": {
            "agent_visibility": "full",
            "priority": "P2",
            "source_device": "windows_pc_abcom",
            "daily_batch": "2026-08-22",
        },
    }


@pytest.fixture
def sample_item():
    """A representative ingested item as pulled from the VPS."""
    return {
        "id": 1,
        "device": "windows_pc_abcom",
        "source": "filesystem",
        "data_type": "file_change",
        "content": {"filename": "report.pdf", "path": "/Documents/report.pdf"},
        "device_time": "2026-08-22T10:00:00Z",
        "content_hash": None,
    }


class FakeHTTPResponse:
    """Minimal context-manager stand-in for urllib's urlopen() return value."""

    def __init__(self, payload: bytes, chunks=None):
        self._payload = payload
        self._chunks = chunks  # for streaming: list of byte blocks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size=-1):
        if self._chunks is not None:
            return self._chunks.pop(0) if self._chunks else b""
        data, self._payload = self._payload, b""
        return data


@pytest.fixture
def fake_http_response():
    """Factory for FakeHTTPResponse (single-shot or streaming)."""
    return FakeHTTPResponse
