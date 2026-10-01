"""Low-latency WAV playback for progressive TTS (OptimalX voice-playback.js style).

Uses sounddevice for thread-safe, fast chunk playback from background worker
threads. Falls back to pw-play/paplay when sounddevice is unavailable.

Qt QMediaPlayer is intentionally NOT used here — creating QMediaPlayer +
QEventLoop on a background thread deadlocks / freezes the PyQt6 GUI.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_PCM = Tuple[np.ndarray, int]


class WavPlayback:
    """Play 48 kHz PCM WAV clips with minimal gap between Kokoro stream chunks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._stop_flag = threading.Event()
        self._is_playing_audio: bool = False
        self._player_binary = (
            shutil.which("pw-play")
            or shutil.which("paplay")
            or shutil.which("aplay")
        )
        self._sd = None
        self._sf = None
        self._init_audio_libs()

    @property
    def is_playing(self) -> bool:
        """Return True if audio playback is actively playing sound."""
        if self._is_playing_audio:
            return True
        if self._sd is not None:
            try:
                stream = self._sd.get_stream()
                if stream is not None and getattr(stream, "active", False):
                    return True
            except Exception:
                pass
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return True
        return False

    def _init_audio_libs(self) -> None:
        try:
            import sounddevice as sd
            import soundfile as sf

            self._sd = sd
            self._sf = sf
        except ImportError:
            logger.debug("sounddevice/soundfile unavailable; using CLI audio fallback.")

    @property
    def uses_native_audio(self) -> bool:
        return self._sd is not None and self._sf is not None

    def load_pcm(self, wav_path: str) -> _PCM:
        """Decode a WAV file into mono float32 samples."""
        path = str(Path(wav_path).resolve())
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        if self._sf is None:
            raise RuntimeError("soundfile is not available")
        data, sample_rate = self._sf.read(path, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        return np.ascontiguousarray(data), int(sample_rate)

    def load_pcm_bytes(self, wav_bytes: bytes) -> _PCM:
        if self._sf is None:
            raise RuntimeError("soundfile is not available")
        data, sample_rate = self._sf.read(io.BytesIO(wav_bytes), dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        return np.ascontiguousarray(data), int(sample_rate)

    def play(self, wav_path: str, blocking: bool = True) -> bool:
        path = str(Path(wav_path).resolve())
        if not os.path.exists(path):
            logger.warning("Audio file does not exist: %s", path)
            return False
        if self._try_sounddevice_path(path, blocking):
            return True
        return self._play_subprocess(path, blocking)

    def play_pcm(self, pcm: np.ndarray, sample_rate: int, blocking: bool = True) -> bool:
        if self._try_sounddevice_pcm(pcm, sample_rate, blocking):
            return True
        return self._play_subprocess_from_pcm(pcm, sample_rate, blocking)

    def stop(self) -> None:
        self._stop_flag.set()
        self._is_playing_audio = False
        with self._lock:
            if self._sd is not None:
                try:
                    self._sd.stop()
                except Exception:
                    pass
            self._stop_subprocess()
        self._stop_flag.clear()

    def _try_sounddevice_path(self, path: str, blocking: bool) -> bool:
        if not self.uses_native_audio:
            return False
        try:
            pcm, sample_rate = self.load_pcm(path)
            return self._try_sounddevice_pcm(pcm, sample_rate, blocking)
        except Exception as exc:
            logger.debug("sounddevice path playback failed: %s", exc)
            return False

    def _try_sounddevice_pcm(self, pcm: np.ndarray, sample_rate: int, blocking: bool) -> bool:
        if not self.uses_native_audio:
            return False
        sd = self._sd
        self._is_playing_audio = True
        try:
            with self._lock:
                self._stop_flag.clear()
                self._stop_subprocess()
            sd.play(pcm, sample_rate)
            if blocking:
                while True:
                    stream = sd.get_stream()
                    if stream is None or not stream.active:
                        break
                    if self._stop_flag.is_set():
                        sd.stop()
                        return False
                    sd.sleep(50)
                sd.wait()
            return True
        except Exception as exc:
            logger.debug("sounddevice PCM playback failed: %s", exc)
            try:
                sd.stop()
            except Exception:
                pass
            return False
        finally:
            if blocking:
                self._is_playing_audio = False

    def _play_subprocess(self, path: str, blocking: bool) -> bool:
        if not self._player_binary:
            logger.warning("No audio player (pw-play/paplay/aplay) available.")
            return False
        cmd = [self._player_binary, path]

        def _run() -> None:
            self._is_playing_audio = True
            try:
                with self._lock:
                    self._proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                while self._proc.poll() is None:
                    if self._stop_flag.is_set():
                        self._stop_subprocess()
                        return
                    try:
                        self._proc.wait(timeout=0.05)
                    except subprocess.TimeoutExpired:
                        continue
            except Exception as exc:
                logger.error("Error playing audio: %s", exc)
            finally:
                self._is_playing_audio = False
                with self._lock:
                    if self._proc and self._proc.poll() is not None:
                        self._proc = None

        if blocking:
            _run()
            return True
        threading.Thread(target=_run, name="eidos-wav-play", daemon=True).start()
        return True

    def _play_subprocess_from_pcm(self, pcm: np.ndarray, sample_rate: int, blocking: bool) -> bool:
        if self._sf is None or not self._player_binary:
            return False
        import tempfile

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                tmp_path = handle.name
            int_pcm = np.clip(pcm, -1.0, 1.0)
            self._sf.write(tmp_path, int_pcm, sample_rate, subtype="PCM_16")
            return self._play_subprocess(tmp_path, blocking=blocking)
        except Exception as exc:
            logger.error("PCM fallback playback failed: %s", exc)
            return False
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def _stop_subprocess(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=0.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._proc = None
