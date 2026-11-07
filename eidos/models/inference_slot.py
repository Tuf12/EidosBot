"""Process-wide mutex for GPU-heavy local inference (Ollama LLM + Parakeet STT).

Only one of these may run at a time so VRAM is not stacked. CPU-only work
(nomic-embed on CPU, Kokoro TTS) does not use this slot.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

_cond = threading.Condition()
_holder: Optional[str] = None


def holder() -> Optional[str]:
    """Name of the task currently holding the slot, or None."""
    with _cond:
        return _holder


@contextmanager
def gpu_inference_slot(label: str) -> Iterator[None]:
    """Block until the GPU inference slot is free, then run ``label``."""
    acquire(label)
    try:
        yield
    finally:
        release(label)


def acquire(label: str) -> None:
    global _holder
    with _cond:
        while _holder is not None:
            if _holder != label:
                logger.info(
                    "Waiting for GPU inference slot (%s running; %s queued)",
                    _holder,
                    label,
                )
            _cond.wait()
        _holder = label


def release(label: str) -> None:
    global _holder
    with _cond:
        if _holder == label:
            _holder = None
            _cond.notify_all()
        elif _holder is not None:
            logger.warning(
                "GPU inference slot release mismatch (holder=%s, release=%s)",
                _holder,
                label,
            )
