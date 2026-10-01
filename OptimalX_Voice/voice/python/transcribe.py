#!/usr/bin/env python3
"""Parakeet STT via sherpa-onnx INT8 ONNX for OptimalX Desktop.

Usage:
  transcribe.py --wav /path/to/audio.wav
  transcribe.py --status
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

import soundfile as sf

from device_resolve import cuda_mem_info, resolve_stt_provider
from stt_model import DEFAULT_MODEL, ensure_model, model_paths, model_ready, normalize_model_id

_RECOGNIZER = None
_RECOGNIZER_KEY = None


def _recognizer_key(model_name: str, provider: str, num_threads: int) -> str:
    return f"{model_name}|{provider}|{num_threads}"


def load_model(model_name: str, provider: str):
    global _RECOGNIZER, _RECOGNIZER_KEY
    import sherpa_onnx

    model_id = normalize_model_id(model_name)
    num_threads = max(1, min(os.cpu_count() or 4, 8))
    key = _recognizer_key(model_id, provider, num_threads)
    if _RECOGNIZER is not None and _RECOGNIZER_KEY == key:
        return _RECOGNIZER

    paths = ensure_model(model_id)
    recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
        str(paths["encoder"]),
        str(paths["decoder"]),
        str(paths["joiner"]),
        str(paths["tokens"]),
        num_threads=num_threads,
        provider=provider,
        debug=False,
        decoding_method="greedy_search",
        model_type="nemo_transducer",
    )
    _RECOGNIZER = recognizer
    _RECOGNIZER_KEY = key
    return recognizer


def status(model_name: str) -> dict:
    model_id = normalize_model_id(model_name)
    try:
        import sherpa_onnx

        sherpa_ver = getattr(sherpa_onnx, "__version__", "unknown")
        sherpa_ok = True
    except Exception as exc:
        return {"ok": False, "ready": False, "error": f"sherpa-onnx not available: {exc}"}

    free, total = cuda_mem_info()
    provider = resolve_stt_provider(None)
    paths = None
    ready = False
    try:
        paths = model_paths(model_id)
        ready = model_ready(model_id)
    except ValueError as exc:
        return {"ok": False, "ready": False, "error": str(exc)}

    return {
        "ok": True,
        "ready": ready,
        "engine": "parakeet",
        "backend": "sherpa-onnx",
        "model": model_id,
        "modelDir": str(paths["root"]) if paths else "",
        "sherpaOnnx": sherpa_ver if sherpa_ok else None,
        "cudaAvailable": bool(total),
        "cudaFreeMb": round(free / (1024 * 1024), 1) if free else 0,
        "cudaTotalMb": round(total / (1024 * 1024), 1) if total else 0,
        "provider": provider,
        "note": "idle (loads on first use)" if not ready else None,
    }


def transcribe(wav_path: str, model_name: str, device: str | None) -> dict:
    path = Path(wav_path)
    if not path.is_file():
        raise FileNotFoundError(f"WAV not found: {wav_path}")

    model_id = normalize_model_id(model_name)
    provider = resolve_stt_provider(device)
    recognizer = load_model(model_id, provider)

    audio, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    if audio.ndim == 2 and audio.shape[1] > 1:
        audio = audio[:, 0]
    else:
        audio = audio.reshape(-1)

    stream = recognizer.create_stream()
    stream.accept_waveform(int(sample_rate), audio)
    recognizer.decode_stream(stream)
    text = (stream.result.text or "").strip()

    return {
        "ok": True,
        "text": text,
        "model": model_id,
        "provider": provider,
        "device": provider,
        "wavPath": str(path),
        "backend": "sherpa-onnx",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="OptimalX Parakeet STT (sherpa-onnx)")
    parser.add_argument("--wav", default="")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--device", default="")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    try:
        if args.status:
            print(json.dumps(status(args.model)))
            return 0
        if not args.wav:
            raise ValueError("Missing --wav")
        result = transcribe(args.wav, args.model, args.device or None)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "trace": traceback.format_exc(),
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
