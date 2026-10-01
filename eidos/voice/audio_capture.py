"""Microphone audio capture for push-to-talk voice interaction."""

import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class AudioRecorder:
    """Records microphone audio to a standard WAV file for STT transcription."""

    def __init__(self):
        self.arecord_bin = shutil.which("arecord")
        self.ffmpeg_bin = shutil.which("ffmpeg")

        if not self.arecord_bin and not self.ffmpeg_bin:
            logger.warning("Neither 'arecord' nor 'ffmpeg' found on system for audio recording.")

        self._proc: Optional[subprocess.Popen] = None
        self._current_path: Optional[str] = None
        self._lock = threading.Lock()
        self._start_time: float = 0.0

    @property
    def is_recording(self) -> bool:
        """Check if recording is in progress."""
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def start_recording(self, out_path: Optional[str] = None) -> str:
        """Begin recording audio from default input device."""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                logger.warning("Already recording audio.")
                return self._current_path or ""

            if not out_path:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                    out_path = tf.name

            self._current_path = out_path
            self._start_time = time.time()

            if self.arecord_bin:
                cmd = [
                    self.arecord_bin,
                    "-q",
                    "-f", "S16_LE",
                    "-r", "16000",
                    "-c", "1",
                    out_path,
                ]
            elif self.ffmpeg_bin:
                cmd = [
                    self.ffmpeg_bin,
                    "-y",
                    "-f", "pulse",
                    "-i", "default",
                    "-ac", "1",
                    "-ar", "16000",
                    out_path,
                ]
            else:
                raise RuntimeError("No recording utility (arecord / ffmpeg) available.")

            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=os.setsid,  # Start in a new process group for clean signal handling
                )
                logger.info("Started recording audio to %s", out_path)
            except Exception as e:
                self._proc = None
                self._current_path = None
                raise RuntimeError(f"Failed to start audio recording: {e}")

            return out_path

    def stop_recording(self) -> str:
        """Stop recording and finalize the WAV file."""
        with self._lock:
            if not self._proc:
                return self._current_path or ""

            proc = self._proc
            out_path = self._current_path or ""
            self._proc = None

        try:
            # Send SIGINT so arecord writes the valid WAV header
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        except (ProcessLookupError, OSError):
            try:
                proc.send_signal(signal.SIGINT)
            except Exception:
                pass
        except Exception as e:
            logger.debug("Error sending SIGINT to recording process: %s", e)
            try:
                proc.terminate()
            except Exception:
                pass

        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            logger.warning("Recording process timed out, killing.")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass

        duration = time.time() - self._start_time
        logger.info("Stopped audio recording (duration: %.2fs) -> %s", duration, out_path)

        # Give disk buffer a tiny moment to flush
        time.sleep(0.05)

        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError(f"Recorded WAV file was not created or is empty: {out_path}")

        return out_path

    def cancel_recording(self):
        """Cancel recording and discard recorded audio file."""
        try:
            path = self.stop_recording()
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
