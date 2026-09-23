#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: $0 /path/to/jt-agent.gguf [prompt]"
  exit 1
fi

MODEL_PATH="$1"
PROMPT="${2:-You are jtagent on Samsung S24. Summarize latest local context.}"

# Example assumes llama-cli is installed from llama.cpp Termux build.
llama-cli \
  -m "$MODEL_PATH" \
  -c 4096 \
  -n 256 \
  --temp 0.2 \
  --top-p 0.9 \
  -p "$PROMPT"
