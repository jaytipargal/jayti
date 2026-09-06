"""Prompt builders — both branch on the "No relevant context found." sentinel.

eka_agent_cloud.build_system_prompt  (Claude-based agent)
eka_agent_deploy.build_prompt        (local LoRA-based agent)
"""
import eka_agent_cloud
import eka_agent_deploy

NO_CONTEXT_SENTINEL = "No relevant context found."


# ── build_system_prompt (cloud agent) ───────────────────────────────────
def test_system_prompt_includes_context_when_present():
    prompt = eka_agent_cloud.build_system_prompt("chunk-1\nchunk-2")
    assert "chunk-1" in prompt
    assert "Knowledge Context:" in prompt
    assert "forensic intelligence assistant" in prompt


def test_system_prompt_omits_context_when_none():
    prompt = eka_agent_cloud.build_system_prompt(None)
    assert "Knowledge Context:" not in prompt
    assert "forensic intelligence assistant" in prompt


def test_system_prompt_omits_context_on_sentinel():
    # The retrieval "no results" sentinel must NOT be embedded as if it were context.
    prompt = eka_agent_cloud.build_system_prompt(NO_CONTEXT_SENTINEL)
    assert "Knowledge Context:" not in prompt


def test_system_prompt_omits_context_on_empty_string():
    prompt = eka_agent_cloud.build_system_prompt("")
    assert "Knowledge Context:" not in prompt


# ── build_prompt (local LoRA agent) ─────────────────────────────────────
def test_build_prompt_includes_context_when_present():
    prompt = eka_agent_deploy.build_prompt("who?", "some context")
    assert "some context" in prompt
    assert "Knowledge Context:" in prompt
    assert "who?" in prompt
    assert prompt.rstrip().endswith("### Response:")


def test_build_prompt_bare_when_no_context():
    prompt = eka_agent_deploy.build_prompt("who?", None)
    assert "Knowledge Context:" not in prompt
    assert "### Instruction:\nwho?" in prompt


def test_build_prompt_bare_on_sentinel():
    prompt = eka_agent_deploy.build_prompt("who?", NO_CONTEXT_SENTINEL)
    assert "Knowledge Context:" not in prompt
    assert "who?" in prompt
