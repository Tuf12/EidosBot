"""Voice I/O package for EidosBot (Kokoro-82M TTS + Parakeet TDT INT8 STT)."""

from eidos.voice.audio_capture import AudioRecorder
from eidos.voice.chunker import (
    DEFAULT_TTS_CHUNK_SIZES,
    split_for_tts_playback,
    split_speech_chunks,
    text_for_speech,
)
from eidos.voice.sanitize import sanitize_for_tts
from eidos.voice.speech_stream import SpeechStreamer
from eidos.voice.stt import ParakeetSTT
from eidos.voice.tts import KokoroTTS
from eidos.voice.voice_manager import VoiceManager

__all__ = [
    "AudioRecorder",
    "DEFAULT_TTS_CHUNK_SIZES",
    "KokoroTTS",
    "ParakeetSTT",
    "SpeechStreamer",
    "VoiceManager",
    "sanitize_for_tts",
    "split_for_tts_playback",
    "split_speech_chunks",
    "text_for_speech",
]
