'use strict';

const { spawn } = require('child_process');
const path = require('path');
const { resolvePythonBin, pythonScriptsDir, voiceProcessEnv } = require('./paths');

/**
 * Keep one Parakeet Python process warm so mic commits are not cold starts.
 * Protocol: newline-delimited JSON on stdin/stdout.
 */
function createParakeetWorker(config) {
  let child = null;
  let buffer = '';
  /** @type {Array<{resolve:Function, reject:Function}>} */
  let queue = [];
  let starting = null;
  let ready = false;
  let startResolve = null;
  let startReject = null;

  function pythonOrThrow() {
    const bin = resolvePythonBin(config);
    if (!bin) {
      throw new Error(
        'Voice Python venv not found. Run electron/voice/python/setup.sh (see electron/voice/SETUP.md).'
      );
    }
    return bin;
  }

  function rejectAll(err) {
    const pending = queue;
    queue = [];
    for (const item of pending) item.reject(err);
  }

  function handleLine(line) {
    let parsed;
    try {
      parsed = JSON.parse(line);
    } catch {
      return;
    }

    if (!ready && parsed.event === 'loaded') {
      if (parsed.ok === false) {
        const err = new Error(parsed.error || 'Parakeet failed to load');
        if (startReject) startReject(err);
        startResolve = null;
        startReject = null;
        starting = null;
        return;
      }
      ready = true;
      if (startResolve) startResolve();
      startResolve = null;
      startReject = null;
      starting = null;
      return;
    }

    const waiter = queue.shift();
    if (!waiter) return;
    if (parsed.ok === false) waiter.reject(new Error(parsed.error || 'STT worker error'));
    else waiter.resolve(parsed);
  }

  function ensureStarted() {
    if (child && !child.killed && ready) return Promise.resolve();
    if (starting) return starting;
    starting = new Promise((resolve, reject) => {
      startResolve = resolve;
      startReject = reject;
      ready = false;
      const pythonBin = pythonOrThrow();
      const script = path.join(pythonScriptsDir(), 'transcribe_worker.py');
      const proc = spawn(
        pythonBin,
        [script, '--model', config?.voice?.sttModel || 'parakeet-tdt-0.6b-v2-int8'],
        {
          cwd: pythonScriptsDir(),
          env: voiceProcessEnv(config),
          stdio: ['pipe', 'pipe', 'pipe'],
        }
      );
      child = proc;
      buffer = '';
      let stderrTail = '';
      proc.stdout.setEncoding('utf8');
      proc.stderr.setEncoding('utf8');
      proc.stdout.on('data', (chunk) => {
        buffer += chunk;
        let idx;
        while ((idx = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, idx).trim();
          buffer = buffer.slice(idx + 1);
          if (line) handleLine(line);
        }
      });
      proc.stderr.on('data', (chunk) => {
        stderrTail = `${stderrTail}${chunk}`.slice(-2000);
      });
      proc.on('error', (err) => {
        child = null;
        ready = false;
        starting = null;
        rejectAll(err);
        if (startReject) startReject(err);
        startResolve = null;
        startReject = null;
      });
      proc.on('close', (code) => {
        const wasReady = ready;
        child = null;
        ready = false;
        starting = null;
        const err = new Error(
          `Parakeet worker exited (${code})${stderrTail ? `: ${stderrTail.slice(-400)}` : ''}`
        );
        rejectAll(err);
        if (!wasReady && startReject) startReject(err);
        startResolve = null;
        startReject = null;
      });
      setTimeout(() => {
        if (ready) return;
        const err = new Error(
          `Parakeet worker failed to load within 60s${stderrTail ? `: ${stderrTail.slice(-400)}` : ''}`
        );
        try {
          proc.kill('SIGKILL');
        } catch {
          /* ignore */
        }
        if (startReject) startReject(err);
        startResolve = null;
        startReject = null;
        starting = null;
      }, 60000);
    });
    return starting;
  }

  async function request(payload, { timeoutMs = 300000 } = {}) {
    await ensureStarted();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error(`Parakeet worker timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      queue.push({
        resolve: (value) => {
          clearTimeout(timer);
          resolve(value);
        },
        reject: (err) => {
          clearTimeout(timer);
          reject(err);
        },
      });
      try {
        child.stdin.write(`${JSON.stringify(payload)}\n`);
      } catch (err) {
        clearTimeout(timer);
        reject(err);
      }
    });
  }

  function stop() {
    ready = false;
    if (child && !child.killed) {
      try {
        child.stdin.write(`${JSON.stringify({ cmd: 'quit' })}\n`);
      } catch {
        /* ignore */
      }
      try {
        child.kill('SIGTERM');
      } catch {
        /* ignore */
      }
    }
    child = null;
    starting = null;
    startResolve = null;
    startReject = null;
    rejectAll(new Error('Parakeet worker stopped'));
  }

  function isWarm() {
    return Boolean(child && !child.killed && ready);
  }

  return {
    request,
    stop,
    ensureStarted,
    isWarm,
  };
}

module.exports = { createParakeetWorker };
