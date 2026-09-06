"""eka_agent_server auth gate: verify_api_key.

verify_api_key is the single guard in front of every data endpoint, so its
three cases (match / mismatch / missing) are worth pinning down directly. The
function is called as a plain function here — the DB-backed endpoints are out of
scope for this suite (they'd need a fake Postgres).
"""
import pytest
from fastapi import HTTPException

import eka_agent_server as server


@pytest.fixture(autouse=True)
def fixed_api_key(monkeypatch):
    monkeypatch.setattr(server, "API_KEY", "secret-key")


def test_valid_key_returns_true():
    assert server.verify_api_key("secret-key") is True


def test_invalid_key_raises_401():
    with pytest.raises(HTTPException) as exc:
        server.verify_api_key("wrong-key")
    assert exc.value.status_code == 401


def test_missing_key_raises_401():
    with pytest.raises(HTTPException) as exc:
        server.verify_api_key(None)
    assert exc.value.status_code == 401
