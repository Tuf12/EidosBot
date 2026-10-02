"""Kokoro-82M Text-to-Speech integration for EidosBot.

Uses OptimalX Desktop's warm speak_worker.py process for consistent 48 kHz output
and CPU inference so local LLMs keep GPU VRAM.
"""

import json
import logging
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from typing import Iterator, List, Optional

from eidos.voice.helpers import resolve_voice_runtime
from eidos.voice.kokoro_worker import KokoroWorkerClient, decode_chunk_to_wav
from eidos.voice.playback import WavPlayback
from eidos.voice.tts_service import (
    TTS_INFERENCE_DEVICE,
    prepare_speech_text,
    prepare_stream,
    resolve_speed,
    resolve_voice,
)
from eidos.voice.voices import DEFAULT_VOICE_ID, list_voices as catalog_voices

logger = logging.getLogger(__name__)


class KokoroTTS:
    """Manages speech synthesis and audio playback using Kokoro-82M."""

    def __init__(
        self,
        python_path: Optional[str] = None,
        voice_dir: Optional[str] = None,
        default_voice: str = DEFAULT_VOICE_ID,
        default_speed: float = 1.0,
        device: str = "cpu",
    ):
        runtime = resolve_voice_runtime(
            configured_python=python_path,
            configured_scripts_dir=voice_dir,
        )
        self.python_path = runtime.python_path
        self.voice_dir = runtime.scripts_dir
        self._voice_runtime = runtime
        self.default_voice = resolve_voice(default_voice)
        self.default_speed = resolve_speed(default_speed)
        # Live Kokoro inference always uses CPU (OptimalX TTS_INFERENCE_DEVICE).
        self.device = TTS_INFERENCE_DEVICE

        self._playback = WavPlayback()
        self._worker: Optional[KokoroWorkerClient] = None
        self._worker_lock = threading.Lock()

    @property
    def is_available(self) -> bool:
        """Check whether voice python and speak.py exist."""
        if not self.voice_dir:
            return False
        speak_script = Path(self.voice_dir) / "speak.py"
        return speak_script.exists() and os.path.exists(self.python_path)

    @property
    def is_playing(self) -> bool:
        """Return True if Kokoro audio playback is actively playing sound."""
        return getattr(self._playback, "is_playing", False)

    def _get_worker(self) -> Optional[KokoroWorkerClient]:
        if not self.voice_dir:
            return None
        worker_script = Path(self.voice_dir) / "speak_worker.py"
        if not worker_script.exists():
            return None
        with self._worker_lock:
            if self._worker is None:
                self._worker = KokoroWorkerClient(
                    python_path=self.python_path,
                    voice_dir=self.voice_dir,
                    device=self.device,
                    warm_voice=self.default_voice,
                )
            return self._worker

    def warm(self) -> None:
        """Pre-load Kokoro so the first spoken reply is not a cold start."""
        worker = self._get_worker()
        if worker and worker.is_available:
            try:
                worker.ensure_started()
                logger.info("Kokoro TTS worker warm on %s", self.device)
            except Exception as exc:
                logger.warning("Could not warm Kokoro worker: %s", exc)

    def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
        out_path: Optional[str] = None,
    ) -> str:
        """Synthesize text into a WAV file using Kokoro-82M."""
        clean_text = prepare_speech_text(text)
        if not clean_text:
            raise ValueError("Cannot synthesize empty text.")

        if not self.is_available:
            raise RuntimeError(
                f"Kokoro TTS script speak.py not found in {self.voice_dir} or python {self.python_path} is invalid."
            )

        selected_voice = resolve_voice(voice, self.default_voice)
        selected_speed = resolve_speed(speed, self.default_speed)

        if not out_path:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                out_path = tf.name

        worker = self._get_worker()
        if worker and worker.is_available:
            try:
                worker.synthesize_to_file(
                    text=clean_text,
                    out_path=out_path,
                    voice=selected_voice,
                    speed=selected_speed,
                )
                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    return out_path
                logger.warning(
                    "Warm Kokoro worker did not produce non-empty WAV at %s; falling back to speak.py",
                    out_path,
                )
            except Exception as exc:
                logger.warning("Warm Kokoro worker failed, falling back to speak.py: %s", exc)

        return self._synthesize_subprocess(
            clean_text=clean_text,
            selected_voice=selected_voice,
            selected_speed=selected_speed,
            out_path=out_path,
        )

    def synthesize_stream(
        self,
        text: str,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
        pieces: Optional[List[str]] = None,
    ) -> Iterator[str]:
        """Yield WAV file paths for each streamed Kokoro audio chunk."""
        prepared = prepare_stream(
            text,
            voice=voice,
            speed=speed,
            default_voice=self.default_voice,
            default_speed=self.default_speed,
        )
        if not prepared:
            return

        stream_pieces = pieces or prepared.pieces
        worker = self._get_worker()
        if worker and worker.is_available:
            try:
                for chunk in worker.synthesize_stream(
                    text=prepared.text,
                    voice=prepared.voice,
                    speed=prepared.speed,
                    pieces=stream_pieces,
                ):
                    yield decode_chunk_to_wav(chunk)
                return
            except Exception as exc:
                logger.warning("Kokoro stream failed, falling back to per-piece synthesis: %s", exc)

        for piece in stream_pieces:
            yield self.synthesize(text=piece, voice=prepared.voice, speed=prepared.speed)

    def _synthesize_subprocess(
        self,
        clean_text: str,
        selected_voice: str,
        selected_speed: float,
        out_path: str,
    ) -> str:
        speak_script = str(Path(self.voice_dir) / "speak.py")
        cmd = [
            self.python_path,
            speak_script,
            "--text",
            clean_text,
            "--voice",
            selected_voice,
            "--speed",
            str(selected_speed),
            "--device",
            self.device,
            "--out",
            out_path,
        ]

        timeout_s = max(180, int(len(clean_text) * 0.35))
        logger.debug("Running Kokoro synthesis (timeout=%ss): %s", timeout_s, cmd)
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
            cwd=self.voice_dir,
        )

        if proc.returncode != 0:
            err = proc.stderr.strip() or proc.stdout.strip()
            raise RuntimeError(f"Kokoro synthesis failed (exit {proc.returncode}): {err}")

        for line in proc.stdout.splitlines():
            line_str = line.strip()
            if line_str.startswith("{") and line_str.endswith("}"):
                try:
                    res = json.loads(line_str)
                    if not res.get("ok", True):
                        raise RuntimeError(f"Kokoro synthesis returned error: {res.get('error')}")
                except json.JSONDecodeError:
                    pass

        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError(f"Output WAV file was not generated at {out_path}")

        return out_path

    def play_audio(self, wav_path: str, blocking: bool = False, interrupt: bool = True) -> bool:
        """Play a 48 kHz WAV clip (PyQt6 when available, else pw-play/paplay)."""
        if interrupt:
            self.stop()
        return self._playback.play(wav_path, blocking=blocking)

    def speak(
        self,
        text: str,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
        blocking: bool = False,
    ) -> bool:
        """Synthesize and play speech using the same OptimalX prep pipeline."""
        prepared = prepare_stream(
            text,
            voice=voice,
            speed=speed,
            default_voice=self.default_voice,
            default_speed=self.default_speed,
        )
        if not prepared:
            return False
        try:
            if len(prepared.pieces) == 1:
                wav_path = self.synthesize(
                    text=prepared.text,
                    voice=prepared.voice,
                    speed=prepared.speed,
                )
                return self.play_audio(wav_path, blocking=blocking)
            for wav_path in self.synthesize_stream(
                text=prepared.text,
                voice=prepared.voice,
                speed=prepared.speed,
                pieces=prepared.pieces,
            ):
                if not self.play_audio(wav_path, blocking=True, interrupt=False):
                    return False
            return True
        except Exception as e:
            logger.error("Failed to speak text: %s", e)
            return False

    def stop(self):
        """Immediately stop any playing speech and cancel in-flight synthesis."""
        worker = self._worker
        if worker:
            try:
                worker.cancel_stream()
            except Exception:
                pass
        self._playback.stop()

    def shutdown(self):
        """Stop playback and tear down the warm Kokoro worker."""
        self.stop()
        with self._worker_lock:
            if self._worker:
                self._worker.shutdown()
                self._worker = None

    def list_voices(self) -> List[str]:
        """Return list of supported Kokoro voices."""
        if not self.is_available:
            return [DEFAULT_VOICE_ID]

        speak_script = str(Path(self.voice_dir) / "speak.py")
        cmd = [self.python_path, speak_script, "--list-voices"]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
                cwd=self.voice_dir,
            )
            for line in proc.stdout.splitlines():
                line_str = line.strip()
                if not line_str.startswith("{"):
                    continue
                try:
                    res = json.loads(line_str)
                    voices = res.get("voices")
                    if isinstance(voices, list):
                        return voices
                except json.JSONDecodeError:
                    pass
        except Exception as e:
            logger.debug("Failed to list voices via CLI: %s", e)

        return [entry["id"] for entry in catalog_voices()]
