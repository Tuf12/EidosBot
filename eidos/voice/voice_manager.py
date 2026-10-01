"""Unified Voice Manager coordinating Speech-to-Text and Text-to-Speech."""

import logging
import os
import threading
from typing import Callable, List, Optional

from eidos.voice.audio_capture import AudioRecorder
from eidos.voice.speech_stream import SpeechStreamer
from eidos.voice.stt import ParakeetSTT
from eidos.voice.tts import KokoroTTS
from eidos.voice.voices import DEFAULT_VOICE_ID, normalize_voice_id
from eidos.voice.tts_service import resolve_speed

logger = logging.getLogger(__name__)


class VoiceManager:
    """Coordinates microphone recording, Parakeet STT transcription, and Kokoro TTS playback."""

    def __init__(
        self,
        python_path: Optional[str] = None,
        voice_dir: Optional[str] = None,
        default_voice: str = DEFAULT_VOICE_ID,
        default_speed: float = 1.0,
        device: str = "cpu",  # noqa: ARG002 — live Kokoro always uses CPU
        hotkey_str: Optional[str] = None,
    ):
        self.tts = KokoroTTS(
            python_path=python_path,
            voice_dir=voice_dir,
            default_voice=normalize_voice_id(default_voice),
            default_speed=resolve_speed(default_speed),
            device=device,
        )
        self.speech_stream = SpeechStreamer(self.tts)
        if self.tts.is_available:
            threading.Thread(target=self.tts.warm, name="eidos-tts-warm", daemon=True).start()
        self.stt = ParakeetSTT(
            python_path=python_path,
            voice_dir=voice_dir,
        )
        self.recorder = AudioRecorder()

        self.hotkey_str = hotkey_str
        self._hotkey_listener = None

        self._on_transcribed_callbacks: List[Callable[[str], None]] = []
        self._on_state_change_callbacks: List[Callable[[bool], None]] = []

        if hotkey_str:
            self._init_hotkey(hotkey_str)

    def _init_hotkey(self, hotkey_str: str):
        """Set up push-to-talk toggle hotkey."""
        try:
            from pynput import keyboard

            def on_toggle():
                if self.is_recording():
                    self.stop_listening()
                else:
                    self.start_listening()

            self._hotkey_listener = keyboard.GlobalHotKeys({hotkey_str: on_toggle})
            self._hotkey_listener.daemon = True
            self._hotkey_listener.start()
            logger.info("Voice push-to-talk hotkey active on %s", hotkey_str)
        except Exception as e:
            logger.warning("Could not initialize voice push-to-talk hotkey (%s)", e)

    def register_on_transcribed(self, callback: Callable[[str], None]):
        """Register callback for when speech transcription finishes."""
        self._on_transcribed_callbacks.append(callback)

    def register_on_state_change(self, callback: Callable[[bool], None]):
        """Register callback for recording state changes (recording started / stopped)."""
        self._on_state_change_callbacks.append(callback)

    def is_recording(self) -> bool:
        """Check if microphone recording is in progress."""
        return self.recorder.is_recording

    def start_listening(self) -> str:
        """Begin audio capture. Stops TTS so the user can barge in."""
        self.stop_speaking()
        path = self.recorder.start_recording()
        for cb in self._on_state_change_callbacks:
            try:
                cb(True)
            except Exception as e:
                logger.error("Error in state change callback: %s", e)
        return path

    def stop_listening(self) -> str:
        """Finish audio capture, transcribe with Parakeet STT, and return text."""
        wav_path = self.recorder.stop_recording()

        for cb in self._on_state_change_callbacks:
            try:
                cb(False)
            except Exception as e:
                logger.error("Error in state change callback: %s", e)

        if not wav_path or not os.path.exists(wav_path):
            return ""

        try:
            text = self.stt.transcribe(wav_path)
            logger.info("Transcribed speech: '%s'", text)
            for cb in self._on_transcribed_callbacks:
                try:
                    cb(text)
                except Exception as e:
                    logger.error("Error in transcribed callback: %s", e)
            return text
        except Exception as e:
            logger.error("Error during speech transcription: %s", e)
            return ""
        finally:
            try:
                if os.path.exists(wav_path):
                    os.remove(wav_path)
            except Exception:
                pass

    def is_speaking(self) -> bool:
        """Check if TTS playback or progressive synthesis is currently active."""
        if hasattr(self, "speech_stream") and hasattr(self.speech_stream, "is_speaking"):
            if self.speech_stream.is_speaking():
                return True
        if hasattr(self, "tts") and getattr(self.tts, "is_playing", False):
            return True
        return False

    def speak(self, text: str, voice: Optional[str] = None, speed: Optional[float] = None, blocking: bool = False) -> bool:
        """Speak a short alert. Interrupts any in-progress reply stream."""
        self.speech_stream.stop()
        return self.tts.speak(text=text, voice=voice, speed=speed, blocking=blocking)

    def speak_response(self, text: str, voice: Optional[str] = None, speed: Optional[float] = None) -> bool:
        """Speak a chat reply using progressive clips so the first words start quickly."""
        return self.speech_stream.speak_text(text=text, voice=voice, speed=speed)

    def stop_speaking(self):
        """Immediately stop ongoing TTS playback and discard queued clips."""
        self.speech_stream.stop()
        self.tts.stop()

    def shutdown(self):
        """Stop recordings, hotkey listeners, and playback."""
        self.recorder.cancel_recording()
        self.stop_speaking()
        self.tts.shutdown()
        if self._hotkey_listener:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
