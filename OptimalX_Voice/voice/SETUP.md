# Desktop voice (Kokoro TTS + Parakeet STT)

Local-first voice for OptimalX Desktop.

| Role | Engine | Default |
|------|--------|---------|
| TTS (Eidos voice) | [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) via Python (`kokoro` + warm worker) | **`bm_george`** (British male) |
| STT (mic) | NVIDIA **Parakeet** TDT 0.6B v2 via **sherpa-onnx INT8** | Push-to-talk → WAV → transcript |

The [kokorottsai.com](https://kokorottsai.com/#tryonline) demo shows a **subset** of voices. OptimalX exposes the **full Kokoro-82M pack (54 voices)** in AI Settings so you can audition them in-app.

## One-time setup

```bash
bash electron/voice/python/setup.sh
```

Creates or links `~/OptimalX/voice/venv` and downloads the Parakeet INT8 ONNX pack (~600 MB) to `~/OptimalX/voice/models/`.

- If `~/.venvs/optimalx-voice` already has a working **CUDA** PyTorch (RTX 50-series / Blackwell `cu128`), setup **symlinks** that venv in.
- Otherwise it creates a fresh venv and, on compute capability ≥ 12, installs `torch` from the `cu128` index.

Optional (phonemizer reliability; Kokoro also vendors `espeakng-loader`):

```bash
sudo apt install espeak-ng
```

## GPU note (RTX 50-series / Blackwell)

Blackwell needs **PyTorch built with CUDA 12.8+** (`sm_120`). A `cu118`/`cu124` wheel will show CUDA “available” then fail kernels. Use:

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
```

**TTS always runs on CPU** so the chat LLM can keep the GPU. The Kokoro worker starts at app launch, loads `KModel`, then synthesizes a discarded `"Ready."` so first-inference kernels are warm. Read-aloud uses **growing chunks** in one stream (about 60–120, then 160–220, then 380 characters, split on sentence ends) so the first WAV starts quickly and later pieces stay ahead of playback. CUDA helpers remain in the Python scripts for a future offline converter; the live speak/warm path ignores them.

**STT** defaults to **CPU** — the INT8 ONNX Parakeet model is small (~600 MB) and typically faster than Whisper on CPU, so it leaves GPU headroom for Ollama (e.g. Gemma 26B).

Long dictation uses **rolling slices** (45s windows, 1.0s overlap — extended from Android’s 20s / 0.5s) with live text in the composer while the mic is open. Matches the Android Gemma scribe / `TranscriptAssembler` pattern.

## Config (`~/.config/optimalx-desktop/config.json` → `voice`)

```json
{
  "voice": {
    "ttsVoice": "bm_george",
    "ttsSpeed": 1,
    "sttModel": "parakeet-tdt-0.6b-v2-int8",
    "device": "cpu",
    "pythonPath": null
  }
}
```

`pythonPath` optional — defaults to `~/OptimalX/voice/venv/bin/python`, then `~/.venvs/optimalx-voice/bin/python`.

Legacy `sttModel` value `nvidia/parakeet-tdt-0.6b-v2` (NeMo) is mapped automatically to the INT8 ONNX pack.

## Smoke tests

```bash
~/OptimalX/voice/venv/bin/python electron/voice/python/speak.py \
  --text "Hello from Eidos." --voice bm_george --out /tmp/eidos.wav

~/OptimalX/voice/venv/bin/python electron/voice/python/transcribe.py --status

~/OptimalX/voice/venv/bin/python electron/voice/python/transcribe.py \
  --wav /tmp/eidos.wav
```

In the app: **AI Settings → Voice** — pick a voice (default George), Preview; chat mic uses Parakeet when enabled in the overflow menu.
