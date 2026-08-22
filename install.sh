#!/bin/bash
# ============================================================
# EKA Agent — Universal Installer (one-liner)
# Works on: Termux (Android), Linux, macOS, WSL, any Unix
# ============================================================
# Usage:
#   curl -sL https://agent.urgaa.in/install.sh | bash
# ============================================================

set -e

INSTALL_DIR="${HOME}"
URL="https://agent.urgaa.in"

echo "=== EKA Agent Installer ==="
echo ""

# Check for curl
if ! command -v curl &> /dev/null; then
    echo "ERROR: curl not found. Install it first:"
    echo "  Termux:  pkg install curl"
    echo "  Ubuntu:  sudo apt install curl"
    echo "  macOS:   brew install curl"
    exit 1
fi

# Download client
echo "Downloading eka client to ${INSTALL_DIR}/eka..."
curl -sL -o "${INSTALL_DIR}/eka" "${URL}/eka_client.sh"
chmod +x "${INSTALL_DIR}/eka"

# Add to PATH if not already
if ! echo "$PATH" | grep -q "${INSTALL_DIR}"; then
    SHELL_RC="${HOME}/.bashrc"
    if [ -f "${HOME}/.zshrc" ]; then
        SHELL_RC="${HOME}/.zshrc"
    fi
    echo "export PATH=\$PATH:${INSTALL_DIR}" >> "$SHELL_RC"
    echo "Added ${INSTALL_DIR} to PATH via $SHELL_RC"
fi

# Set default URL
echo "export EKA_AGENT_URL=\"${URL}\"" >> "${HOME}/.bashrc" 2>/dev/null || true

echo ""
echo "✅ Installed! Test with:"
echo "  ${INSTALL_DIR}/eka 'What data was found?'"
echo ""
echo "Or restart shell and use:"
echo "  eka 'your question'"
echo ""
echo "Options:"
echo "  eka 'query' --json       Raw JSON output"
echo "  eka 'query' --raw        No RAG, direct LLM"
echo "  eka 'query' --search     Search only"
echo "  eka 'query' --stream     Streaming response"
echo "  eka 'query' --top-k 10   More results"