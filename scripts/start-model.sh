#!/usr/bin/env bash
# Serve the local Gemma model on http://127.0.0.1:8088 (OpenAI-compatible API, CPU only).
set -euo pipefail
cd "$(dirname "$0")/.."
LLAMA_TAG="${LLAMA_TAG:-b11555}"
MODEL_FILE="${MODEL_FILE:-gemma-4-E2B-it-Q4_0.gguf}"
# Physical cores usually beat hyper-threads for llama.cpp on laptops (measured: see docs/llama-bench.txt).
THREADS="${THREADS:-$(( $(nproc) > 1 ? $(nproc) / 2 : 1 ))}"
exec ".tools/llama/llama-$LLAMA_TAG/llama-server" \
  -m "models/$MODEL_FILE" --host 127.0.0.1 --port "${PORT:-8088}" \
  -c 4096 -t "$THREADS" --jinja --reasoning off --no-webui
