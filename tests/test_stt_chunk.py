"""Parakeet long-audio slicing stays under the 240s sherpa decode cap."""

import sys
import tempfile
import unittest
import wave
from pathlib import Path

_VOICE_PY = Path(__file__).resolve().parents[1] / "OptimalX_Voice" / "voice" / "python"
sys.path.insert(0, str(_VOICE_PY))

from stt_chunk import (  # noqa: E402
    CHUNK_SECONDS,
    MODEL_MAX_SECONDS,
    SAFE_SINGLE_PASS_SECONDS,
    plan_slices,
    stitch_transcripts,
)
from eidos.voice.stt import _transcribe_timeout_seconds  # noqa: E402


def _assert_cover(test, slices, n_samples, safe_samples):
    test.assertTrue(slices)
    test.assertEqual(slices[0][0], 0)
    test.assertEqual(slices[-1][1], n_samples)
    previous_start = -1
    covered_to = 0
    for start, end in slices:
        test.assertGreater(start, previous_start)
        test.assertGreater(end, start)
        test.assertLessEqual(end - start, safe_samples)
        test.assertLessEqual(start, covered_to)
        covered_to = max(covered_to, end)
        previous_start = start
    test.assertEqual(covered_to, n_samples)


class TestParakeetChunkPlan(unittest.TestCase):
    def test_short_audio_is_one_slice(self):
        rate = 16000
        for seconds in (0, 1, 30, 210):
            n = seconds * rate
            slices = plan_slices(n, rate)
            if seconds == 0:
                self.assertEqual(slices, [])
            else:
                self.assertEqual(slices, [(0, n)])

    def test_over_cap_stays_under_model_limit(self):
        rate = 16000
        safe = int(SAFE_SINGLE_PASS_SECONDS * rate)
        self.assertLess(CHUNK_SECONDS, MODEL_MAX_SECONDS)
        self.assertLess(SAFE_SINGLE_PASS_SECONDS, MODEL_MAX_SECONDS)
        for seconds in (211, 400, 1000):
            slices = plan_slices(seconds * rate, rate)
            self.assertGreater(len(slices), 1)
            _assert_cover(self, slices, seconds * rate, safe)

    def test_pause_moves_the_cut_and_keeps_overlap(self):
        rate = 10
        n = 100 * rate
        rms = [1.0] * n
        for index in range(345, 360):
            rms[index] = 0.0
        slices = plan_slices(
            n,
            rate,
            rms=rms,
            frame_size=1,
            chunk_seconds=40,
            overlap_seconds=1,
            safe_single_pass_seconds=50,
            silence_search_seconds=10,
            min_chunk_seconds=5,
            silence_window_frames=3,
        )
        self.assertGreaterEqual(slices[0][1], 345)
        self.assertLessEqual(slices[0][1], 365)
        self.assertLess(slices[1][0], slices[0][1])
        self.assertGreaterEqual(slices[0][1] - slices[1][0], rate)  # >= 1s overlap
        _assert_cover(self, slices, n, 50 * rate)

    def test_flat_audio_cuts_at_the_chunk_target(self):
        rate = 10
        n = 100 * rate
        slices = plan_slices(
            n,
            rate,
            rms=[1.0] * n,
            frame_size=1,
            chunk_seconds=40,
            overlap_seconds=1,
            safe_single_pass_seconds=50,
            silence_search_seconds=10,
            min_chunk_seconds=5,
            silence_window_frames=3,
        )
        self.assertEqual(slices[0], (0, 40 * rate))
        self.assertEqual(slices[1][0], 39 * rate)


class TestTranscriptStitch(unittest.TestCase):
    def test_joins_overlap_phrase(self):
        text = stitch_transcripts(
            [
                "we need chunking so long messages work",
                "long messages work past the model cap",
            ]
        )
        self.assertEqual(text, "we need chunking so long messages work past the model cap")

    def test_keeps_punctuation_from_the_first_copy(self):
        self.assertEqual(
            stitch_transcripts(["Hello world.", "world. How are you"]),
            "Hello world. How are you",
        )

    def test_single_function_word_does_not_drop_text(self):
        self.assertEqual(
            stitch_transcripts(["I went to the store", "the cat sat down"]),
            "I went to the store the cat sat down",
        )

    def test_skips_empty_pieces(self):
        self.assertEqual(stitch_transcripts(["", "  hello  ", ""]), "hello")


class TestTranscribeTimeout(unittest.TestCase):
    def test_scales_with_wav_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            short = Path(tmp) / "short.wav"
            long = Path(tmp) / "long.wav"
            _write_silence(short, seconds=10, rate=8000)
            _write_silence(long, seconds=400, rate=8000)
            self.assertEqual(_transcribe_timeout_seconds(str(short)), 90)
            self.assertEqual(_transcribe_timeout_seconds(str(long)), int(400 * 1.5 + 45))
            self.assertEqual(_transcribe_timeout_seconds(str(Path(tmp) / "missing.wav")), 600)


def _write_silence(path: Path, seconds: int, rate: int) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * (seconds * rate))


if __name__ == "__main__":
    unittest.main()
