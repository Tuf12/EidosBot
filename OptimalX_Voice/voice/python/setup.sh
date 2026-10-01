#!/usr/bin/env bash
# OptimalX Desktop voice stack: Kokoro TTS + Parakeet STT (sherpa-onnx INT8)
set -euo pipefail

VOICE_HOME="${OPTIMALX_HOME:-$HOME/OptimalX}/voice"
VENV="$VOICE_HOME/venv"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prefer an existing CUDA-ready venv (created for RTX 50-series / Blackwell).
CUDA_VENV="${OPTIMALX_VOICE_VENV:-$HOME/.venvs/optimalx-voice}"

mkdir -p "$VOICE_HOME"

use_existing_cuda_venv=0
if [[ -x "$CUDA_VENV/bin/python" ]]; then
  if "$CUDA_VENV/bin/python" - <<'PY' >/dev/null 2>&1
import torch
assert torch.cuda.is_available()
x = torch.zeros(1, device="cuda")
arch = set(torch.cuda.get_arch_list() or [])
major, minor = torch.cuda.get_device_capability(0)
tag = f"sm_{major}{minor}"
assert (not arch) or tag in arch or f"sm_{major}0" in arch
PY
  then
    use_existing_cuda_venv=1
  fi
fi

if [[ "$use_existing_cuda_venv" -eq 1 ]]; then
  echo "Using CUDA voice venv: $CUDA_VENV"
  if [[ -e "$VENV" && ! -L "$VENV" ]]; then
    backup="$VENV.cpu-backup-$(date +%Y%m%d%H%M%S)"
    echo "Moving previous venv aside → $backup"
    mv "$VENV" "$backup"
  fi
  ln -sfn "$CUDA_VENV" "$VENV"
else
  if [[ ! -x "$VENV/bin/python" ]]; then
    python3 -m venv "$VENV"
  fi
  "$VENV/bin/pip" install -U pip
  # Install Blackwell-capable torch when NVIDIA reports compute 12.x
  if command -v nvidia-smi >/dev/null 2>&1; then
    cap="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ' || true)"
    major="${cap%%.*}"
    if [[ "${major:-0}" -ge 12 ]]; then
      echo "Detected Blackwell GPU (compute $cap) — installing torch cu128"
      "$VENV/bin/pip" install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
    fi
  fi
fi

"$VENV/bin/pip" install -U pip
"$VENV/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

# British English spaCy model used by Kokoro/misaki for en pipelines
"$VENV/bin/python" -m spacy download en_core_web_sm || true

MODEL_DIR="$VOICE_HOME/models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
if [[ ! -f "$MODEL_DIR/encoder.int8.onnx" ]]; then
  echo "Downloading Parakeet INT8 ONNX model (~600 MB)…"
  mkdir -p "$VOICE_HOME/models"
  tmp="$(mktemp -d)"
  curl -fL \
    -o "$tmp/parakeet-int8.tar.bz2" \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2"
  tar -xjf "$tmp/parakeet-int8.tar.bz2" -C "$VOICE_HOME/models"
  rm -rf "$tmp"
fi

echo ""
echo "Voice venv ready: $VENV"
echo "Default TTS voice: bm_george (British male George)"
echo "STT engine: parakeet-tdt-0.6b-v2-int8 (sherpa-onnx, CPU by default)"
echo ""
PYTHONPATH="$SCRIPT_DIR" "$VENV/bin/python" -c "from device_resolve import resolve_device, resolve_stt_provider; print('TTS auto device:', resolve_device()); print('STT provider:', resolve_stt_provider())"
PYTHONPATH="$SCRIPT_DIR" "$VENV/bin/python" "$SCRIPT_DIR/transcribe.py" --status
