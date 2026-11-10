#!/usr/bin/env bash
# Download VLM models into ./models with curl (llama.cpp's -hf download is unreliable here).
# Usage: scripts/fetch-models.sh [qwen|gemma|all]   (default: qwen)
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p models
HF=https://huggingface.co

get() {  # get <repo> <file> [dest-name]
  local repo=$1 file=$2 dest=${3:-$2}
  if [ -s "models/$dest" ]; then echo "ok (cached): $dest"; return; fi
  echo "downloading: $dest"
  curl -fL --retry 3 -C - -o "models/$dest.part" "$HF/$repo/resolve/main/$file"
  mv "models/$dest.part" "models/$dest"
}

case "${1:-qwen}" in
  qwen|all)
    get ggml-org/Qwen2.5-VL-3B-Instruct-GGUF Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf
    get ggml-org/Qwen2.5-VL-3B-Instruct-GGUF mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf
    ;;&
  gemma|all)
    get ggml-org/gemma-3-4b-it-GGUF gemma-3-4b-it-Q4_K_M.gguf
    get ggml-org/gemma-3-4b-it-GGUF mmproj-model-f16.gguf mmproj-gemma-3-4b-it-f16.gguf
    ;;&
  qwen|gemma|all) ;;
  *) echo "usage: $0 [qwen|gemma|all]"; exit 1 ;;
esac
ls -lh models
