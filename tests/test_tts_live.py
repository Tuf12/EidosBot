"""Unit tests for the single-track phone TTS stream. Does not start the app."""

import struct
import threading
import time
import unittest
import uuid

from eidos.gateway.tts_live import (
    SAMPLE_RATE,
    LiveTtsSession,
    extract_pcm,
    patch_wav_sizes,
    primer_frame,
    resample_mono16,
    run_live_session,
    to_mono16,
    valid_session_id,
    wav_header,
)


def _wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    return wav_header(rate, 1, 16, len(pcm)) + pcm


class TestWavBytes(unittest.TestCase):
    def test_roundtrip_pcm(self):
        pcm = struct.pack("<hhh", 0, 1000, -1000)
        got, rate, channels, bits = extract_pcm(_wav(pcm))
        self.assertEqual(got, pcm)
        self.assertEqual(rate, SAMPLE_RATE)
        self.assertEqual(channels, 1)
        self.assertEqual(bits, 16)

    def test_patch_replaces_unknown_length(self):
        pcm = b"\x01\x00" * 8
        raw = wav_header(SAMPLE_RATE, 1, 16, 0xFFFFFFFF) + pcm
        patched = patch_wav_sizes(raw)
        self.assertEqual(struct.unpack_from("<I", patched, 40)[0], len(pcm))
        self.assertEqual(struct.unpack_from("<I", patched, 4)[0], len(patched) - 8)

    def test_stereo_to_mono_and_resample(self):
        stereo = struct.pack("<hhhh", 0, 1000, 0, -1000)
        mono = to_mono16(stereo, 2, 16)
        self.assertEqual(struct.unpack("<hh", mono), (500, -500))
        up = resample_mono16(struct.pack("<hh", 0, 1000), 24000, 48000)
        self.assertGreaterEqual(len(up) // 2, 3)

    def test_primer_frame_length(self):
        frame = primer_frame(4800, [1])
        self.assertEqual(len(frame), 4800 * 2)

    def test_session_id(self):
        self.assertTrue(valid_session_id(str(uuid.uuid4())))
        self.assertFalse(valid_session_id("../etc/passwd"))
        self.assertFalse(valid_session_id("not-a-uuid"))


class TestLiveSession(unittest.TestCase):
    def test_ready_text_skips_primer(self):
        pcm = struct.pack("<hh", 5, -5)
        path_box = {}

        def synth(text, voice, speed):
            self.assertEqual(text, "Hello there")
            import tempfile
            handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            handle.write(_wav(pcm))
            handle.close()
            path_box["path"] = handle.name
            yield handle.name

        session = LiveTtsSession(str(uuid.uuid4()))
        session.submit("Hello there", "bm_george", None, "Hello there")
        run_live_session(session, synth)
        self.assertTrue(session.complete)
        self.assertIsNone(session.error)
        self.assertEqual(session.speech_start, 0)
        self.assertEqual(session.cues, [0.0])
        self.assertAlmostEqual(session.duration, 2 / SAMPLE_RATE)
        spoken, *_ = extract_pcm(session.wav_bytes())
        self.assertEqual(spoken, pcm)
        self.assertFalse(__import__("os").path.exists(path_box["path"]))

    def test_primer_runs_until_text_arrives(self):
        pcm = struct.pack("<h", 7)

        def synth(text, voice, speed):
            import tempfile
            handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            handle.write(_wav(pcm))
            handle.close()
            yield handle.name

        session = LiveTtsSession(str(uuid.uuid4()))
        worker = threading.Thread(target=run_live_session, args=(session, synth))
        worker.start()
        time.sleep(0.35)
        session.submit("After primer", None, None, "After primer")
        worker.join(timeout=5)
        self.assertTrue(session.complete)
        self.assertGreaterEqual(session.speech_start, 0.1)
        self.assertEqual(session.cues[0], session.speech_start)
        spoken, *_ = extract_pcm(session.wav_bytes())
        self.assertTrue(spoken.endswith(pcm))


if __name__ == "__main__":
    unittest.main()
