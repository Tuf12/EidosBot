'use strict';

const path = require('path');
const fs = require('fs');
const os = require('os');
const { OPTIMALX_HOME } = require('../config');

const VOICE_HOME = path.join(OPTIMALX_HOME, 'voice');
const VENV_DIR = path.join(VOICE_HOME, 'venv');
const VENV_PYTHON = path.join(VENV_DIR, 'bin', 'python');
const CACHE_DIR = path.join(VOICE_HOME, 'cache');
const MODELS_DIR = path.join(VOICE_HOME, 'models');

function ensureVoiceDirs() {
  fs.mkdirSync(VOICE_HOME, { recursive: true });
  fs.mkdirSync(CACHE_DIR, { recursive: true });
  fs.mkdirSync(MODELS_DIR, { recursive: true });
}

/** Optional CUDA-ready venv created during Blackwell setup (outside OptimalX home). */
const FALLBACK_VOICE_PYTHON = path.join(os.homedir(), '.venvs', 'optimalx-voice', 'bin', 'python');

function resolvePythonBin(config = {}) {
  const configured =
    typeof config?.voice?.pythonPath === 'string' ? config.voice.pythonPath.trim() : '';
  if (configured && fs.existsSync(configured)) return configured;
  if (fs.existsSync(VENV_PYTHON)) return VENV_PYTHON;
  if (fs.existsSync(FALLBACK_VOICE_PYTHON)) return FALLBACK_VOICE_PYTHON;
  return null;
}

/** Live TTS stays on CPU so the chat LLM can keep the GPU. CUDA remains in Python for a future offline converter. */
const TTS_INFERENCE_DEVICE = 'cpu';

/** Env for Python voice processes — omit force when device is auto/null. */
function voiceProcessEnv(config = {}) {
  const env = { ...process.env, PYTHONUNBUFFERED: '1' };
  const fromEnv = String(process.env.OPTIMALX_VOICE_DEVICE || '').trim().toLowerCase();
  const fromConfig = config?.voice?.device;
  const device =
    fromEnv === 'cuda' || fromEnv === 'cpu'
      ? fromEnv
      : fromConfig === 'cuda' || fromConfig === 'cpu'
        ? fromConfig
        : '';
  if (device) env.OPTIMALX_VOICE_DEVICE = device;
  else delete env.OPTIMALX_VOICE_DEVICE;
  return env;
}

function pythonScriptsDir() {
  return path.join(__dirname, 'python');
}

module.exports = {
  VOICE_HOME,
  VENV_DIR,
  VENV_PYTHON,
  CACHE_DIR,
  MODELS_DIR,
  TTS_INFERENCE_DEVICE,
  ensureVoiceDirs,
  resolvePythonBin,
  voiceProcessEnv,
  pythonScriptsDir,
};
