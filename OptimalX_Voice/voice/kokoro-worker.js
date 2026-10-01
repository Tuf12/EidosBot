'use strict';

const { spawn } = require('child_process');
const path = require('path');
const { resolvePythonBin, pythonScriptsDir, voiceProcessEnv, TTS_INFERENCE_DEVICE } = require('./paths');

function isCurrentStreamEvent(active, parsed) {
  if (!active) return false;
  const expected = active.streamId;
  if (!expected) return true;
  const got = parsed && parsed.streamId;
  if (!got) return false;
  return String(got) === String(expected);
}

/**
 * Keep one Kokoro Python process warm so read-aloud is not a cold model load every line.
 */
function createKokoroWorker(config) {
  let child = null;
  let buffer = '';
  /** @type {Array<{resolve:Function, reject:Function, stream?:boolean, onChunk?:Function}>} */
  let queue = [];
  let starting = null;
  let ready = false;
  let startResolve = null;
  let startReject = null;
  /** @type {{ onChunk: Function, resolve: Function, reject: Function } | null} */
  let activeStream = null;

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
    activeStream = null;
    for (const item of pending) item.reject(err);
  }

  function finishActiveStream(parsed) {
    if (!isCurrentStreamEvent(activeStream, parsed)) return false;
    const waiter = activeStream;
    activeStream = null;
    queue = queue.filter((item) => item !== waiter);
    if (parsed.ok === false) {
      waiter.reject(new Error(parsed.error || 'TTS stream failed'));
    } else {
      waiter.resolve(parsed);
    }
    return true;
  }

  function resolveActiveStreamCancelled() {
    if (!activeStream) return;
    const waiter = activeStream;
    activeStream = null;
    queue = queue.filter((item) => item !== waiter);
    waiter.resolve({ ok: true, cancelled: true, streamId: waiter.streamId || null });
  }

  function writeWorker(payload) {
    if (!child || child.killed) {
      throw new Error('Kokoro worker is not running');
    }
    child.stdin.write(`${JSON.stringify(payload)}\n`);
  }

  function cancelActiveStream() {
    if (!activeStream) return;
    const streamId = activeStream.streamId || null;
    resolveActiveStreamCancelled();
    try {
      writeWorker({ cmd: 'cancel', streamId });
    } catch {
      /* worker may already be gone */
    }
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
        const err = new Error(parsed.error || 'Kokoro failed to load');
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

    if (parsed.event === 'chunk') {
      if (isCurrentStreamEvent(activeStream, parsed)) {
        try {
          activeStream.onChunk(parsed);
        } catch {
          /* ignore chunk handler errors */
        }
      }
      return;
    }

    if (parsed.event === 'speak_done') {
      finishActiveStream(parsed);
      return;
    }

    const waiter = queue.shift();
    if (!waiter) return;
    if (parsed.ok === false) waiter.reject(new Error(parsed.error || 'TTS worker error'));
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
      const script = path.join(pythonScriptsDir(), 'speak_worker.py');
      const args = [script, '--device', TTS_INFERENCE_DEVICE];
      const proc = spawn(pythonBin, args, {
        cwd: pythonScriptsDir(),
        env: { ...voiceProcessEnv(config), OPTIMALX_VOICE_DEVICE: TTS_INFERENCE_DEVICE },
        stdio: ['pipe', 'pipe', 'pipe'],
      });
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
      proc.on('close', (code, signal) => {
        const wasReady = ready;
        child = null;
        ready = false;
        starting = null;
        const detail = signal ? `signal ${signal}` : `code ${code}`;
        const err = new Error(
          `Kokoro worker exited (${detail})${stderrTail ? `: ${stderrTail.slice(-400)}` : ''}`
        );
        rejectAll(err);
        if (!wasReady && startReject) startReject(err);
        startResolve = null;
        startReject = null;
      });
      setTimeout(() => {
        if (ready) return;
        const err = new Error(
          `Kokoro worker failed to load within 120s${stderrTail ? `: ${stderrTail.slice(-400)}` : ''}`
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
      }, 120000);
    });
    return starting;
  }

  async function requestStream(payload, { onChunk, timeoutMs = 180000 } = {}) {
    await ensureStarted();
    if (activeStream) {
      cancelActiveStream();
    }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (activeStream && activeStream.resolve === resolve) {
          activeStream = null;
        }
        queue = queue.filter((item) => item.resolve !== resolve);
        reject(new Error(`Kokoro stream timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      const waiter = {
        stream: true,
        streamId: payload?.streamId || null,
        onChunk: onChunk || (() => {}),
        resolve: (value) => {
          clearTimeout(timer);
          resolve(value);
        },
        reject: (err) => {
          clearTimeout(timer);
          reject(err);
        },
      };
      activeStream = waiter;
      queue.push(waiter);
      try {
        writeWorker(payload);
      } catch (err) {
        clearTimeout(timer);
        if (activeStream === waiter) activeStream = null;
        queue = queue.filter((item) => item !== waiter);
        reject(err);
      }
    });
  }

  async function request(payload, { timeoutMs = 180000 } = {}) {
    await ensureStarted();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error(`Kokoro worker timed out after ${timeoutMs}ms`));
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
    activeStream = null;
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
    rejectAll(new Error('Kokoro worker stopped'));
  }

  function isWarm() {
    return Boolean(child && !child.killed && ready);
  }

  return {
    request,
    requestStream,
    cancelActiveStream,
    stop,
    ensureStarted,
    isWarm,
  };
}

module.exports = { createKokoroWorker, isCurrentStreamEvent };
