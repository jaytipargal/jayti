#!/bin/bash
# ============================================================
# EKA Agent — Lightweight Client (curl-based)
# For Termux (Android), Linux, macOS, WSL, or any device with curl
# ============================================================
# Usage:
#   ./eka_client.sh "your query here"
#   ./eka_client.sh "your query" --no-rag      # skip RAG
#   ./eka_client.sh "your query" --stream      # streaming response
#   ./eka_client.sh "your query" --raw         # no RAG, direct LLM
#   ./eka_client.sh "your query" --search      # search only, no LLM
#
# Setup:
#   export EKA_AGENT_URL="https://api.eka-ai.in"
#   # or default: http://139.84.165.81
#
# Install on Termux:
#   pkg install curl
#   curl -o ~/eka_client.sh https://api.eka-ai.in/eka_client.sh
#   chmod +x ~/eka_client.sh
#   echo 'export EKA_AGENT_URL="https://api.eka-ai.in"' >> ~/.bashrc
# ============================================================

EKA_AGENT_URL="${EKA_AGENT_URL:-http://139.84.165.81}"

if [ $# -lt 1 ]; then
    echo "EKA Agent Client"
    echo ""
    echo "Usage: eka_client.sh \"your query\" [options]"
    echo ""
    echo "Options:"
    echo "  --no-rag    Skip RAG retrieval (still uses LLM)"
    echo "  --raw       Direct LLM query, no RAG context"
    echo "  --stream    Stream response via SSE"
    echo "  --search    Search only, return chunks without LLM"
    echo "  --top-k N   Number of retrieval results (default: 5)"
    echo "  --json      Output raw JSON response"
    echo ""
    echo "Examples:"
    echo "  eka_client.sh \"What data was found on Jayti's phone?\""
    echo "  eka_client.sh \"WhatsApp chats about SPC Leathers\" --top-k 10"
    echo "  eka_client.sh \"quick question\" --raw"
    echo "  eka_client.sh \"long analysis\" --stream"
    exit 1
fi

QUERY=""
USE_RAG="true"
RAW_MODE=false
STREAM_MODE=false
SEARCH_ONLY=false
TOP_K=5
JSON_OUTPUT=false

# Parse arguments
for arg in "$@"; do
    case "$arg" in
        --no-rag)  USE_RAG="false";;
        --raw)     RAW_MODE=true; USE_RAG="false";;
        --stream)  STREAM_MODE=true;;
        --search)  SEARCH_ONLY=true;;
        --top-k)   shift_next=true;;
        --json)    JSON_OUTPUT=true;;
        *)
            if [ "$shift_next" = true ]; then
                TOP_K="$arg"
                shift_next=false
            else
                QUERY="$arg"
            fi
            ;;
    esac
done

if [ -z "$QUERY" ]; then
    echo "Error: No query provided"
    exit 1
fi

# ── Search-only mode (retrieval server directly) ──
if [ "$SEARCH_ONLY" = true ]; then
    if [ "$JSON_OUTPUT" = true ]; then
        curl -s -X POST "${EKA_AGENT_URL}:8100/search" \
            -H "Content-Type: application/json" \
            -d "{\"query\": \"${QUERY}\", \"top_k\": ${TOP_K}}"
    else
        curl -s -X POST "${EKA_AGENT_URL}:8100/search" \
            -H "Content-Type: application/json" \
            -d "{\"query\": \"${QUERY}\", \"top_k\": ${TOP_K}}" | \
            python3 -c "
import sys, json
data = json.load(sys.stdin)
print(f'Query: {data[\"query\"]}')
print(f'Total hits: {data[\"total\"]} ({data[\"search_time_ms\"]}ms)')
print('---')
for i, hit in enumerate(data['hits'], 1):
    print(f'[{i}] Score: {hit[\"score\"]:.4f} | ID: {hit[\"id\"]}')
    meta = hit.get('metadata', {})
    if meta:
        for k, v in meta.items():
            print(f'    {k}: {v}')
    doc = hit['document'][:500]
    print(f'    Text: {doc}...' if len(hit['document']) > 500 else f'    Text: {doc}')
    print()
" 2>/dev/null || curl -s -X POST "${EKA_AGENT_URL}:8100/search" \
            -H "Content-Type: application/json" \
            -d "{\"query\": \"${QUERY}\", \"top_k\": ${TOP_K}}"
    fi
    exit 0
fi

# ── Build request ──
PAYLOAD="{\"query\": \"${QUERY}\", \"top_k\": ${TOP_K}, \"use_rag\": ${USE_RAG}}"

# ── Stream mode ──
if [ "$STREAM_MODE" = true ]; then
    curl -N -X POST "${EKA_AGENT_URL}:8000/query/stream" \
        -H "Content-Type: application/json" \
        -d "${PAYLOAD}" 2>/dev/null | while IFS= read -r line; do
        if echo "$line" | grep -q '^data: '; then
            echo "$line" | sed 's/^data: //' | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if 'text' in d:
        print(d['text'], end='', flush=True)
    elif 'done' in d:
        print()
except: pass
" 2>/dev/null
        fi
    done
    exit 0
fi

# ── Raw mode ──
if [ "$RAW_MODE" = true ]; then
    ENDPOINT="${EKA_AGENT_URL}:8000/raw"
else
    ENDPOINT="${EKA_AGENT_URL}:8000/query"
fi

# ── Normal query ──
if [ "$JSON_OUTPUT" = true ]; then
    curl -s -X POST "$ENDPOINT" \
        -H "Content-Type: application/json" \
        -d "${PAYLOAD}"
else
    RESPONSE=$(curl -s -X POST "$ENDPOINT" \
        -H "Content-Type: application/json" \
        -d "${PAYLOAD}")

    echo "$RESPONSE" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print('=' * 60)
    print(f'Query: {data[\"query\"]}')
    print(f'Sources: {data[\"sources\"]} | Retrieval: {data[\"retrieval_time_ms\"]}ms | Gen: {data[\"generation_time_ms\"]}ms')
    print(f'Model: {data.get(\"model\", \"N/A\")}')
    print('=' * 60)
    print()
    print(data['response'])
    print()
    print(f'Total time: {data[\"total_time_ms\"]}ms')
except Exception as e:
    print(f'Error parsing response: {e}')
    print(sys.stdin.read())
" 2>/dev/null || echo "$RESPONSE"
fi