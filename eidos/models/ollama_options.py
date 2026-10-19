"""Read local Ollama generation options from config."""

from __future__ import annotations

from typing import Any, Dict, Optional

DEFAULT_NUM_CTX = 65536


def local_num_ctx(local_cfg: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return chat ``num_ctx`` for Ollama, or ``None`` to leave Ollama's model default."""
    cfg = local_cfg or {}
    raw = cfg.get("num_ctx")
    if raw is None or raw == "":
        return DEFAULT_NUM_CTX
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_NUM_CTX
    return max(0, value) or None


def chat_generation_options(num_ctx: Optional[int]) -> Dict[str, Any]:
    """Sampling and context options sent on every Ollama chat request."""
    options: Dict[str, Any] = {
        "repeat_penalty": 1.15,
        "temperature": 0.7,
    }
    if num_ctx is not None:
        options["num_ctx"] = num_ctx
    return options


def embedding_num_gpu(mem_cfg: Optional[Dict[str, Any]]) -> Optional[int]:
    """GPU layers for embedding models; ``0`` keeps nomic on CPU."""
    cfg = mem_cfg or {}
    if "embedding_num_gpu" not in cfg:
        return 0
    raw = cfg.get("embedding_num_gpu")
    if raw is None or raw == "":
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0
