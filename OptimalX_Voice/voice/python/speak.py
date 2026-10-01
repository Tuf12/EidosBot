#!/usr/bin/env python3
"""Kokoro TTS CLI for OptimalX Desktop.

Usage:
  speak.py --text "Hello" --voice bm_george --out /tmp/out.wav
  speak.py --list-voices
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

from device_resolve import is_explicit_device, min_vram_tts_bytes, resolve_device
from tts_chunk_split import split_for_tts_playback

REPO_ID = "hexgrad/Kokoro-82M"
SRC_SAMPLE_RATE = 24000
OUT_SAMPLE_RATE = 48000


def list_voices() -> list[str]:
    # Keep in sync with electron/voice/voices.js — HF catalog for hexgrad/Kokoro-82M.
    return [
        "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore",
        "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
        "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
        "am_onyx", "am_puck", "am_santa",
        "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
        "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
        "ef_dora", "em_alex", "em_santa",
        "ff_siwis",
        "hf_alpha", "hf_beta", "hm_omega", "hm_psi",
        "if_sara", "im_nicola",
        "jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo",
        "pf_dora", "pm_alex", "pm_santa",
        "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi",
        "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang",
    ]


def lang_code_for_voice(voice: str) -> str:
    prefix = (voice or "a")[0].lower()
    return {
        "a": "a",
        "b": "b",
        "e": "e",
        "f": "f",
        "h": "h",
        "i": "i",
        "j": "j",
        "p": "p",
        "z": "z",
    }.get(prefix, "a")


def _is_cuda_oom(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "out of memory" in msg and ("cuda" in msg or "cublas" in msg or "cudnn" in msg)


def _prepare_audio_int16(audio):
    import numpy as np

    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    audio = np.asarray(audio).squeeze()
    if audio.dtype != np.int16:
        audio = np.clip(audio, -1.0, 1.0)
        audio = (audio * 32767.0).astype(np.int16)
    if SRC_SAMPLE_RATE != OUT_SAMPLE_RATE:
        from math import gcd

        from scipy.signal import resample_poly

        g = gcd(SRC_SAMPLE_RATE, OUT_SAMPLE_RATE)
        up = OUT_SAMPLE_RATE // g
        down = SRC_SAMPLE_RATE // g
        audio = resample_poly(audio.astype(np.float32), up, down)
        audio = np.clip(audio, -32768, 32767).astype(np.int16)
    return audio


def _encode_wav(audio, out_path: str) -> tuple[int, float]:
    import soundfile as sf

    audio = _prepare_audio_int16(audio)
    sample_rate = OUT_SAMPLE_RATE
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    sf.write(out_path, audio, sample_rate, subtype="PCM_16")
    return sample_rate, round(float(len(audio)) / sample_rate, 3)


def encode_wav_base64(audio) -> tuple[str, float]:
    import base64
    import io

    import soundfile as sf

    audio = _prepare_audio_int16(audio)
    sample_rate = OUT_SAMPLE_RATE
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, subtype="PCM_16", format="WAV")
    duration_sec = round(float(len(audio)) / sample_rate, 3)
    return base64.b64encode(buf.getvalue()).decode("ascii"), duration_sec


class KokoroRuntime:
    """Keep one KModel + per-language KPipeline instances warm in the worker."""

    def __init__(self, device: str | None = None):
        self.device_name = resolve_device(device, min_free_bytes=min_vram_tts_bytes())
        self._model = None
        self._pipes: dict[str, object] = {}

    def warm(self, voice: str = "bm_george") -> None:
        self._pipe_for_voice(voice or "bm_george")

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        from kokoro import KModel

        model = KModel(repo_id=REPO_ID)
        if self.device_name == "cuda":
            model = model.cuda()
        else:
            model = model.cpu()
        model.eval()
        self._model = model
        return model

    def _pipe_for_voice(self, voice: str):
        from kokoro import KPipeline

        voice = voice or "bm_george"
        lang = lang_code_for_voice(voice)
        if lang not in self._pipes:
            self._pipes[lang] = KPipeline(
                lang_code=lang,
                model=self._ensure_model(),
                device=self.device_name,
                repo_id=REPO_ID,
            )
        return self._pipes[lang]

    def synthesize(self, text: str, voice: str, out_path: str, speed: float) -> dict:
        import numpy as np

        voice = voice or "bm_george"
        lang = lang_code_for_voice(voice)
        pipe = self._pipe_for_voice(voice)
        chunks = []
        for _gs, _ps, audio in pipe(text, voice=voice, speed=speed):
            chunks.append(audio)
        if not chunks:
            raise RuntimeError("Kokoro produced no audio")
        audio = np.concatenate(chunks)
        sample_rate, duration_sec = _encode_wav(audio, out_path)
        return {
            "ok": True,
            "outPath": out_path,
            "voice": voice,
            "langCode": lang,
            "device": self.device_name,
            "sampleRate": sample_rate,
            "durationSec": duration_sec,
        }

    def synthesize_stream(
        self,
        text: str,
        voice: str,
        speed: float,
        pieces: list[str] | None = None,
    ):
        """Yield one WAV chunk per growing head/body piece (then Kokoro segments)."""
        voice = voice or "bm_george"
        lang = lang_code_for_voice(voice)
        pipe = self._pipe_for_voice(voice)
        parts = [str(part).strip() for part in (pieces or []) if str(part).strip()]
        if not parts:
            parts = split_for_tts_playback(text)
        emitted = 0
        for part in parts:
            for _gs, _ps, audio in pipe(part, voice=voice, speed=speed):
                audio_b64, duration_sec = encode_wav_base64(audio)
                yield {
                    "index": emitted,
                    "audioBase64": audio_b64,
                    "mimeType": "audio/wav",
                    "durationSec": duration_sec,
                    "voice": voice,
                    "langCode": lang,
                    "device": self.device_name,
                    "sampleRate": OUT_SAMPLE_RATE,
                }
                emitted += 1
        if emitted == 0:
            raise RuntimeError("Kokoro produced no audio")

    def reset_device(self, device_name: str) -> None:
        self.device_name = device_name
        self._model = None
        self._pipes = {}


def synthesize_stream(
    text: str,
    voice: str,
    device: str | None,
    speed: float,
    runtime: KokoroRuntime | None = None,
    pieces: list[str] | None = None,
):
    device_name = resolve_device(device, min_free_bytes=min_vram_tts_bytes())
    active = runtime
    if active is None:
        active = KokoroRuntime(device)
    elif device in ("cpu", "cuda") and device != active.device_name:
        active.reset_device(device_name)

    try:
        yield from active.synthesize_stream(text, voice, speed, pieces)
    except Exception as exc:
        if active.device_name == "cuda" and _is_cuda_oom(exc):
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            active.reset_device("cpu")
            yield from active.synthesize_stream(text, voice, speed, pieces)
            return
        raise


def synthesize(
    text: str,
    voice: str,
    out_path: str,
    device: str | None,
    speed: float,
    runtime: KokoroRuntime | None = None,
) -> dict:
    device_name = resolve_device(device, min_free_bytes=min_vram_tts_bytes())
    active = runtime
    if active is None:
        active = KokoroRuntime(device)
    elif device in ("cpu", "cuda") and device != active.device_name:
        active.reset_device(device_name)

    try:
        result = active.synthesize(text, voice, out_path, speed)
        if active.device_name == "cpu" and not is_explicit_device(device):
            result["deviceNote"] = "cuda_unavailable_or_low_vram"
        return result
    except Exception as exc:
        if active.device_name == "cuda" and _is_cuda_oom(exc):
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            active.reset_device("cpu")
            result = active.synthesize(text, voice, out_path, speed)
            result["deviceFallback"] = "cpu"
            result["deviceNote"] = "cuda_oom"
            return result
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="OptimalX Kokoro TTS")
    parser.add_argument("--text", default="")
    parser.add_argument("--voice", default="bm_george")
    parser.add_argument("--out", default="")
    parser.add_argument("--device", default="")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--list-voices", action="store_true")
    args = parser.parse_args()

    try:
        if args.list_voices:
            print(json.dumps({"ok": True, "voices": list_voices()}))
            return 0
        text = (args.text or "").strip()
        if not text:
            raise ValueError("Missing --text")
        if not args.out:
            raise ValueError("Missing --out")
        runtime = KokoroRuntime(args.device or None)
        runtime.warm(args.voice)
        result = synthesize(
            text=text,
            voice=args.voice,
            out_path=args.out,
            device=args.device or None,
            speed=float(args.speed or 1.0),
            runtime=runtime,
        )
        print(json.dumps(result))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "trace": traceback.format_exc(),
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
