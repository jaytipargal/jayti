# EKA Agent — Device Client Installation Guide

## S24 (Termux) Installation

The S24 Ultra is currently factory-reset (2026-07-21) and last alive 2026-08-15.
Once Termux is reinstalled, run these commands on the phone:

```bash
# 1. Install curl
pkg install curl -y

# 2. Download the client
curl -o ~/eka_client.sh https://agent.urgaa.in/eka_client.sh

# 3. Make executable
chmod +x ~/eka_client.sh

# 4. Add to PATH (optional — add to ~/.bashrc)
echo 'export PATH=$PATH:~/EKA_AGENT_URL=https://agent.urgaa.in' >> ~/.bashrc
source ~/.bashrc

# 5. Test
~/eka_client.sh "What data was found?"
```

## Any Other Device (Linux/macOS/WSL)

```bash
# 1. Download
curl -o ~/eka_client.sh https://agent.urgaa.in/eka_client.sh
chmod +x ~/eka_client.sh

# 2. Query
~/eka_client.sh "your question here"

# 3. Options
~/eka_client.sh "question" --no-rag       # skip RAG retrieval
~/eka_client.sh "question" --raw          # direct LLM, no RAG
~/eka_client.sh "question" --stream       # streaming response
~/eka_client.sh "question" --search       # search only, no LLM
~/eka_client.sh "question" --top-k 10     # more retrieval results
~/eka_client.sh "question" --json         # raw JSON output
```

## Public API Endpoints

| Endpoint | URL | Description |
|----------|-----|-------------|
| Agent Health | `https://agent.urgaa.in/agent/health` | Server status |
| Agent Query | `https://agent.urgaa.in/agent/query` | RAG + Claude LLM |
| Agent Stream | `https://agent.urgaa.in/agent/query/stream` | Streaming SSE |
| Agent Raw | `https://agent.urgaa.in/agent/raw` | Direct LLM (no RAG) |
| Retrieval Health | `https://agent.urgaa.in/retrieval/health` | FAISS index status |
| Retrieval Search | `https://agent.urgaa.in/retrieval/search` | Semantic search |
| Retrieval Augment | `https://agent.urgaa.in/retrieval/augment` | RAG context formatting |
| Client Script | `https://agent.urgaa.in/eka_client.sh` | Download client |

## curl Example (no client script needed)

```bash
curl -X POST https://agent.urgaa.in/agent/query \
  -H "Content-Type: application/json" \
  -d '{"query": "What data was found?", "top_k": 5, "max_tokens": 512}'
```

## Infrastructure

- **VPS:** Vultr 139.84.165.81 (Ubuntu 22.04, 2 CPU, 3.8GB RAM)
- **FAISS Index:** 1,149,950 vectors, 384-dim, all-MiniLM-L6-v2
- **LLM:** Claude Sonnet 4-6 (Anthropic API)
- **Knowledge Base:** 1.15M chunks from forensic extraction artifacts
- **Services:** systemd-managed, auto-restart on failure
- **HTTPS:** Let's Encrypt via nginx