'use strict';

const { createTtsService } = require('./tts-kokoro');
const { createSttService } = require('./stt-parakeet');
const { listVoices, normalizeVoiceId, DEFAULT_VOICE_ID, getVoice } = require('./voices');
const { sanitizeForTts } = require('./sanitize');
const { resolvePythonBin, ensureVoiceDirs, VOICE_HOME } = require('./paths');

function createVoiceService(config) {
  ensureVoiceDirs();
  const tts = createTtsService(config);
  const stt = createSttService(config);

  async function getStatus(options = {}) {
    const [ttsStatus, sttStatus] = await Promise.all([
      tts.getStatus(options),
      stt.getStatus(options),
    ]);
    return {
      ok: Boolean(resolvePythonBin(config)),
      voiceHome: VOICE_HOME,
      tts: ttsStatus,
      stt: sttStatus,
      defaultVoice: DEFAULT_VOICE_ID,
      selectedVoice: normalizeVoiceId(config?.voice?.ttsVoice),
    };
  }

  return {
    getStatus,
    listVoices,
    getVoice,
    normalizeVoiceId,
    sanitizeForTts,
    speak: async (text, options = {}) => {
      const device = options?.device || config?.voice?.device;
      if (device === 'cuda') {
        try {
          stt.stop?.();
        } catch {
          /* ignore */
        }
      }
      return tts.speak(text, options);
    },
    speakStream: (text, options, onChunk) => tts.speakStream(text, options, onChunk),
    stopSpeak: () => tts.stop(),
    warmTts: () => tts.warm(),
    warmStt: () => stt.warm?.(),
    transcribeWav: (...args) => stt.transcribeWav(...args),
    transcribeBuffer: (...args) => stt.transcribeBuffer(...args),
    getSttStatus: () => stt.getStatus(),
    getTtsStatus: () => tts.getStatus(),
    stopSttWorker: () => stt.stop?.(),
  };
}

module.exports = {
  createVoiceService,
  listVoices,
  DEFAULT_VOICE_ID,
  sanitizeForTts,
};
