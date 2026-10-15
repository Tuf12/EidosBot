"""One continuous WAV stream for phone playback.

Android keeps a single media element alive when the screen locks or the user
leaves the app. A playlist of separate clips does not. This session writes one
WAV: a short realtime lead-in until the reply text arrives, then speech PCM.
"""

from __future__ import annotations

import os
import struct
import threading
import time
import uuid
from typing import Callable, Iterator, Optional

SAMPLE_RATE = 48000
CHANNELS = 1
BITS = 16
FRAME_SEC = 0.1
WAIT_FOR_TEXT_SEC = 180
MAX_TEXT_CHARS = 30000
SESSION_TTL_SEC = 30 * 60

SynthStream = Callable[[str, Optional[str], Optional[float]], Iterator[str]]


def wav_header(sample_rate: int, channels: int, bits: int, data_bytes: int) -> bytes:
    """Canonical 44-byte PCM header. data_bytes may be 0xFFFFFFFF while the length is still unknown."""
    block_align = channels * (bits // 8)
    byte_rate = sample_rate * block_align
    payload = 36 + data_bytes
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        payload & 0xFFFFFFFF,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
        b"data",
        data_bytes & 0xFFFFFFFF,
    )


def patch_wav_sizes(blob: bytes) -> bytes:
    """Rewrite RIFF and data sizes once the full stream is known."""
    if len(blob) < 44:
        return blob
    out = bytearray(blob)
    data_size = len(blob) - 44
    struct.pack_into("<I", out, 4, len(blob) - 8)
    struct.pack_into("<I", out, 40, data_size)
    return bytes(out)


