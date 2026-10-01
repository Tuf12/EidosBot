"""Queued TTS pipeline: synthesize the next clip while the current one plays."""

from __future__ import annotations

import logging
import os
import queue
import threading
from typing import List, Optional

from eidos.voice.tts import KokoroTTS
from eidos.voice.tts_service import prepare_stream

logger = logging.getLogger(__name__)

_SENTINEL = object()


class SpeechStreamer:
    """Progressive Kokoro playback aligned with OptimalX Desktop read-aloud."""

    def __init__(self, tts: KokoroTTS):
        self.tts = tts
        self._lock = threading.Lock()
        self._generation = 0
        self._synth_q: queue.Queue = queue.Queue()
        self._play_q: queue.Queue = queue.Queue()
        self._feed_buffer = ""
        self._feed_stage = 0
        self._started = False
        self._is_synthesizing = False
        self._is_playing = False
        self._feed_voice: Optional[str] = None
        self._feed_speed: Optional[float] = None

    def is_speaking(self) -> bool:
        """Return True if speech streaming is actively synthesizing or playing audio."""
        return (
            self._is_synthesizing
            or self._is_playing
            or (not self._synth_q.empty())
            or (not self._play_q.empty())
            or getattr(self.tts, "is_playing", False)
        )

    def _ensure_workers(self) -> None:
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._synth_loop, name="eidos-tts-synth", daemon=True).start()
        threading.Thread(target=self._play_loop, name="eidos-tts-play", daemon=True).start()

    def speak_text(
        self,
        text: str,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
    ) -> bool:
        """Chunk a complete reply and start speaking as soon as the first clip is ready."""
        prepared = prepare_stream(
            text,
            voice=voice,
            speed=speed,
            default_voice=self.tts.default_voice,
            default_speed=self.tts.default_speed,
        )
        if not prepared or not prepared.pieces:
            logger.debug("Speech streamer skipped empty/non-spoken text.")
            return False

        self.stop()
        self._ensure_workers()
        with self._lock:
            gen = self._generation
        logger.info(
            "Speaking reply in %d TTS piece(s) (first %d chars, voice=%s, speed=%s).",
            len(prepared.pieces),
            len(prepared.pieces[0]),
            prepared.voice,
            prepared.speed,
        )
        self._synth_q.put((gen, prepared.text, prepared.voice, prepared.speed, prepared.pieces))
        return True

    def begin(self, voice: Optional[str] = None, speed: Optional[float] = None) -> None:
        """Start a streaming utterance (feed tokens, then finish)."""
        self.stop()
        self._ensure_workers()
        with self._lock:
            self._feed_voice = voice
            self._feed_speed = speed
            self._feed_stage = 0

    def feed(self, delta: str) -> None:
        """Append streaming text and emit clips when OptimalX window targets are met."""
        if not delta:
            return
        self._ensure_workers()
        with self._lock:
            self._feed_buffer += delta
            voice = self._feed_voice
            speed = self._feed_speed
            gen = self._generation
            emitted: List[str] = []
            while True:
                piece, remainder, stage = self._take_feed_piece(self._feed_buffer, self._feed_stage)
                if not piece:
                    break
                self._feed_buffer = remainder
                self._feed_stage = stage
                emitted.append(piece)
        for piece in emitted:
            self._synth_q.put((gen, piece, voice, speed, [piece]))

    def finish(self) -> None:
        """Flush remaining buffered stream text as final clip(s)."""
        with self._lock:
            voice = self._feed_voice
            speed = self._feed_speed
            gen = self._generation
            buffer = self._feed_buffer
            self._feed_buffer = ""
            self._feed_stage = 0

        prepared = prepare_stream(
            buffer,
            voice=voice,
            speed=speed,
            default_voice=self.tts.default_voice,
            default_speed=self.tts.default_speed,
        )
        if not prepared:
            return
        for piece in prepared.pieces:
            self._synth_q.put((gen, piece, prepared.voice, prepared.speed, [piece]))

    def stop(self) -> None:
        """Cancel queued clips and stop the current player."""
        self._is_synthesizing = False
        self._is_playing = False
        with self._lock:
            self._generation += 1
            self._feed_buffer = ""
            self._feed_stage = 0
            self._feed_voice = None
            self._feed_speed = None
        self._drain(self._synth_q)
        self._drain(self._play_q)
        self.tts.stop()

    @staticmethod
    def _drain(q: queue.Queue) -> None:
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                break

    def _take_feed_piece(self, buffer: str, stage: int) -> tuple[str, str, int]:
        from eidos.voice.chunker import BODY_MAX, WINDOWS, take_window

        text = (buffer or "").strip()
        if not text:
            return "", "", stage
        if stage < len(WINDOWS):
            min_chars, max_chars = WINDOWS[stage]
        else:
            min_chars, max_chars = 1, BODY_MAX
        if len(text) < min_chars:
            return "", buffer, stage
        piece = take_window(text, min_chars, max_chars)
        if not piece:
            return "", buffer, stage
        remainder = text[len(piece) :].lstrip()
        next_stage = stage + 1 if stage < len(WINDOWS) else stage
        return piece, remainder, next_stage

    def _synth_loop(self) -> None:
        while True:
            item = self._synth_q.get()
            if item is _SENTINEL:
                return
            gen, text, voice, speed, pieces = item
            if gen != self._generation:
                continue
            self._is_synthesizing = True
            try:
                # Stream each Kokoro chunk to the play queue as soon as it is ready
                # (OptimalX speakStream onChunk — do NOT buffer the full utterance first).
                for wav_path in self.tts.synthesize_stream(
                    text=text,
                    voice=voice,
                    speed=speed,
                    pieces=pieces,
                ):
                    if gen != self._generation:
                        self._cleanup_wav(wav_path)
                        break
                    self._play_q.put((gen, wav_path))
            except Exception as e:
                logger.error("TTS clip synthesis failed: %s", e)
                continue
            finally:
                self._is_synthesizing = False

    def _play_loop(self) -> None:
        """Play Kokoro stream chunks back-to-back as they arrive."""
        while True:
            item = self._play_q.get()
            if item is _SENTINEL:
                return
            gen, wav_path = item
            if gen != self._generation:
                self._cleanup_wav(wav_path)
                continue
            self._is_playing = True
            try:
                playback = self.tts._playback
                if playback.uses_native_audio:
                    pcm, sample_rate = playback.load_pcm(wav_path)
                    playback.play_pcm(pcm, sample_rate, blocking=True)
                else:
                    self.tts.play_audio(wav_path, blocking=True, interrupt=False)
            except Exception as e:
                logger.error("TTS clip playback failed: %s", e)
            finally:
                self._is_playing = False
                self._cleanup_wav(wav_path)

    @staticmethod
    def _cleanup_wav(wav_path: str) -> None:
        try:
            if wav_path and os.path.exists(wav_path):
                os.remove(wav_path)
        except Exception:
            pass
