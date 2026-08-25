"""compute_hash — content fingerprinting used for dedup across the pipeline.

The function is duplicated verbatim in eka_agent_push and eka_agent_pull; this
suite exercises both so the two copies can't silently drift apart.
"""
import hashlib
import json

import pytest

import eka_agent_push
import eka_agent_pull

MODULES = [eka_agent_push, eka_agent_pull]
IDS = ["push", "pull"]


@pytest.fixture(params=MODULES, ids=IDS)
def compute_hash(request):
    return request.param.compute_hash


def test_matches_reference_sha256(compute_hash):
    content = {"filename": "a.txt", "size": 10}
    expected = hashlib.sha256(
        json.dumps(content, sort_keys=True, default=str).encode()
    ).hexdigest()
    assert compute_hash(content) == expected


def test_is_deterministic(compute_hash):
    content = {"x": 1, "y": [1, 2, 3]}
    assert compute_hash(content) == compute_hash(content)


def test_independent_of_key_order(compute_hash):
    # sort_keys=True means insertion order must not affect the digest.
    a = {"a": 1, "b": 2, "c": 3}
    b = {"c": 3, "b": 2, "a": 1}
    assert compute_hash(a) == compute_hash(b)


def test_distinct_content_distinct_hash(compute_hash):
    assert compute_hash({"a": 1}) != compute_hash({"a": 2})


def test_returns_64_hex_chars(compute_hash):
    digest = compute_hash({"any": "thing"})
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_handles_non_json_native_values(compute_hash):
    # default=str lets otherwise-unserialisable values (sets) hash instead of raising.
    assert isinstance(compute_hash({"s": {1, 2, 3}}), str)


def test_two_implementations_agree():
    # The push and pull copies must produce identical digests for the same input.
    content = {"device": "s24", "nested": {"k": "v"}, "n": 42}
    assert eka_agent_push.compute_hash(content) == eka_agent_pull.compute_hash(content)
