"""Tests for EidosBot Voice Subsystem (Kokoro-82M TTS + Parakeet STT + Audio Capture)."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from eidos.safety.risk_classifier import RiskClassifier, RiskLevel
from eidos.voice.audio_capture import AudioRecorder
from eidos.voice.chunker import split_for_tts_playback, split_speech_chunks
from eidos.voice.helpers import (
    find_voice_python,
    find_voice_scripts_dir,
    resolve_voice_runtime,
    voice_python_candidates,
    voice_scripts_candidates,
)
from eidos.voice.sanitize import sanitize_for_tts
from eidos.voice.stt import ParakeetSTT
from eidos.voice.playback import WavPlayback
from eidos.voice.tts import KokoroTTS
from eidos.voice.tts_service import prepare_stream, resolve_speed, resolve_voice
from eidos.voice.voices import DEFAULT_VOICE_ID, normalize_voice_id
from eidos.voice.voice_manager import VoiceManager


class TestVoiceHelpers(unittest.TestCase):
    """Test environment discovery for voice python and scripts."""

    def test_find_voice_python(self):
        py = find_voice_python()
        self.assertTrue(os.path.exists(py))
        self.assertTrue(os.access(py, os.X_OK))

    def test_find_voice_scripts_dir(self):
        scripts_dir = find_voice_scripts_dir()
        if scripts_dir:
            self.assertTrue((Path(scripts_dir) / "speak.py").exists())
            self.assertTrue((Path(scripts_dir) / "transcribe.py").exists())

    def test_voice_python_candidate_order_matches_optimalx(self):
        candidates = voice_python_candidates()
        self.assertGreaterEqual(len(candidates), 2)
        self.assertIn("OptimalX", str(candidates[0]))
        self.assertIn("optimalx-voice", str(candidates[1]))

    def test_bundled_optimalx_voice_scripts_in_candidates(self):
        bundled = voice_scripts_candidates()[0]
        self.assertIn("OptimalX_Voice", str(bundled))
        self.assertTrue((bundled / "speak.py").exists())
        self.assertTrue((bundled / "transcribe.py").exists())

    def test_resolve_voice_runtime_logs_ready_when_available(self):
        runtime = resolve_voice_runtime()
        self.assertIsNotNone(runtime.python_path)
        if runtime.scripts_dir:
            self.assertTrue((Path(runtime.scripts_dir) / "speak.py").exists())


class TestKokoroTTS(unittest.TestCase):
    """Test Kokoro-82M Text-to-Speech integration."""

    def setUp(self):
        self.tts = KokoroTTS()

    def test_initialization(self):
        self.assertIsNotNone(self.tts.python_path)
        self.assertEqual(self.tts.default_voice, "bm_george")
        self.assertEqual(self.tts.default_speed, 1.0)
        self.assertEqual(self.tts.device, "cpu")

    def test_list_voices(self):
        voices = self.tts.list_voices()
        self.assertIsInstance(voices, list)
        self.assertIn("bm_george", voices)

    def test_empty_text_error(self):
        with self.assertRaises(ValueError):
            self.tts.synthesize("")

    @patch.object(WavPlayback, "play", return_value=True)
    def test_play_audio(self, _mock_play):
        with tempfile.NamedTemporaryFile(suffix=".wav") as tf:
            tf.write(b"RIFFdummywavdata")
            tf.flush()
            success = self.tts.play_audio(tf.name, blocking=True)
            self.assertTrue(success)

    def test_stop_playback(self):
        self.tts._playback = MagicMock()
        self.tts.stop()
        self.tts._playback.stop.assert_called_once()


class TestParakeetSTT(unittest.TestCase):
    """Test NVIDIA Parakeet TDT INT8 ONNX Speech-to-Text integration."""

    def setUp(self):
        self.stt = ParakeetSTT()

    def test_initialization(self):
        self.assertIsNotNone(self.stt.python_path)
        self.assertEqual(self.stt.model_name, "parakeet-tdt-0.6b-v2-int8")

    def test_status(self):
        if self.stt.is_available:
            st = self.stt.status()
            self.assertIsInstance(st, dict)
            self.assertTrue(st.get("ok", False))

    def test_missing_file_raises(self):
        if self.stt.is_available:
            with self.assertRaises(FileNotFoundError):
                self.stt.transcribe("/tmp/non_existent_audio_xyz123.wav")


class TestAudioRecorder(unittest.TestCase):
    """Test microphone audio recording."""

    def setUp(self):
        self.recorder = AudioRecorder()

    def test_initialization(self):
        self.assertFalse(self.recorder.is_recording)

    @patch("subprocess.Popen")
    def test_start_and_stop_recording_mock(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tf.write(b"RIFF1234")
            out_file = tf.name

        with patch("os.getpgid", return_value=99999), patch("os.killpg") as mock_killpg:
            res_path = self.recorder.start_recording(out_path=out_file)
            self.assertEqual(res_path, out_file)
            self.assertTrue(self.recorder.is_recording)

            stopped_path = self.recorder.stop_recording()
            self.assertEqual(stopped_path, out_file)
            self.assertFalse(self.recorder.is_recording)
            mock_killpg.assert_called_once()

        if os.path.exists(out_file):
            os.remove(out_file)


class TestVoiceManager(unittest.TestCase):
    """Test coordinated VoiceManager workflow."""

    def setUp(self):
        self.vm = VoiceManager()

    def test_callbacks_registration(self):
        recorded_calls = []
        state_calls = []

        self.vm.register_on_transcribed(lambda t: recorded_calls.append(t))
        self.vm.register_on_state_change(lambda s: state_calls.append(s))

        self.assertEqual(len(self.vm._on_transcribed_callbacks), 1)
        self.assertEqual(len(self.vm._on_state_change_callbacks), 1)

    def test_risk_classification(self):
        level, reason = RiskClassifier.classify_action("speak_aloud", {"message": "Hello"})
        self.assertEqual(level, RiskLevel.LEVEL_0_PASSIVE)

        level, reason = RiskClassifier.classify_action("speak", {"text": "Alert"})
        self.assertEqual(level, RiskLevel.LEVEL_0_PASSIVE)

    def test_speak_response_uses_streamer(self):
        self.vm.speech_stream = MagicMock()
        self.vm.speak_response("Hello there, this is a chat reply.")
        self.vm.speech_stream.speak_text.assert_called_once()


class TestVoiceCatalog(unittest.TestCase):
    """OptimalX voice catalog and TTS prep helpers."""

    def test_default_voice_is_george(self):
        self.assertEqual(DEFAULT_VOICE_ID, "bm_george")
        self.assertEqual(normalize_voice_id(None), "bm_george")
        self.assertEqual(normalize_voice_id("not-a-voice"), "bm_george")
        self.assertEqual(normalize_voice_id("af_bella"), "af_bella")

    def test_speed_clamped_like_optimalx(self):
        self.assertEqual(resolve_speed(0.1), 0.5)
        self.assertEqual(resolve_speed(3.0), 2.0)
        self.assertEqual(resolve_speed(1.0), 1.0)

    def test_prepare_stream_splits_long_reply(self):
        text = (
            "This is the opening sentence of the reply, kept fairly short. "
            "The second sentence adds more detail so the second loading clip can run longer than the first. "
            "Now we stretch into a longer third passage that should land near the steady three-hundred-eighty "
            "character clip size used once the first two loading steps have already started speech for the user."
        )
        prepared = prepare_stream(text, voice="bm_george", speed=1.0)
        self.assertIsNotNone(prepared)
        self.assertEqual(prepared.voice, "bm_george")
        self.assertGreaterEqual(len(prepared.pieces), 2)
        self.assertEqual(" ".join(prepared.pieces), prepared.text)


class TestProgressiveChunker(unittest.TestCase):
    """OptimalX-style growing-window TTS clip splitting."""

    def test_empty_and_short_text(self):
        from eidos.voice.chunker import text_for_speech

        self.assertEqual(split_speech_chunks(""), [])
        self.assertEqual(split_speech_chunks("  hi  "), ["hi"])
        self.assertEqual(text_for_speech(""), "")

    def test_strips_markdown_for_speech(self):
        from eidos.voice.chunker import text_for_speech

        raw = "# Hello\n\nThis is **bold** and `code`.\n\n```python\nprint('nope')\n```\nDone."
        spoken = text_for_speech(raw)
        self.assertNotIn("```", spoken)
        self.assertNotIn("**", spoken)
        self.assertNotIn("# Hello", spoken)
        self.assertIn("Hello", spoken)
        self.assertIn("bold", spoken)
        self.assertIn("code", spoken)
        self.assertIn("Done.", spoken)

    def test_sanitize_currency_and_urls(self):
        spoken = sanitize_for_tts("Visit https://example.com for $12.50 off.")
        self.assertNotIn("https://", spoken)
        self.assertIn("dollar", spoken)

    def test_short_greeting_is_single_chunk(self):
        text = (
            "I'm doing great, thank you for asking! Everything is running smoothly "
            "on my end. How can I help you with your work today?"
        )
        chunks = split_for_tts_playback(text)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], text)

    def test_progressive_windows_grow(self):
        text = (
            "This is the opening sentence of the reply, kept fairly short. "
            "The second sentence adds more detail so the second loading clip can run longer than the first. "
            "Now we stretch into a longer third passage that should land near the steady three-hundred-eighty "
            "character clip size used once the first two loading steps have already started speech for the user. "
            "We keep talking after that with another full paragraph of spoken prose so a later clip is required, "
            "still using the larger steady size until the assistant response is fully spoken aloud without a long "
            "buffer pause between the on-screen text and the first words the user actually hears from the speakers."
        )
        chunks = split_for_tts_playback(text)
        self.assertGreaterEqual(len(chunks), 3)
        self.assertLessEqual(len(chunks[0]), 140)
        self.assertGreaterEqual(len(chunks[0]), 40)
        self.assertGreater(len(chunks[1]), len(chunks[0]))
        self.assertEqual(" ".join(chunks), text)

    def test_streaming_extract_waits_until_first_window(self):
        from eidos.voice.chunker import extract_next_chunk

        chunk, rest = extract_next_chunk("Hello there.", target=140, finished=False)
        self.assertEqual(chunk, "")
        self.assertIn("Hello", rest)

        chunk, rest = extract_next_chunk("Hello there.", target=140, finished=True)
        self.assertEqual(chunk, "Hello there.")
        self.assertEqual(rest, "")


class TestSpeechStreamer(unittest.TestCase):
    """Queued synth-while-playing pipeline."""

    def test_first_clip_synthesized_before_later_clips_play(self):
        import threading

        from eidos.voice.speech_stream import SpeechStreamer

        tts = MagicMock()
        synthesized = []
        played = []
        first_synth = threading.Event()
        first_play = threading.Event()
        all_played = threading.Event()

        text = (
            "This is the first spoken loading clip for the assistant reply. "
            "The second clip is intentionally longer so synthesis can stay ahead of playback without gaps. "
            "Now we stretch into a longer third passage that should land near the steady three-hundred-eighty "
            "character clip size used once the first two loading steps have already started speech for the user. "
            "A final stretch of spoken text keeps the pipeline busy so the third clip is long enough to "
            "stand on its own instead of being folded back into the second loading step of the reply."
        )
        expected = split_for_tts_playback(text)

        def synth_stream(text, voice=None, speed=None, pieces=None):
            for piece in pieces or [text]:
                synthesized.append(piece)
                if len(synthesized) == 1:
                    first_synth.set()
                yield f"/tmp/clip-{len(synthesized)}.wav"

        playback = MagicMock()
        playback.uses_native_audio = False

        def play(wav_path, blocking=False, interrupt=True):
            played.append((wav_path, interrupt))
            if len(played) == 1:
                first_play.set()
            if len(played) >= len(expected):
                all_played.set()

        tts.synthesize_stream.side_effect = synth_stream
        tts.play_audio.side_effect = play
        tts._playback = playback

        streamer = SpeechStreamer(tts)
        self.assertTrue(streamer.speak_text(text))
        self.assertTrue(first_synth.wait(timeout=2.0))
        self.assertTrue(first_play.wait(timeout=2.0))
        self.assertTrue(all_played.wait(timeout=2.0))
        self.assertEqual(synthesized, expected)
        self.assertLessEqual(len(synthesized[0]), 140)
        self.assertFalse(played[0][1])  # interrupt=False between clips

    def test_stop_discards_queued_clips(self):
        from eidos.voice.speech_stream import SpeechStreamer

        tts = MagicMock()
        tts.synthesize_stream.return_value = iter(["/tmp/clip.wav"])
        streamer = SpeechStreamer(tts)
        streamer.speak_text("Hello world. This should be cancelled immediately by stop.")
        streamer.stop()
        tts.stop.assert_called()


if __name__ == "__main__":
    unittest.main()
