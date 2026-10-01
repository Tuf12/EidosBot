"""OptimalX-aligned TTS preparation — mirrors electron/voice/tts-kokoro.js."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from eidos.voice.chunker import split_for_tts_playback
from eidos.voice.sanitize import sanitize_for_tts
from eidos.voice.voices import DEFAULT_VOICE_ID, normalize_voice_id

# Live TTS stays on CPU so local LLMs keep GPU VRAM (OptimalX paths.js).
TTS_INFERENCE_DEVICE = "cpu"
SPEED_MIN = 0.5
SPEED_MAX = 2.0


def prepare_speech_text(text: str | None) -> str:
    """Strip markdown and tooling noise before Kokoro synthesis."""
    return sanitize_for_tts(text)


def resolve_voice(voice_id: str | None, default_voice: str = DEFAULT_VOICE_ID) -> str:
    return normalize_voice_id(voice_id or default_voice)


def resolve_speed(speed: float | None, default_speed: float = 1.0) -> float:
    value = float(speed if speed is not None else default_speed)
    return max(SPEED_MIN, min(SPEED_MAX, value))


@dataclass(frozen=True)
class PreparedSpeech:
    text: str
    pieces: List[str]
    voice: str
    speed: float
    device: str = TTS_INFERENCE_DEVICE


def prepare_stream(
    text: str | None,
    voice: Optional[str] = None,
    speed: Optional[float] = None,
    default_voice: str = DEFAULT_VOICE_ID,
    default_speed: float = 1.0,
) -> Optional[PreparedSpeech]:
    """Sanitize, normalize voice/speed, and split into OptimalX playback windows."""
    cleaned = prepare_speech_text(text)
    if not cleaned:
        return None
    return PreparedSpeech(
        text=cleaned,
        pieces=split_for_tts_playback(cleaned),
        voice=resolve_voice(voice, default_voice),
        speed=resolve_speed(speed, default_speed),
    )
