"""One seekable speech clip for the desktop player bar.

Kokoro still arrives as short WAV pieces. This session stitches them into a
single timeline so the pill can pause, replay, and jump by five seconds the
same way the phone player does. The PortAudio stream is never started from a
Qt widget thread's media-player object; sounddevice's callback owns the device.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_END_SLACK_SEC = 0.05


def _mono_float(pcm: np.ndarray) -> np.ndarray:
    samples = np.asarray(pcm, dtype=np.float32)
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    return np.ascontiguousarray(samples, dtype=np.float32)


def _resample(pcm: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate or pcm.size == 0:
        return pcm
    if src_rate <= 0 or dst_rate <= 0:
        return pcm
    dst_count = max(1, int(round(pcm.size * dst_rate / src_rate)))
    if pcm.size == 1:
        return np.full(dst_count, pcm[0], dtype=np.float32)
    src_x = np.linspace(0.0, 1.0, pcm.size, endpoint=False)
    dst_x = np.linspace(0.0, 1.0, dst_count, endpoint=False)
    return np.interp(dst_x, src_x, pcm).astype(np.float32)


class ClipPlayer:
    """Growing PCM buffer with pause, seek, and a finished state that stays open."""

    def __init__(self, enable_output: bool = True):
        self._lock = threading.Lock()
        self._pcm = np.zeros(0, dtype=np.float32)
        self._rate = 48000
        self._cues: list[int] = []
        self._cursor = 0
        self._active = False
        self._playing = False
        self._paused = False
        self._complete = False
        self._stream = None
        self._enable_output = enable_output
        self._sd = None
        if enable_output:
            try:
                import sounddevice as sd

                self._sd = sd
            except ImportError:
                logger.debug("sounddevice unavailable; speech bar will not play audio.")

    @property
    def available(self) -> bool:
        return self._sd is not None and self._enable_output

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def playing(self) -> bool:
        with self._lock:
            return self._transport_playing_locked()

    def busy(self) -> bool:
        """True while audio is starting or still coming out. Finished and paused are idle."""
        with self._lock:
            if not self._active or self._finished_locked():
                return False
            if self._transport_playing_locked():
                return True
            return not self._paused and (self._pcm.size == 0 or self._cursor >= self._pcm.size)

    def begin(self) -> None:
        """Open a new clip. Replaces whatever was showing on the player bar."""
        self.dismiss()
        with self._lock:
            self._pcm = np.zeros(0, dtype=np.float32)
            self._cues = []
            self._cursor = 0
            self._rate = 48000
            self._active = True
            self._playing = False
            self._paused = False
            self._complete = False

    def append(self, pcm: np.ndarray, sample_rate: int) -> None:
        incoming = _mono_float(pcm)
        with self._lock:
            if not self._active or incoming.size == 0:
                return
            rate = int(sample_rate) or self._rate or 48000
            if self._pcm.size == 0:
                self._rate = rate
            samples = _resample(incoming, rate, self._rate)
            if samples.size == 0:
                return
            self._cues.append(int(self._pcm.size))
            self._pcm = np.concatenate([self._pcm, samples])
            if not self._paused and not self._finished_locked():
                self._playing = True
        self._ensure_stream()

    def mark_complete(self) -> None:
        with self._lock:
            if self._active:
                self._complete = True

    def pause(self) -> None:
        with self._lock:
            if not self._active or self._finished_locked():
                return
            self._paused = True
            self._playing = False

    def resume(self) -> None:
        """Continue, or start again from the beginning when the clip has finished."""
        with self._lock:
            if not self._active:
                return
            if self._finished_locked():
                self._cursor = 0
            self._paused = False
            self._playing = self._pcm.size > 0
        self._ensure_stream()

    def seek_by(self, seconds: float) -> None:
        with self._lock:
            if not self._active or self._rate <= 0:
                return
            duration = self._pcm.size / self._rate
            position = duration if self._finished_locked() else self._cursor / self._rate
            was_finished = self._finished_locked()
            target = max(0.0, min(position + float(seconds), duration))
            if was_finished and duration > 0 and target >= duration - 0.2:
                self._cursor = self._pcm.size
                return
            self._cursor = max(0, min(int(round(target * self._rate)), self._pcm.size))
            if was_finished:
                # Stepping back from the end leaves the clip paused at the new spot.
                self._paused = True
                self._playing = False

    def dismiss(self) -> None:
        stream = None
        with self._lock:
            stream = self._stream
            self._stream = None
            self._active = False
            self._playing = False
            self._paused = False
            self._complete = False
            self._cursor = 0
            self._pcm = np.zeros(0, dtype=np.float32)
            self._cues = []
        _close_stream(stream)

    def snapshot(self) -> dict:
        stream = None
        with self._lock:
            rate = self._rate or 48000
            duration = self._pcm.size / rate if rate else 0.0
            position = self._cursor / rate if rate else 0.0
            finished = self._finished_locked()
            if finished:
                position = duration
            starved = (
                self._active
                and not self._paused
                and not finished
                and self._cursor >= self._pcm.size
            )
            playing = self._transport_playing_locked() and not starved
            held = self._paused or (not self._playing and not starved and self._pcm.size > 0)
            data = {
                "active": self._active,
                "playing": playing,
                "paused": self._active and not finished and not playing and held,
                "buffering": self._active and not finished and not self._paused and (self._pcm.size == 0 or starved),
                "finished": finished,
                "position": position,
                "duration": duration,
            }
            if finished and self._stream is not None:
                stream = self._stream
                self._stream = None
        _close_stream(stream)
        return data

    def advance(self, frames: int) -> None:
        """Pull frames as the audio callback would. Used by tests and the device callback."""
        frames = max(0, int(frames))
        with self._lock:
            if frames <= 0 or not self._playing or self._paused or not self._active:
                return
            self._cursor = min(self._pcm.size, self._cursor + frames)
            if self._complete and self._cursor >= self._pcm.size:
                self._playing = False
                self._paused = False

    def _transport_playing_locked(self) -> bool:
        return self._active and self._playing and not self._paused and not self._finished_locked()

    def _finished_locked(self) -> bool:
        if not self._active or not self._complete or self._pcm.size == 0:
            return False
        slack = max(1, int(self._rate * _END_SLACK_SEC))
        return self._cursor >= self._pcm.size - slack

    def _ensure_stream(self) -> None:
        if not self.available:
            return
        with self._lock:
            if self._stream is not None or not self._active or self._pcm.size == 0:
                return
            rate = self._rate or 48000
        try:
            stream = self._sd.OutputStream(
                samplerate=rate,
                channels=1,
                dtype="float32",
                callback=self._on_audio,
                blocksize=2048,
            )
            stream.start()
        except Exception:
            logger.warning("Could not open the speech playback stream.", exc_info=True)
            with self._lock:
                if self._stream is None:
                    self._playing = False
            return
        with self._lock:
            if not self._active:
                leftover = stream
                stream = None
            else:
                leftover = self._stream
                self._stream = stream
                stream = None
        _close_stream(leftover)
        _close_stream(stream)

    def _on_audio(self, outdata, frames, _time_info, _status) -> None:
        with self._lock:
            outdata.fill(0)
            if not self._playing or self._paused or not self._active or self._pcm.size == 0:
                return
            start = self._cursor
            end = min(start + int(frames), self._pcm.size)
            count = end - start
            if count > 0:
                outdata[:count, 0] = self._pcm[start:end]
                self._cursor = end
            if self._complete and self._cursor >= self._pcm.size:
                self._playing = False
                self._paused = False


def _close_stream(stream: Optional[object]) -> None:
    if stream is None:
        return
    try:
        stream.stop()
    except Exception:
        pass
    try:
        stream.close()
    except Exception:
        pass
