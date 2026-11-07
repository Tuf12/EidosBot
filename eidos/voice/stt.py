"""Parakeet TDT 0.6B INT8 Speech-to-Text integration for EidosBot.

Runs local NVIDIA Parakeet TDT 0.6B v2 via sherpa-onnx INT8 on CPU.
Transcribes microphone WAV audio into text for the agent loop.
Recordings longer than the model's 240s decode cap are sliced inside transcribe.py.
"""

import json
import logging
import os
from contextlib import nullcontext
from pathlib import Path
import subprocess
from typing import Any, Dict, Optional
import wave

from eidos.models.inference_slot import gpu_inference_slot
from eidos.voice.helpers import resolve_voice_runtime

logger = logging.getLogger(__name__)


class ParakeetSTT:
    """Fast local CPU Speech-to-Text transcriber using sherpa-onnx INT8."""

    def __init__(
        self,
        python_path: Optional[str] = None,
        voice_dir: Optional[str] = None,
        model_name: str = "parakeet-tdt-0.6b-v2-int8",
        device: str = "cuda",
    ):
        runtime = resolve_voice_runtime(
            configured_python=python_path,
            configured_scripts_dir=voice_dir,
        )
        self.python_path = runtime.python_path
        self.voice_dir = runtime.scripts_dir
        self.model_name = model_name
        self.device = (device or "cuda").strip().lower()

    def _uses_gpu(self) -> bool:
        return self.device == "cuda"

    @property
    def is_available(self) -> bool:
        """Check whether voice python and transcribe.py exist."""
        if not self.voice_dir:
            return False
        transcribe_script = Path(self.voice_dir) / "transcribe.py"
        return transcribe_script.exists() and os.path.exists(self.python_path)

    def transcribe(self, wav_path: str) -> str:
        """Transcribe a WAV file into text."""
        if not self.is_available:
            raise RuntimeError(
                f"Parakeet STT script transcribe.py not found in {self.voice_dir} or python {self.python_path} is invalid."
            )

        if not os.path.exists(wav_path):
            raise FileNotFoundError(f"Audio file does not exist: {wav_path}")

        transcribe_script = str(Path(self.voice_dir) / "transcribe.py")
        cmd = [
            self.python_path,
            transcribe_script,
            "--wav",
            wav_path,
            "--device",
            self.device,
        ]

        timeout = _transcribe_timeout_seconds(wav_path)
        logger.debug("Running Parakeet transcription (timeout %ss): %s", timeout, cmd)
        slot = (
            gpu_inference_slot("parakeet-stt")
            if self._uses_gpu()
            else nullcontext()
        )
        with slot:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                cwd=self.voice_dir,
            )

        if proc.returncode != 0:
            err = proc.stderr.strip() or proc.stdout.strip()
            raise RuntimeError(f"Transcription process failed (exit {proc.returncode}): {err}")

        # Parse JSON output from stdout
        for line in proc.stdout.splitlines():
            line_str = line.strip()
            if line_str.startswith("{") and line_str.endswith("}"):
                try:
                    res = json.loads(line_str)
                    if res.get("ok"):
                        chunks = _chunk_count(res)
                        if chunks > 1:
                            logger.info("Parakeet transcribed long audio in %s slices", chunks)
                        return res.get("text", "").strip()
                    else:
                        raise RuntimeError(f"Transcription error: {res.get('error')}")
                except json.JSONDecodeError:
                    pass

        # If no json block found, return stripped stdout
        return proc.stdout.strip()

    def status(self) -> Dict[str, Any]:
        """Query engine and model status."""
        if not self.is_available:
            return {"ok": False, "error": "voice scripts or python not found"}

        transcribe_script = str(Path(self.voice_dir) / "transcribe.py")
        cmd = [self.python_path, transcribe_script, "--status"]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                cwd=self.voice_dir,
            )
            for line in proc.stdout.splitlines():
                line_str = line.strip()
                if line_str.startswith("{") and line_str.endswith("}"):
                    return json.loads(line_str)
            return {"ok": proc.returncode == 0, "raw": proc.stdout}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def _chunk_count(result: Dict[str, Any]) -> int:
    try:
        return max(1, int(result.get("chunks") or 1))
    except (TypeError, ValueError):
        return 1


def _wav_duration_seconds(wav_path: str) -> Optional[float]:
    try:
        with wave.open(wav_path, "rb") as handle:
            rate = handle.getframerate()
            frames = handle.getnframes()
        if not rate or frames < 0:
            return None
        return frames / float(rate)
    except (wave.Error, EOFError, OSError):
        return None


def _transcribe_timeout_seconds(wav_path: str) -> int:
    """Budget for model load plus chunked decode of a long recording.

    A fixed 60s cap kills messages that are allowed to run past a few minutes.
    """
    seconds = _wav_duration_seconds(wav_path)
    if seconds is None:
        return 600
    return int(min(1800, max(90, seconds * 1.5 + 45)))
