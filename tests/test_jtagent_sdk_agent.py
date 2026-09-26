"""Offline tests for sandbox/jtagent/jtagent_sdk_agent.py.

The real claude_agent_sdk must never be called here, so a minimal stand-in is
injected before import: `tool` returns the wrapped coroutine unchanged (so the
retrieval logic is directly awaitable) and ClaudeAgentOptions just records its
kwargs. The corpus glob is pointed at a tmp dir so the real 168k-row corpus on a
Colab box is never read.
"""
import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1] / "sandbox" / "jtagent" / "jtagent_sdk_agent.py"


class _Options:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


@pytest.fixture
def agent(monkeypatch, tmp_path):
    sdk = types.ModuleType("claude_agent_sdk")
    sdk.tool = lambda name, description, schema: (lambda fn: fn)
    sdk.create_sdk_mcp_server = lambda name, version="1.0.0", tools=None: {"name": name, "tools": tools}
    sdk.ClaudeAgentOptions = _Options
    for name in ("AssistantMessage", "ResultMessage", "TextBlock"):
        setattr(sdk, name, type(name, (), {}))
    sdk.query = None
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    try:
        import anyio  # noqa: F401
    except ImportError:
        monkeypatch.setitem(sys.modules, "anyio", types.ModuleType("anyio"))

    spec = importlib.util.spec_from_file_location("jtagent_sdk_agent_under_test", AGENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    corpus = tmp_path / "training"
    monkeypatch.setattr(mod, "_corpus_globs", lambda: [str(corpus / "**" / "*.jsonl")])
    mod.test_corpus = corpus
    return mod


def _write(corpus, category, rows):
    d = corpus / category
    d.mkdir(parents=True, exist_ok=True)
    (d / "part.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _search(mod, **args):
    return asyncio.run(mod.search_personal_context(args))["content"][0]["text"]


def test_options_lock_agent_to_single_retrieval_tool(agent):
    opts = agent.build_options()
    # allowed_tools alone only auto-approves; these are what actually restrict.
    assert opts.tools == []
    assert opts.strict_mcp_config is True
    assert opts.setting_sources == []
    assert {"Bash", "Read", "Write", "Edit", "WebFetch"} <= set(opts.disallowed_tools)
    assert getattr(opts, "permission_mode", None) is None
    assert opts.allowed_tools == ["mcp__jtagent__search_personal_context"]
    assert list(opts.mcp_servers) == ["jtagent"]
    assert opts.model == agent.MODEL
    assert "never claim a device is online without evidence" in opts.system_prompt


def test_real_sdk_cli_flags_remove_builtin_tools():
    """Check the command the real SDK would launch, not just the options object.

    Skipped where the SDK or the Claude Code CLI isn't installed (e.g. CI).
    """
    import shutil

    pytest.importorskip("claude_agent_sdk")
    cli = shutil.which("claude")
    if cli is None:
        pytest.skip("Claude Code CLI not installed")
    try:
        from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
    except ImportError:
        pytest.skip("SDK transport layout changed")
    spec = importlib.util.spec_from_file_location("jtagent_sdk_agent_real", AGENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    transport = SubprocessCLITransport(prompt="hi", options=mod.build_options())
    transport._cli_path = cli
    cmd = transport._build_command()
    assert cmd[cmd.index("--tools") + 1] == ""               # no built-in tools at all
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__jtagent__search_personal_context"
    assert "--strict-mcp-config" in cmd
    assert "--setting-sources=" in cmd                         # no device settings/hooks
    assert "bypassPermissions" not in cmd


def test_search_ranks_matches_and_skips_pad_rows(agent):
    _write(agent.test_corpus, "devices", [
        {"category": "pad", "input": "vivobook vivobook vivobook", "output": "pad"},
        {"category": "devices", "input": "Which laptop runs Ollama?",
         "output": "The ASUS VivoBook serves the Q4_K_M GGUF via Ollama."},
        {"category": "devices", "input": "Phone?", "output": "Samsung S24 in Termux."},
    ])
    text = _search(agent, query="vivobook ollama", limit=5)
    # the pad row would also match "vivobook" if it were not skipped
    assert text.startswith("Top 1 matches for 'vivobook ollama'")
    assert "[devices]" in text and "VivoBook serves" in text


def test_search_coerces_structured_output(agent):
    _write(agent.test_corpus, "infrastructure", [
        {"category": "infrastructure", "input": "VPS ports?", "output": {"ufw": [22, 80, 443]}},
    ])
    assert '"ufw": [22, 80, 443]' in _search(agent, query="vps ports")


def test_search_reports_unsynced_corpus_instead_of_guessing(agent):
    assert "not synced" in _search(agent, query="anything here")


def test_search_rejects_empty_query(agent):
    assert _search(agent, query="   ") == "empty query"


def test_search_no_match(agent):
    _write(agent.test_corpus, "devices", [{"category": "devices", "input": "Phone?", "output": "S24"}])
    assert _search(agent, query="zzzqqq") == "No matches for: zzzqqq"


def test_main_requires_anthropic_key(agent, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert agent.main() == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
