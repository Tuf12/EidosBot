"""Growing TTS windows — keep in sync with electron/voice/tts-chunk-split.js."""

from __future__ import annotations

import re

HEAD1_MIN = 80
HEAD1_MAX = 140
HEAD2_MIN = 160
HEAD2_MAX = 220
BODY_MAX = 380
MIN_TAIL_CHARS = 24

WINDOWS = (
    (HEAD1_MIN, HEAD1_MAX),
    (HEAD2_MIN, HEAD2_MAX),
)

_SENTENCE_BREAK = re.compile(r"""[.!?]["')\]]*(?=\s|$)""")


def normalize_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _sentence_breaks(window_text: str) -> list[int]:
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


def split_for_tts_playback(text: str | None) -> list[str]:
    remaining = normalize_text(text)
    if not remaining:
        return []

    pieces: list[str] = []
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
