#!/usr/bin/env bash
# Download a CPU build of llama.cpp and the Gemma 4 E2B (instruction-tuned, Q4_0) GGUF.
# Both go into this folder only (.tools/ and models/), no sudo, nothing global.
set -euo pipefail
cd "$(dirname "$0")/.."
LLAMA_TAG="${LLAMA_TAG:-b11555}"
MODEL_REPO="${MODEL_REPO:-ggml-org/gemma-4-E2B-it-GGUF}"
MODEL_FILE="${MODEL_FILE:-gemma-4-E2B-it-Q4_0.gguf}"

if [ ! -x ".tools/llama/llama-$LLAMA_TAG/llama-server" ]; then
  mkdir -p .tools/llama
  curl -fL -o .tools/llama.tgz \
    "https://github.com/ggml-org/llama.cpp/releases/download/$LLAMA_TAG/llama-$LLAMA_TAG-bin-ubuntu-x64.tar.gz"
  tar xzf .tools/llama.tgz -C .tools/llama
fi
mkdir -p models
if [ ! -s "models/$MODEL_FILE" ]; then
  curl -fL -o "models/$MODEL_FILE" "https://huggingface.co/$MODEL_REPO/resolve/main/$MODEL_FILE"
fi
ls -lh "models/$MODEL_FILE"
