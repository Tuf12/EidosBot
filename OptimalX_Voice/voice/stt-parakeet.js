'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const {
  ensureVoiceDirs,
  resolvePythonBin,
  CACHE_DIR,
} = require('./paths');
const { createParakeetWorker } = require('./parakeet-worker');

const DEFAULT_MODEL = 'parakeet-tdt-0.6b-v2-int8';

function createSttService(config) {
  ensureVoiceDirs();
  const worker = createParakeetWorker(config);

  function pythonOrThrow() {
    const bin = resolvePythonBin(config);
    if (!bin) {
      throw new Error(
        'Voice Python venv not found. Run electron/voice/python/setup.sh (see electron/voice/SETUP.md).'
      );
    }
    return bin;
  }

  async function getStatus({ probe = false } = {}) {
    let bin;
    try {
      bin = pythonOrThrow();
    } catch (err) {
      return {
        ok: false,
        ready: false,
        warm: false,
        engine: 'parakeet',
        error: err.message || String(err),
      };
    }
    // Do not cold-start Parakeet just for settings — first load is a few seconds.
    const warm = worker.isWarm();
    if (!warm && !probe) {
      return {
        ok: true,
        ready: true,
        warm: false,
        engine: 'parakeet',
        pythonPath: bin,
        note: 'idle (loads on first use)',
      };
    }
    try {
      const status = await worker.request({ cmd: 'status' }, { timeoutMs: probe ? 180000 : 15000 });
      return { ...status, pythonPath: bin, warm: true, ready: true, ok: true };
    } catch (err) {
      return {
        ok: false,
        ready: Boolean(bin),
        warm: worker.isWarm(),
        engine: 'parakeet',
        pythonPath: bin,
        error: err.message || String(err),
      };
    }
  }

  async function transcribeWav(wavPath, options = {}) {
    if (!wavPath || !fs.existsSync(wavPath)) {
      throw new Error('WAV path missing');
    }
    const payload = {
      cmd: 'transcribe',
      wavPath,
      device: options.device || config?.voice?.device || undefined,
    };
    return worker.request(payload, { timeoutMs: options.timeoutMs || 300000 });
  }

  async function transcribeBuffer(buffer, options = {}) {
    ensureVoiceDirs();
    const ext = options.ext || 'wav';
    const tmpPath = path.join(
      CACHE_DIR,
      `stt-${Date.now()}-${crypto.randomBytes(4).toString('hex')}.${ext}`
    );
    fs.writeFileSync(tmpPath, Buffer.from(buffer));
    try {
      return await transcribeWav(tmpPath, options);
    } finally {
      if (options.keepTemp !== true) {
        try {
          fs.unlinkSync(tmpPath);
        } catch {
          /* ignore */
        }
      }
    }
  }

  return {
    getStatus,
    transcribeWav,
    transcribeBuffer,
    warm: () => worker.ensureStarted(),
    stop: () => worker.stop(),
    DEFAULT_MODEL,
  };
}

module.exports = {
  createSttService,
  DEFAULT_MODEL,
};
