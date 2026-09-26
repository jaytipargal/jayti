#!/usr/bin/env python3
"""jtagent as a Claude Agent SDK agent (Colab / VPS / devices).

Claude-backed (``claude-sonnet-5`` by default) and locked to a single custom
tool, ``search_personal_context``, over Jayti's **redacted** training corpus
(``training/<category>/*.jsonl`` from segment_jsonl). No Bash/Read/Write, so it
can only answer from retrieved personal context. This complements the local
GGUF path (export_gguf -> Ollama / llama.cpp); it does not replace it.

Requirements:
  - ``pip install claude-agent-sdk anyio``
  - Node.js + the Claude Code CLI (``npm i -g @anthropic-ai/claude-code``);
    the SDK spawns it.
  - ``ANTHROPIC_API_KEY`` (an ``sk-ant-...`` key). This is NOT the EKA_* keys
    from device.env; those are VPS-hub push creds for a different service.
  - Optional: ``JTAGENT_CORPUS_DIR`` pointing at a ``training/`` directory. If no
    corpus is found the agent says so instead of guessing.

Run:
  export ANTHROPIC_API_KEY=sk-ant-...
  python jtagent_sdk_agent.py "what devices are registered for jtagent?"
"""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

try:
    import anyio
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        create_sdk_mcp_server,
        query,
        tool,
    )
except ImportError:  # pragma: no cover - surfaced at runtime
    sys.stderr.write(
        "Missing deps. Install with: pip install claude-agent-sdk anyio\n"
        "and: npm i -g @anthropic-ai/claude-code (needs Node.js)\n"
    )
    raise

MODEL = os.environ.get("JTAGENT_MODEL", "claude-sonnet-5")

SYSTEM_PROMPT = (
    "You are jtagent, Jayti's personal assistant. Answer from the provided "
    "personal context accurately and concisely. Always call the "
    "search_personal_context tool to retrieve relevant data before answering a "
    "factual question about Jayti's accounts, devices, chats or files. Keep "
    "secrets redacted and never claim a device is online without evidence. If "
    "the retrieved context does not contain the answer, say so plainly instead "
    "of guessing."
)


def _corpus_globs() -> list[str]:
    """Redacted training-corpus locations, most specific first."""
    env_dir = os.environ.get("JTAGENT_CORPUS_DIR", "").strip()
    globs = [os.path.join(env_dir, "**", "*.jsonl")] if env_dir else []
    home = os.path.expanduser("~")
    globs += [
        "/content/TAN/jtagent/training/**/*.jsonl",
        "/content/drive/MyDrive/TAN /jtagent/training/**/*.jsonl",
        os.path.join(home, "jtagent", "training", "**", "*.jsonl"),
        os.path.join(home, "storage", "shared", "TAN", "jtagent", "training", "**", "*.jsonl"),
        os.path.join(os.getcwd(), "data", "training", "**", "*.jsonl"),
    ]
    return globs


def _iter_corpus_rows():
    seen = set()
    for pat in _corpus_globs():
        for fp in glob.glob(pat, recursive=True):
            if fp in seen or fp.endswith(".bak"):
                continue
            seen.add(fp)
            try:
                for line in Path(fp).read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("category") == "pad":
                        continue
                    yield row
            except OSError:
                continue


@tool(
    "search_personal_context",
    "Search Jayti's redacted personal-data corpus (chats, browser, devices, "
    "infra) for rows relevant to the query. Returns the top keyword matches.",
    {"query": str, "limit": int},
)
async def search_personal_context(args: dict) -> dict:
    q = str(args.get("query", "")).strip().lower()
    limit = int(args.get("limit") or 8)
    if not q:
        return {"content": [{"type": "text", "text": "empty query"}]}
    terms = [t for t in q.split() if len(t) > 2]
    scored = []
    have_any = False
    for row in _iter_corpus_rows():
        have_any = True
        instr = str(row.get("input") or row.get("instruction") or "")
        out = row.get("output", "")
        if not isinstance(out, str):
            out = json.dumps(out, ensure_ascii=False, default=str)
        hay = f"{instr}\n{out}".lower()
        score = sum(hay.count(t) for t in terms)
        if score:
            scored.append((score, row.get("category", "?"), instr[:300], out[:600]))
    if not have_any:
        return {"content": [{"type": "text", "text": (
            "Personal corpus is not synced here (no training/*.jsonl found). Set "
            "JTAGENT_CORPUS_DIR or sync TAN/jtagent/training. Answering without "
            "personal context."
        )}]}
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        return {"content": [{"type": "text", "text": f"No matches for: {q}"}]}
    lines = [f"Top {min(limit, len(scored))} matches for '{q}':\n"]
    for _score, cat, instr, out in scored[:limit]:
        lines.append(f"[{cat}] Q: {instr}\n   A: {out}\n")
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


# Built-in tools denied as a second line of defence; `tools=[]` is what removes them.
DENIED_BUILTIN_TOOLS = [
    "Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit",
    "Glob", "Grep", "WebSearch", "WebFetch", "Task", "Agent",
]


def build_options() -> ClaudeAgentOptions:
    """Options that confine the agent to its one retrieval tool.

    `allowed_tools` only auto-approves a tool; it does not restrict the tool set.
    The restriction comes from `tools=[]` (the CLI gets `--tools ""`, so no
    built-in tool is available), `strict_mcp_config` (only this in-process
    server, not MCP servers configured on the device) and `setting_sources=[]`
    (no user/project/local settings, so no device hooks or allow rules). No
    permission bypass is needed because the only tool is pre-approved.
    """
    server = create_sdk_mcp_server("jtagent", "1.0.0", tools=[search_personal_context])
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=MODEL,
        mcp_servers={"jtagent": server},
        strict_mcp_config=True,
        setting_sources=[],
        tools=[],
        allowed_tools=["mcp__jtagent__search_personal_context"],
        disallowed_tools=DENIED_BUILTIN_TOOLS,
        max_turns=8,
    )


async def ask(prompt: str) -> None:
    async for msg in query(prompt=prompt, options=build_options()):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    print(block.text, flush=True)
        elif isinstance(msg, ResultMessage):
            cost = getattr(msg, "total_cost_usd", None)
            if cost is not None:
                print(f"\n[jtagent done · cost≈${cost:.4f}]", flush=True)


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.stderr.write("ERROR: set ANTHROPIC_API_KEY (sk-ant-...) first.\n")
        return 2
    prompt = " ".join(sys.argv[1:]).strip() or "Introduce yourself in one sentence as jtagent."
    anyio.run(ask, prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