def extract_pcm(wav: bytes) -> tuple[bytes, int, int, int]:
    """Return interleaved PCM bytes plus sample rate, channels, and bits per sample."""
    if len(wav) < 44 or wav[0:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise ValueError("Not a WAV file")
    channels = 1
    sample_rate = SAMPLE_RATE
    bits = 16
    offset = 12
    pcm = b""
    while offset + 8 <= len(wav):
        chunk_id = wav[offset : offset + 4]
        size = struct.unpack_from("<I", wav, offset + 4)[0]
        start = offset + 8
        end = min(len(wav), start + size)
        if chunk_id == b"fmt " and end >= start + 16:
            channels = struct.unpack_from("<H", wav, start + 2)[0] or 1
            sample_rate = struct.unpack_from("<I", wav, start + 4)[0] or SAMPLE_RATE
            bits = struct.unpack_from("<H", wav, start + 14)[0] or 16
        elif chunk_id == b"data":
            pcm = wav[start:end]
            break
        step = 8 + size + (size % 2)
        if step <= 0:
            break
        offset += step
    if not pcm:
        raise ValueError("WAV data chunk missing")
    return pcm, sample_rate, channels, bits


def to_mono16(pcm: bytes, channels: int, bits: int) -> bytes:
    """Convert PCM to mono signed 16-bit."""
    if bits == 16 and channels == 1:
        even = len(pcm) - (len(pcm) % 2)
        return pcm[:even]
    if bits != 16:
        raise ValueError(f"Unsupported WAV width: {bits}-bit")
    count = len(pcm) // 2
    samples = struct.unpack("<" + "h" * count, pcm[: count * 2])
    mono = []
    for i in range(0, len(samples) - channels + 1, channels):
        total = 0
        for ch in range(channels):
            total += samples[i + ch]
        mono.append(int(total / channels))
    return struct.pack("<" + "h" * len(mono), *mono)


def resample_mono16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear resample of mono s16 PCM."""
    if src_rate == dst_rate:
        return pcm
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("Invalid sample rate")
    count = len(pcm) // 2
    if count == 0:
        return b""
    src = struct.unpack("<" + "h" * count, pcm[: count * 2])
    dst_count = max(1, int(round(count * dst_rate / src_rate)))
    out = []
    scale = (count - 1) / max(1, dst_count - 1)
    for i in range(dst_count):
        pos = i * scale
        left = int(pos)
        right = min(left + 1, count - 1)
        frac = pos - left
        value = src[left] * (1 - frac) + src[right] * frac
        out.append(max(-32767, min(32767, int(round(value)))))
    return struct.pack("<" + "h" * len(out), *out)


def primer_frame(n_samples: int, state: Optional[list[int]] = None) -> bytes:
    """One block of silent PCM frames (zero amplitude). Completely silent to speakers."""
    return b"\x00\x00" * n_samples


class LiveTtsSession:
    """Bytes of one WAV, written from a worker thread and read by the HTTP stream."""

    def __init__(self, session_id: str):
        self.id = session_id
        self.created = time.time()
        self.text: Optional[str] = None
        self.voice: Optional[str] = None
        self.speed: Optional[float] = None
        self.title = "EidosBot"
        self.error: Optional[str] = None
        self.cues: list[float] = []
        self.duration = 0.0
        self.speech_start = 0.0
        self.complete = False
        self.cancelled = False
        self.text_ready = threading.Event()
        self._buf = bytearray()
        self._cond = threading.Condition()
        self._started = False
        self._start_lock = threading.Lock()

    def submit(self, text: str, voice: Optional[str], speed: Optional[float], title: str) -> None:
        if self.text is None and not self.cancelled:
            self.text = text
            self.voice = voice
            self.speed = speed
            self.title = title or "EidosBot"
        self.text_ready.set()

    def fail(self, message: str) -> None:
        self.error = message
        self.cancelled = True
        self.text_ready.set()
        self.finish()

    def append(self, data: bytes) -> None:
        if not data:
            return
        with self._cond:
            if self.complete:
                return
            self._buf.extend(data)
            self._cond.notify_all()

    def add_duration(self, seconds: float) -> None:
        with self._cond:
            self.duration += seconds

    def mark_speech_start(self) -> float:
        with self._cond:
            self.speech_start = self.duration
            return self.speech_start

    def add_cue(self, at_seconds: float) -> None:
        with self._cond:
            self.cues.append(at_seconds)
            self.duration = max(self.duration, at_seconds)

    def finish(self) -> None:
        with self._cond:
            self.complete = True
            self._cond.notify_all()

    def cancel(self) -> None:
        self.cancelled = True
        self.text_ready.set()
        self.finish()

    def wait_chunk(self, offset: int, size: int) -> bytes:
        """Block until bytes are available past offset, or the stream has finished."""
        with self._cond:
            while offset >= len(self._buf) and not self.complete:
                self._cond.wait(timeout=0.5)
            if offset >= len(self._buf):
                return b""
            end = min(len(self._buf), offset + size)
            return bytes(self._buf[offset:end])

    def wav_bytes(self) -> bytes:
        with self._cond:
            return patch_wav_sizes(bytes(self._buf))

    def meta(self) -> dict:
        with self._cond:
            return {
                "ok": self.error is None,
                "id": self.id,
                "title": self.title,
                "cues": list(self.cues),
                "duration": self.duration,
                "speechStart": self.speech_start,
                "complete": self.complete,
                "error": self.error,
                "sampleRate": SAMPLE_RATE,
            }


def _cleanup_wav(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def run_live_session(session: LiveTtsSession, synthesize_stream: SynthStream) -> None:
    """Produce the WAV body. Safe to call from a background thread."""
    frame_samples = int(SAMPLE_RATE * FRAME_SEC)
    rng_state = [session.created and int(session.created * 1000) % 100000 or 1]
    try:
        session.append(wav_header(SAMPLE_RATE, CHANNELS, BITS, 0xFFFFFFFF))
        deadline = time.time() + WAIT_FOR_TEXT_SEC
        while not session.text_ready.is_set():
            if session.cancelled:
                return
            if time.time() > deadline:
                session.error = "Timed out waiting for a reply to read"
                return
            session.append(primer_frame(frame_samples, rng_state))
            session.add_duration(FRAME_SEC)
            time.sleep(FRAME_SEC)

        if session.cancelled or not session.text:
            return

        speech_origin = session.mark_speech_start()
        cursor = speech_origin
        for wav_path in synthesize_stream(session.text, session.voice, session.speed):
            if session.cancelled:
                _cleanup_wav(wav_path)
                return
            try:
                with open(wav_path, "rb") as handle:
                    blob = handle.read()
                pcm, rate, channels, bits = extract_pcm(blob)
                pcm = resample_mono16(to_mono16(pcm, channels, bits), rate, SAMPLE_RATE)
            finally:
                _cleanup_wav(wav_path)
            if not pcm:
                continue
            session.add_cue(cursor)
            session.append(pcm)
            cursor += (len(pcm) // 2) / SAMPLE_RATE
            session.add_duration((len(pcm) // 2) / SAMPLE_RATE)
        if not session.cues:
            session.add_cue(speech_origin)
    except Exception as exc:
        session.error = str(exc)
    finally:
        session.finish()


class LiveTtsHub:
    """In-memory live playback sessions for the phone audio element."""

    def __init__(self) -> None:
        self._sessions: dict[str, LiveTtsSession] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str) -> LiveTtsSession:
        self._purge_expired()
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = LiveTtsSession(session_id)
                self._sessions[session_id] = session
            return session

    def get(self, session_id: str) -> Optional[LiveTtsSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def ensure_started(self, session: LiveTtsSession, synthesize_stream: SynthStream) -> None:
        with session._start_lock:
            if session._started:
                return
            session._started = True
        threading.Thread(
            target=run_live_session,
            args=(session, synthesize_stream),
            name=f"tts-live-{session.id[:8]}",
            daemon=True,
        ).start()

    def _purge_expired(self) -> None:
        cutoff = time.time() - SESSION_TTL_SEC
        with self._lock:
            stale = [key for key, session in self._sessions.items() if session.created < cutoff]
            for key in stale:
                self._sessions.pop(key, None)


def valid_session_id(value: str) -> bool:
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        return str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError, TypeError):
        return False
