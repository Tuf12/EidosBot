"""Progressive TTS chunking — aligned with OptimalX Desktop tts-chunk-split.js.

Growing windows (80–140, 160–220, then 380) let the first WAV start quickly while
later pieces stay ahead of playback on longer answers.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Tuple

from eidos.voice.sanitize import sanitize_for_tts

# OptimalX growing windows — keep in sync with electron/voice/tts-chunk-split.js
HEAD1_MIN = 80
HEAD1_MAX = 140
HEAD2_MIN = 160
HEAD2_MAX = 220
BODY_MAX = 380
MIN_TAIL_CHARS = 24

WINDOWS: Tuple[Tuple[int, int], ...] = (
    (HEAD1_MIN, HEAD1_MAX),
    (HEAD2_MIN, HEAD2_MAX),
)

# Legacy alias for callers that still reference a steady-state size.
DEFAULT_TTS_CHUNK_SIZES: Tuple[int, ...] = (HEAD1_MAX, HEAD2_MAX, BODY_MAX)

_SENTENCE_BREAK = re.compile(r"""[.!?]["')\]]*(?=\s|$)""")


def text_for_speech(text: str) -> str:
    """Strip markdown and tooling noise so Kokoro reads natural prose."""
    return sanitize_for_tts(text)


def normalize_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _sentence_breaks(window_text: str) -> List[int]:
    return [match.end() for match in _SENTENCE_BREAK.finditer(window_text)]


def take_window(text: str, min_chars: int, max_chars: int) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text

    window_text = text[:max_chars]
    ends = _sentence_breaks(window_text)
    in_range = [index for index in ends if min_chars <= index <= max_chars]
    if in_range:
        return text[: in_range[-1]].rstrip()

    space_from_min = window_text.rfind(" ")
    if space_from_min >= min_chars:
        return text[:space_from_min].rstrip()
    if space_from_min > 0:
        return text[:space_from_min].rstrip()
    return window_text


def split_for_tts_playback(text: str | None) -> List[str]:
    """Split sanitized text into OptimalX-style growing playback pieces."""
    remaining = normalize_text(text)
    if not remaining:
        return []

    pieces: List[str] = []
    stage = 0
    while remaining:
        if stage < len(WINDOWS):
            min_chars, max_chars = WINDOWS[stage]
        else:
            min_chars, max_chars = 1, BODY_MAX
        piece = take_window(remaining, min_chars, max_chars)
        if not piece:
            break
        remaining = remaining[len(piece) :].strip()
        if remaining and len(remaining) < MIN_TAIL_CHARS:
            piece = f"{piece} {remaining}".strip()
            remaining = ""
        pieces.append(piece)
        if stage < len(WINDOWS):
            stage += 1
    return pieces


def split_speech_chunks(
    text: str,
    chunk_sizes: Sequence[int] | None = None,  # noqa: ARG001 — kept for API compat
) -> List[str]:
    """Split prepared speech text into progressive playback clips."""
    spoken = text_for_speech(text)
    if not spoken:
        return []
    return split_for_tts_playback(spoken)


def chunk_size_for_index(index: int, chunk_sizes: Sequence[int] | None = None) -> int:
    """Return the max character window for the Nth spoken clip."""
    if chunk_sizes:
        sizes = tuple(max(20, int(n)) for n in chunk_sizes if int(n) > 0)
        if sizes:
            if index < 0:
                index = 0
            if index < len(sizes):
                return sizes[index]
            return sizes[-1]
    if index < 0:
        index = 0
    if index < len(WINDOWS):
        return WINDOWS[index][1]
    return BODY_MAX


def extract_next_chunk(buffer: str, target: int, finished: bool) -> Tuple[str, str]:
    """Pull one speakable clip from a growing buffer (streaming token feed).

    Uses OptimalX window bounds when ``target`` matches a known head size.
    """
    text = (buffer or "").strip()
    if not text:
        return "", ""

    min_chars, max_chars = _window_for_target(target)
    if finished and len(text) <= max_chars:
        return text, ""

    if len(text) < min_chars and not finished:
        return "", buffer

    piece = take_window(text, min_chars, max_chars)
    if not piece:
        if finished:
            return text, ""
        return "", buffer

    rest = text[len(piece) :].lstrip()
    if finished and rest and len(rest) < MIN_TAIL_CHARS:
        return f"{piece} {rest}".strip(), ""
    return piece, rest


def _window_for_target(target: int) -> Tuple[int, int]:
    target = max(20, int(target))
    for min_chars, max_chars in WINDOWS:
        if target <= max_chars:
            return min_chars, max_chars
    return 1, max(target, BODY_MAX)


def _merge_tiny_tail(
    chunks: Iterable[str],
    min_tail: int = MIN_TAIL_CHARS,
    min_clips: int = 2,
) -> List[str]:
    merged = [c.strip() for c in chunks if c and c.strip()]
    if len(merged) >= max(2, min_clips) and len(merged[-1]) < min_tail:
        merged[-2] = f"{merged[-2]} {merged[-1]}".strip()
        merged.pop()
    return merged
