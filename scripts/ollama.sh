#!/usr/bin/env bash
# Run the repository's official portable Ollama installation without sudo.
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
runtime="$project_root/.local/ollama/bin/ollama"
if [[ ! -x "$runtime" ]]; then
  printf '%s\n' "Ollama is missing. Run: uv run python scripts/install_ollama.py" >&2
  exit 1
fi
export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_MODELS="$project_root/data/ollama/models"
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_CONTEXT_LENGTH=8192
export OLLAMA_NO_CLOUD=1
mkdir -p "$OLLAMA_MODELS"
exec "$runtime" "$@"
