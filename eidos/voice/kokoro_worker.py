"""Long-lived Kokoro TTS worker client — matches OptimalX speak_worker.py protocol."""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Iterator, Optional

from eidos.voice.tts_service import TTS_INFERENCE_DEVICE

logger = logging.getLogger(__name__)


class KokoroWorkerClient:
    """Keep one speak_worker.py process warm for low-latency, consistent Kokoro output."""

    def __init__(
        self,
        python_path: str,
        voice_dir: str,
        device: str = "cpu",
        warm_voice: str = "bm_george",
        load_timeout_s: float = 120.0,
    ):
        self.python_path = python_path
        self.voice_dir = voice_dir
        self.device = TTS_INFERENCE_DEVICE
        self.warm_voice = warm_voice or "bm_george"
        self.load_timeout_s = load_timeout_s

        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._stderr_reader: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._cmd_lock = threading.RLock()
        self._ready = threading.Event()
        self._load_error: Optional[str] = None
        self._pending: list[dict] = []
        self._pending_lock = threading.Lock()
        self._active_stream_id: Optional[str] = None
        self._stream_chunks: list[dict] = []
        self._stream_done = threading.Event()
        self._stream_error: Optional[str] = None

    @property
    def is_available(self) -> bool:
        worker_script = Path(self.voice_dir) / "speak_worker.py"
        return worker_script.exists() and os.path.exists(self.python_path)

    @property
    def is_warm(self) -> bool:
        return self._ready.is_set() and self._proc is not None and self._proc.poll() is None

    def ensure_started(self) -> None:
        with self._lock:
            if self.is_warm:
                return
            if self._proc is not None and self._proc.poll() is None:
                if not self._ready.wait(timeout=self.load_timeout_s):
                    raise RuntimeError(self._load_error or "Kokoro worker failed to load")
                return
            self._start_process()
        if not self._ready.wait(timeout=self.load_timeout_s):
            raise RuntimeError(self._load_error or "Kokoro worker failed to load")

    def synthesize_to_file(
        self,
        text: str,
        out_path: str,
        voice: str,
        speed: float,
    ) -> dict:
        self.ensure_started()
        with self._cmd_lock:
            response = self._request(
                {
                    "cmd": "speak",
                    "text": text,
                    "outPath": out_path,
                    "voice": voice,
                    "speed": speed,
                    "device": self.device,
                },
                expected_key="outPath",
            )
            if not response.get("ok", True):
                raise RuntimeError(response.get("error") or "Kokoro synthesis failed")
            return response

    def synthesize_stream(
        self,
        text: str,
        voice: str,
        speed: float,
        pieces: Optional[list[str]] = None,
        on_chunk: Optional[Callable[[dict], None]] = None,
    ) -> Iterator[dict]:
        self.ensure_started()
        stream_id = f"eidos-{uuid.uuid4().hex[:12]}"
        payload = {
            "cmd": "speak_stream",
            "streamId": stream_id,
            "text": text,
            "voice": voice,
            "speed": speed,
            "device": self.device,
        }
        if pieces:
            payload["pieces"] = pieces

        with self._cmd_lock:
            with self._pending_lock:
                self._active_stream_id = stream_id
                self._stream_chunks = []
                self._stream_error = None
                self._stream_done.clear()

            self._write(payload)

            while True:
                with self._pending_lock:
                    if self._stream_chunks:
                        chunk = self._stream_chunks.pop(0)
                    elif self._stream_done.is_set():
                        if self._stream_error:
                            raise RuntimeError(self._stream_error)
                        return
                    else:
                        chunk = None
                if chunk is None:
                    time.sleep(0.01)
                    continue
                if on_chunk:
                    on_chunk(chunk)
                yield chunk

    def cancel_stream(self) -> None:
        if not self._proc or self._proc.poll() is not None:
            return
        stream_id = self._active_stream_id
        try:
            self._write({"cmd": "cancel", "streamId": stream_id or ""})
        except Exception:
            pass
        with self._pending_lock:
            self._stream_chunks = []
            self._stream_done.set()

    def shutdown(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            self._ready.clear()
        if proc and proc.poll() is None:
            try:
                proc.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
                proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _start_process(self) -> None:
        worker_script = str(Path(self.voice_dir) / "speak_worker.py")
        env = os.environ.copy()
        env["OPTIMALX_VOICE_DEVICE"] = self.device
        self._ready.clear()
        self._load_error = None
        self._proc = subprocess.Popen(
            [self.python_path, worker_script, "--device", self.device, "--warm-voice", self.warm_voice],
            cwd=self.voice_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self._reader = threading.Thread(target=self._read_stdout, name="eidos-kokoro-worker", daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._read_stderr, name="eidos-kokoro-worker-err", daemon=True)
        self._stderr_reader.start()

    def _read_stderr(self) -> None:
        proc = self._proc
        if not proc or not proc.stderr:
            return
        for raw in proc.stderr:
            line = raw.strip()
            if line:
                logger.debug("Kokoro worker stderr: %s", line)

    def _read_stdout(self) -> None:
        proc = self._proc
        if not proc or not proc.stdout:
            return
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._handle_message(msg)
        self._ready.clear()
        with self._pending_lock:
            self._stream_done.set()

    def _handle_message(self, msg: dict) -> None:
        event = msg.get("event")
        if event == "loaded":
            if msg.get("ok", True):
                self._ready.set()
            else:
                self._load_error = msg.get("error") or "Kokoro failed to load"
                self._ready.set()
            return

        if event == "chunk":
            with self._pending_lock:
                if self._active_stream_id and msg.get("streamId") == self._active_stream_id:
                    self._stream_chunks.append(msg)
            return

        if event == "speak_done":
            with self._pending_lock:
                if msg.get("ok") is False:
                    self._stream_error = msg.get("error") or "TTS stream failed"
                self._active_stream_id = None
                self._stream_done.set()
            return

        with self._pending_lock:
            self._pending.append(msg)

    def _write(self, payload: dict) -> None:
        proc = self._proc
        if not proc or proc.poll() is not None or not proc.stdin:
            raise RuntimeError("Kokoro worker is not running")
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    def _request(self, payload: dict, expected_key: Optional[str] = None, timeout_s: float = 180.0) -> dict:
        with self._pending_lock:
            self._pending.clear()
        self._write(payload)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            with self._pending_lock:
                if self._pending:
                    for i, item in enumerate(self._pending):
                        if expected_key and expected_key not in item and not item.get("error"):
                            continue
                        return self._pending.pop(i)
            if self._proc and self._proc.poll() is not None:
                break
            time.sleep(0.01)
        raise TimeoutError("Kokoro worker request timed out")


def decode_chunk_to_wav(chunk: dict, out_path: Optional[str] = None) -> str:
    """Write a speak_stream chunk's base64 WAV payload to disk."""
    audio_b64 = chunk.get("audioBase64")
    if not audio_b64:
        raise RuntimeError("Kokoro stream chunk missing audioBase64")
    data = base64.b64decode(audio_b64)
    if not out_path:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            out_path = tf.name
    with open(out_path, "wb") as handle:
        handle.write(data)
    return out_path
