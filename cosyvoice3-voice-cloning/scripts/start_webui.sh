#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-8000}"
CODE_DIR="${CODE_DIR:-/mnt/e/AI_Models/CosyVoice_code}"
MODEL_DIR="${MODEL_DIR:-/mnt/e/AI_Models/Fun-CosyVoice3-0.5B-2512}"
PYTHON_BIN="${PYTHON_BIN:-/mnt/e/AI_Models/miniconda_envs/cosyvoice310/bin/python3}"

cd "$CODE_DIR"
exec "$PYTHON_BIN" -u webui.py --port "$PORT" --model_dir "$MODEL_DIR"

