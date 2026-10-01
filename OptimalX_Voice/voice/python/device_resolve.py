"""Shared CUDA/CPU device selection for OptimalX voice (Kokoro + Parakeet)."""

from __future__ import annotations

import os

# Headroom when sharing the GPU with Ollama / Image Studio.
_MIN_VRAM_TTS_BYTES = int(os.environ.get("OPTIMALX_VOICE_MIN_VRAM_TTS_MB", "1500")) * 1024 * 1024
# INT8 ONNX Parakeet is ~600 MB; only used when STT provider is explicitly cuda.
_MIN_VRAM_STT_BYTES = int(os.environ.get("OPTIMALX_VOICE_MIN_VRAM_STT_MB", "700")) * 1024 * 1024


def cuda_mem_info() -> tuple[int, int]:
    """Return (free_bytes, total_bytes) for GPU 0, or (0, 0) when unavailable."""
    try:
        import torch

        if not torch.cuda.is_available():
            return 0, 0
        free, total = torch.cuda.mem_get_info(0)
        return int(free), int(total)
    except Exception:
        return 0, 0


def is_explicit_device(requested: str | None) -> bool:
    if requested in ("cpu", "cuda"):
        return True
    force = os.environ.get("OPTIMALX_VOICE_DEVICE", "").strip().lower()
    return force in ("cpu", "cuda")


def resolve_device(requested: str | None = None, min_free_bytes: int = 0) -> str:
    """Pick cpu/cuda for voice inference.

    Order:
      1. Explicit requested / OPTIMALX_VOICE_DEVICE
      2. CUDA when kernels work and free VRAM >= min_free_bytes (auto only)
      3. cpu
    """
    if requested in ("cpu", "cuda"):
        return requested
    force = os.environ.get("OPTIMALX_VOICE_DEVICE", "").strip().lower()
    if force in ("cpu", "cuda"):
        return force
    try:
        import torch

        if not torch.cuda.is_available():
            return "cpu"
        try:
            arch = set(torch.cuda.get_arch_list() or [])
            major, minor = torch.cuda.get_device_capability(0)
            tag = f"sm_{major}{minor}"
            if arch and tag not in arch and f"sm_{major}0" not in arch:
                return "cpu"
        except Exception:
            pass
        try:
            torch.zeros(1, device="cuda")
        except Exception:
            return "cpu"
        if min_free_bytes > 0:
            free, _ = cuda_mem_info()
            if free < min_free_bytes:
                return "cpu"
        return "cuda"
    except Exception:
        return "cpu"


def min_vram_tts_bytes() -> int:
    return _MIN_VRAM_TTS_BYTES


def min_vram_stt_bytes() -> int:
    return _MIN_VRAM_STT_BYTES


def resolve_stt_provider(requested: str | None = None) -> str:
    """Sherpa-onnx execution provider for STT.

    Defaults to CPU — INT8 Parakeet is fast on CPU and leaves GPU for Ollama.
    Uses CUDA only when explicitly requested and enough VRAM is free.
    """
    if requested == "cpu":
        return "cpu"
    if requested == "cuda":
        free, _ = cuda_mem_info()
        return "cuda" if free >= min_vram_stt_bytes() else "cpu"
    force = os.environ.get("OPTIMALX_VOICE_DEVICE", "").strip().lower()
    if force == "cpu":
        return "cpu"
    if force == "cuda":
        free, _ = cuda_mem_info()
        return "cuda" if free >= min_vram_stt_bytes() else "cpu"
    return "cpu"
