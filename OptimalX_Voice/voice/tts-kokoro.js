'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const {
  ensureVoiceDirs,
  resolvePythonBin,
  CACHE_DIR,
  TTS_INFERENCE_DEVICE,
} = require('./paths');
const { createKokoroWorker } = require('./kokoro-worker');
const { sanitizeForTts } = require('./sanitize');
const { splitForTtsPlayback } = require('./tts-chunk-split');
const { normalizeVoiceId, getVoice, listVoices, DEFAULT_VOICE_ID } = require('./voices');

function createTtsService(config) {
  ensureVoiceDirs();
  const worker = createKokoroWorker(config);
  let speakGeneration = 0;

  async function getStatus({ probe = false } = {}) {
    const bin = resolvePythonBin(config);
    // Settings / status panels must not cold-start Kokoro (torch import can take seconds).
    // Only ping a worker that is already warm, unless probe=true (preview/debug).
    let warm = worker.isWarm();
    let workerDevice = null;
    let cudaFreeMb = null;
    if (bin && (warm || probe)) {
      try {
        if (probe && !warm) await worker.ensureStarted();
        warm = worker.isWarm();
        if (warm || probe) {
          const status = await worker.request({ cmd: 'status' }, { timeoutMs: 15000 });
          workerDevice = status?.device;
          cudaFreeMb = status?.cudaFreeMb;
          warm = true;
        }
      } catch {
        warm = worker.isWarm();
      }
    }
    return {
      ok: Boolean(bin),
      engine: 'kokoro',
      pythonPath: bin,
      defaultVoice: DEFAULT_VOICE_ID,
      voice: normalizeVoiceId(config?.voice?.ttsVoice),
      voices: listVoices(),
      ready: Boolean(bin),
      warm,
      device: workerDevice || TTS_INFERENCE_DEVICE,
      cudaFreeMb,
    };
  }

  async function speakStream(text, options = {}, onChunk) {
    const cleaned = sanitizeForTts(text);
    if (!cleaned) {
      return { ok: true, skipped: true, reason: 'empty' };
    }
    const voiceId = normalizeVoiceId(options.voice || config?.voice?.ttsVoice);
    const voice = getVoice(voiceId);
    const generation = ++speakGeneration;
    const streamId =
      typeof options.streamId === 'string' && options.streamId.trim()
        ? options.streamId.trim()
        : `tts-${Date.now()}-${crypto.randomBytes(4).toString('hex')}`;
    const pieces = splitForTtsPlayback(cleaned);
    const payload = {
      cmd: 'speak_stream',
      streamId,
      text: cleaned,
      pieces,
      voice: voiceId,
      speed: options.speed != null ? options.speed : config?.voice?.ttsSpeed || 1,
      device: TTS_INFERENCE_DEVICE,
    };

    const result = await worker.requestStream(payload, {
      timeoutMs: 180000,
      onChunk: (chunk) => {
        if (generation !== speakGeneration) return;
        onChunk?.({ streamId, ...chunk });
      },
    });
    if (generation !== speakGeneration) {
      return { ok: true, cancelled: true };
    }
    return {
      ...result,
      streamId,
      voice: voiceId,
      voiceMeta: voice,
      chunked: true,
    };
  }

  async function speak(text, options = {}) {
    const cleaned = sanitizeForTts(text);
    if (!cleaned) {
      return { ok: true, skipped: true, reason: 'empty' };
    }
    const voiceId = normalizeVoiceId(options.voice || config?.voice?.ttsVoice);
    const voice = getVoice(voiceId);
    const outPath = path.join(
      CACHE_DIR,
      `tts-${Date.now()}-${crypto.randomBytes(4).toString('hex')}.wav`
    );

    const generation = ++speakGeneration;
    const payload = {
      cmd: 'speak',
      text: cleaned,
      voice: voiceId,
      outPath,
      speed: options.speed != null ? options.speed : config?.voice?.ttsSpeed || 1,
      device: TTS_INFERENCE_DEVICE,
    };

    const result = await worker.request(payload, { timeoutMs: 180000 });
    if (generation !== speakGeneration) {
      return { ok: true, cancelled: true };
    }
    if (!fs.existsSync(outPath)) {
      throw new Error('TTS did not write audio file');
    }
    const audioBase64 = fs.readFileSync(outPath).toString('base64');
    try {
      fs.unlinkSync(outPath);
    } catch {
      /* ignore */
    }
    return {
      ...result,
      voice: voiceId,
      voiceMeta: voice,
      mimeType: 'audio/wav',
      audioBase64,
    };
  }

  function stop() {
    speakGeneration += 1;
    // Keep the warm process; interrupt the in-flight utterance so Note/Chat can replace it.
    worker.cancelActiveStream();
    return { ok: true };
  }

  function shutdown() {
    speakGeneration += 1;
    worker.stop();
    return { ok: true };
  }

  return {
    getStatus,
    listVoices,
    speak,
    speakStream,
    stop,
    shutdown,
    sanitizeForTts,
    warm: () => worker.ensureStarted(),
  };
}

module.exports = {
  createTtsService,
  sanitizeForTts,
};
