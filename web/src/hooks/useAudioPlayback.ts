import { useState, useRef, useCallback, useEffect } from 'react';

interface UseAudioPlaybackOptions {
  token: string;
  defaultVoice?: string;
}

interface LiveMeta {
  ok?: boolean;
  title?: string;
  cues?: number[];
  duration?: number;
  speechStart?: number;
  complete?: boolean;
  error?: string | null;
}

const SEEK_SEC = 5;

function cleanSpeechText(text: string): string {
  return text
    .replace(/<(?:thought|thinking)>[\s\S]*?<\/(?:thought|thinking)>/gi, '')
    .replace(/<recall>[\s\S]*?<\/recall>/gi, '')
    .replace(/<(?:thought|thinking)>[\s\S]*$/gi, '')
    .replace(/<recall>[\s\S]*$/gi, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/```[\s\S]*?```/g, ' Code snippet omitted. ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/[#*_~>]/g, '')
    .replace(/[\u{1F300}-\u{1F9FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}]/gu, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function clipTitle(text: string): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  if (!flat) return 'EidosBot';
  if (flat.length <= 80) return flat;
  return `${flat.slice(0, 77)}…`;
}

function authHeaders(token: string): HeadersInit {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function liveUrl(id: string, token: string, suffix = ''): string {
  const tokenQuery = token ? `?token=${encodeURIComponent(token)}` : '';
  return `/api/tts/live/${id}${suffix}${tokenQuery}`;
}

function fileUrl(id: string, token: string, version: number): string {
  const params = new URLSearchParams();
  if (token) params.set('token', token);
  params.set('v', String(Math.max(0, Math.round(version * 1000))));
  return `/api/tts/live/${id}/file?${params.toString()}`;
}

function bufferedEnd(audio: HTMLAudioElement): number {
  try {
    if (audio.seekable.length > 0) return audio.seekable.end(audio.seekable.length - 1);
  } catch {
    /* ignore */
  }
  try {
    if (audio.buffered.length > 0) return audio.buffered.end(audio.buffered.length - 1);
  } catch {
    /* ignore */
  }
  if (Number.isFinite(audio.duration) && audio.duration > 0) return audio.duration;
  return audio.currentTime || 0;
}

function askPlaybackPermission() {
  try {
    if (!('Notification' in window) || Notification.permission !== 'default') return;
    void Notification.requestPermission().catch(() => {});
  } catch {
    /* ignore */
  }
}

function setPlaybackSessionType() {
  const nav = navigator as Navigator & { audioSession?: { type: string } };
  try {
    if (nav.audioSession) nav.audioSession.type = 'playback';
  } catch {
    /* ignore */
  }
}

/**
 * One audio element for the current read-aloud. Speech is recorded on the
 * server and kept until the next read-aloud replaces it. The first play uses
 * the live WAV so audio can start early; pause, seek, replay, and any break
 * in that stream use the saved recording.
 */
export function useAudioPlayback({ token, defaultVoice = 'bm_george' }: UseAudioPlaybackOptions) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackActive, setPlaybackActive] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [playingMessageId, setPlayingMessageId] = useState<string | null>(null);
  const [isBuffering, setIsBuffering] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [autoSpeak, setAutoSpeak] = useState<boolean>(() => localStorage.getItem('eidos_auto_speak') === 'true');
  const [playbackError, setPlaybackError] = useState<string | null>(null);

  const tokenRef = useRef(token);
  const voiceRef = useRef(defaultVoice);
  tokenRef.current = token;
  voiceRef.current = defaultVoice;

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const sessionRef = useRef<string | null>(null);
  const textSentRef = useRef(false);
  const pendingArmRef = useRef(false);
  const userSeekedRef = useRef(false);
  const skippedPrimerRef = useRef(false);
  const suppressRef = useRef(false);
  const cuesRef = useRef<number[]>([]);
  const speechStartRef = useRef(0);
  const durationRef = useRef(0);
  const positionRef = useRef(0);
  const completeRef = useRef(false);
  const finishedRef = useRef(false);
  const usingFileRef = useRef(false);
  const reopenAutoplayRef = useRef(false);
  const reopenListenerRef = useRef<(() => void) | null>(null);
  const titleRef = useRef('EidosBot');
  const messageIdRef = useRef<string | null>(null);
  const pollRef = useRef<number | null>(null);
  const activeRef = useRef(false);
  const stopAudioRef = useRef<() => void>(() => {});
  const followRef = useRef(false);
  const recoverLockRef = useRef(false);
  const recoverWhenReadyRef = useRef<boolean | null>(null);
  const reopenAtRef = useRef<(audio: HTMLAudioElement, absoluteTime: number, autoplay: boolean) => void>(() => {});
  const settleFinishedRef = useRef<() => boolean>(() => false);
  const pauseAudioRef = useRef<() => void>(() => {});
  const resumeAudioRef = useRef<() => void>(() => {});

  const apiRef = useRef({
    setIsPlaying,
    setPlaybackActive,
    setIsPaused,
    setPlayingMessageId,
    setIsBuffering,
    setPosition,
    setDuration,
    setPlaybackError,
  });
  apiRef.current = {
    setIsPlaying,
    setPlaybackActive,
    setIsPaused,
    setPlayingMessageId,
    setIsBuffering,
    setPosition,
    setDuration,
    setPlaybackError,
  };

  const ensureAudio = useCallback(() => {
    if (audioRef.current) return audioRef.current;
    const audio = document.createElement('audio');
    audio.setAttribute('playsinline', 'true');
    audio.setAttribute('webkit-playsinline', 'true');
    audio.preload = 'auto';
    audio.style.cssText = 'position:fixed;width:0;height:0;opacity:0;pointer-events:none;';
    audio.onplay = () => {
      if (suppressRef.current || finishedRef.current) return;
      if (textSentRef.current) {
        followRef.current = true;
        apiRef.current.setIsPlaying(true);
        apiRef.current.setIsPaused(false);
        apiRef.current.setPlaybackActive(true);
        publishMedia('playing');
      }
    };
    audio.onpause = () => {
      if (suppressRef.current || finishedRef.current) return;
      if (finishIfAtEnd()) return;
      if (textSentRef.current) {
        apiRef.current.setIsPlaying(false);
        if (activeRef.current) apiRef.current.setIsPaused(true);
        publishMedia('paused');
      }
    };
    audio.onwaiting = () => {
      if (finishedRef.current || suppressRef.current) return;
      if (activeRef.current && textSentRef.current) apiRef.current.setIsBuffering(true);
    };
    audio.onplaying = () => {
      if (suppressRef.current || finishedRef.current) return;
      if (textSentRef.current) {
        apiRef.current.setIsBuffering(false);
        apiRef.current.setIsPlaying(true);
        apiRef.current.setIsPaused(false);
      }
    };
    audio.ontimeupdate = () => {
      syncPosition();
      maybeSkipPrimer();
    };
    audio.onended = () => {
      if (suppressRef.current) return;
      if (activeRef.current && textSentRef.current) {
        const played = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 0;
        if (
          usingFileRef.current &&
          followRef.current &&
          !finishedRef.current &&
          played > 0 &&
          durationRef.current > played + 0.3
        ) {
          reopenAtRef.current(audio, Math.min(audio.currentTime || played, durationRef.current), true);
          return;
        }
        if (usingFileRef.current && followRef.current && !finishedRef.current && !completeRef.current) {
          return;
        }
        settleFinishedRef.current();
        if (!usingFileRef.current && sessionRef.current && (completeRef.current || durationRef.current > 0)) {
          reopenAtRef.current(audio, durationRef.current || speechStartRef.current, false);
        }
        return;
      }
      apiRef.current.setIsPlaying(false);
      apiRef.current.setPlaybackActive(false);
      apiRef.current.setIsPaused(false);
      apiRef.current.setIsBuffering(false);
      publishMedia('none');
    };
    audio.onerror = () => {
      if (!activeRef.current || !textSentRef.current) return;
      suppressRef.current = false;
      const start = speechStartRef.current;
      const recorded = durationRef.current;
      const waitingForAudio = recorded <= start + 0.05 && !completeRef.current;
      if (recoverLockRef.current || waitingForAudio) {
        recoverWhenReadyRef.current = followRef.current || !finishedRef.current;
        apiRef.current.setIsBuffering(true);
        apiRef.current.setPlaybackError(null);
        return;
      }
      recoverLockRef.current = true;
      window.setTimeout(() => {
        recoverLockRef.current = false;
      }, 700);
      const speechPos = positionRef.current;
      const speechDur = Math.max(0, recorded - start);
      const atEnd = finishedRef.current || (speechDur > 0.2 && speechPos >= speechDur - 0.4);
      const autoplay = !atEnd && followRef.current;
      followRef.current = autoplay;
      apiRef.current.setPlaybackError(null);
      apiRef.current.setIsBuffering(autoplay);
      if (atEnd) settleFinishedRef.current();
      reopenAtRef.current(audio, atEnd ? recorded : start + speechPos, autoplay);
    };
    document.body.appendChild(audio);
    audioRef.current = audio;
    return audio;
  }, []);

  const publishMedia = (state: 'playing' | 'paused' | 'none') => {
    if (!('mediaSession' in navigator)) return;
    try {
      if (state === 'none' || !textSentRef.current) {
        navigator.mediaSession.playbackState = 'none';
        return;
      }
      const origin = window.location.origin;
      navigator.mediaSession.metadata = new MediaMetadata({
        title: titleRef.current || 'EidosBot',
        artist: 'EidosBot',
        album: 'Voice',
        artwork: [
          { src: `${origin}/icons/icon-192.png`, sizes: '192x192', type: 'image/png' },
          { src: `${origin}/icons/icon-512.png`, sizes: '512x512', type: 'image/png' },
        ],
      });
      navigator.mediaSession.playbackState = state;
      const audio = audioRef.current;
      const start = speechStartRef.current;
      const totalDuration = durationRef.current || (audio && Number.isFinite(audio.duration) ? audio.duration : 0);
      const pos = Math.max(0, (audio ? audio.currentTime || 0 : 0) - start);
      // The live WAV has no declared length, so Android's player treats it as a
      // live broadcast and hides pause/play. A finite position state puts those
      // controls back on the lock screen and notification player.
      const known = Math.max(0, totalDuration - start);
      const dur = Math.max(known, pos + 1, 1);
      if ('setPositionState' in navigator.mediaSession) {
        navigator.mediaSession.setPositionState({
          duration: dur,
          playbackRate: audio?.playbackRate || 1,
          position: Math.min(pos, dur - 0.05),
        });
      }
    } catch {
      /* ignore */
    }
  };

  const syncPosition = () => {
    const audio = audioRef.current;
    if (!audio || !textSentRef.current) return;
    const start = speechStartRef.current;
    const pos = Math.max(0, (audio.currentTime || 0) - start);
    const totalDuration = durationRef.current || (Number.isFinite(audio.duration) ? audio.duration : 0);
    const dur = Math.max(0, totalDuration - start);
    positionRef.current = pos;
    apiRef.current.setPosition(pos);
    if (dur > 0) apiRef.current.setDuration(dur);
    if (activeRef.current && !audio.paused && !finishedRef.current) publishMedia('playing');
    finishIfAtEnd();
  };

  // Leave the player on screen after a read-aloud ends. Stop stays inactive
  // until speech is playing or paused, and play / the 5-second buttons can
  // still move through the same clip.
  const settleFinished = () => {
    if (finishedRef.current) return true;
    if (!activeRef.current || !textSentRef.current) return false;
    finishedRef.current = true;
    const audio = audioRef.current;
    if (audio?.ended) completeRef.current = true;
    try {
      if (audio && !audio.paused && !audio.ended) audio.pause();
    } catch {
      /* ignore */
    }
    apiRef.current.setIsPlaying(false);
    apiRef.current.setIsPaused(false);
    apiRef.current.setIsBuffering(false);
    apiRef.current.setPlaybackActive(true);
    const start = speechStartRef.current;
    const total = durationRef.current || (audio && Number.isFinite(audio.duration) ? audio.duration : 0);
    const dur = Math.max(0, total - start);
    if (dur > 0) {
      positionRef.current = dur;
      apiRef.current.setDuration(dur);
      apiRef.current.setPosition(dur);
    }
    if (completeRef.current) stopPolling();
    publishMedia('paused');
    return true;
  };
  settleFinishedRef.current = settleFinished;

  const finishIfAtEnd = () => {
    if (finishedRef.current) return true;
    const audio = audioRef.current;
    if (!audio || suppressRef.current || !activeRef.current || !textSentRef.current || !completeRef.current) {
      return false;
    }
    const total = durationRef.current;
    if (!(total > 0) || !Number.isFinite(audio.currentTime)) return false;
    const slack = audio.paused || audio.ended ? 0.5 : 0.05;
    if (!(audio.ended || audio.currentTime >= total - slack)) return false;
    return settleFinished();
  };

  const maybeSkipPrimer = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (!textSentRef.current) {
      audio.volume = 0;
      return;
    }
    const start = speechStartRef.current;
    if (userSeekedRef.current || skippedPrimerRef.current) {
      audio.volume = 1;
      return;
    }
    if (start < 0.15) {
      skippedPrimerRef.current = true;
      audio.volume = 1;
      return;
    }
    if (audio.currentTime >= start - 0.05) {
      skippedPrimerRef.current = true;
      audio.volume = 1;
      return;
    }
    if (bufferedEnd(audio) > start + 0.05) {
      try {
        audio.currentTime = start;
        skippedPrimerRef.current = true;
        audio.volume = 1;
      } catch {
        /* The stream may not be seekable yet. Speech still follows the lead-in. */
      }
    }
  };

  const stopPolling = () => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const applyMeta = (meta: LiveMeta) => {
    if (Array.isArray(meta.cues)) cuesRef.current = meta.cues;
    if (typeof meta.speechStart === 'number') speechStartRef.current = meta.speechStart;
    if (typeof meta.duration === 'number' && meta.duration > 0) {
      durationRef.current = meta.duration;
      const start = speechStartRef.current;
      apiRef.current.setDuration(Math.max(0, meta.duration - start));
    }
    if (meta.title && textSentRef.current) titleRef.current = meta.title;
    completeRef.current = Boolean(meta.complete);
    if (meta.error && textSentRef.current) apiRef.current.setPlaybackError(meta.error);
    if (meta.complete && (meta.cues?.length || 0) > 0 && textSentRef.current) {
      apiRef.current.setIsBuffering(false);
    }
    const audio = audioRef.current;
    const start = speechStartRef.current;
    if (
      recoverWhenReadyRef.current !== null &&
      textSentRef.current &&
      typeof meta.duration === 'number' &&
      meta.duration > start + 0.05
    ) {
      const autoplay = recoverWhenReadyRef.current;
      recoverWhenReadyRef.current = null;
      followRef.current = autoplay;
      if (audio && sessionRef.current) {
        reopenAtRef.current(audio, start, autoplay);
      }
    } else if (
      audio &&
      usingFileRef.current &&
      followRef.current &&
      !finishedRef.current &&
      audio.ended &&
      Number.isFinite(audio.duration) &&
      audio.duration > 0 &&
      typeof meta.duration === 'number' &&
      meta.duration > audio.duration + 0.3
    ) {
      reopenAtRef.current(audio, Math.min(audio.currentTime || 0, meta.duration), true);
    }
    maybeSkipPrimer();
    syncPosition();
    if (finishedRef.current && completeRef.current) stopPolling();
  };

  const startPolling = (id: string) => {
    stopPolling();
    const tick = async () => {
      if (sessionRef.current !== id) return;
      try {
        const res = await fetch(liveUrl(id, tokenRef.current, '/meta'), {
          headers: authHeaders(tokenRef.current),
        });
        if (!res.ok || sessionRef.current !== id) return;
        applyMeta((await res.json()) as LiveMeta);
      } catch {
        /* The audio element keeps playing even if this poll misses. */
      }
    };
    void tick();
    pollRef.current = window.setInterval(() => void tick(), 700);
  };

  const cancelSession = (id: string | null) => {
    if (!id) return;
    const auth = tokenRef.current;
    void fetch(liveUrl(id, auth), { method: 'DELETE', headers: authHeaders(auth) }).catch(() => {});
  };

  const stopAudio = useCallback(() => {
    const recorded = durationRef.current > speechStartRef.current + 0.05 || completeRef.current;
    if (textSentRef.current && sessionRef.current && recorded) {
      followRef.current = false;
      recoverWhenReadyRef.current = null;
      try {
        audioRef.current?.pause();
      } catch {
        /* ignore */
      }
      settleFinishedRef.current();
      const audio = audioRef.current;
      if (audio) reopenAtRef.current(audio, durationRef.current || speechStartRef.current, false);
      return;
    }
    suppressRef.current = true;
    const audio = audioRef.current;
    activeRef.current = false;
    textSentRef.current = false;
    pendingArmRef.current = false;
    stopPolling();
    cancelSession(sessionRef.current);
    sessionRef.current = null;
    messageIdRef.current = null;
    cuesRef.current = [];
    speechStartRef.current = 0;
    durationRef.current = 0;
    positionRef.current = 0;
    completeRef.current = false;
    finishedRef.current = false;
    usingFileRef.current = false;
    reopenAutoplayRef.current = false;
    if (audio && reopenListenerRef.current) {
      audio.removeEventListener('loadedmetadata', reopenListenerRef.current);
      reopenListenerRef.current = null;
    }
    try {
      audio?.pause();
      if (audio) audio.src = '';
    } catch {
      /* ignore */
    }
    publishMedia('none');
    apiRef.current.setIsPlaying(false);
    apiRef.current.setPlaybackActive(false);
    apiRef.current.setIsPaused(false);
    apiRef.current.setIsBuffering(false);
    apiRef.current.setPlayingMessageId(null);
    apiRef.current.setPosition(0);
    apiRef.current.setDuration(0);
    window.setTimeout(() => {
      suppressRef.current = false;
    }, 0);
  }, []);
  stopAudioRef.current = stopAudio;

  const openStream = useCallback(
    (text: string | null, messageId?: string) => {
      setPlaybackSessionType();
      const audio = ensureAudio();
      const previous = sessionRef.current;
      const id = crypto.randomUUID();
      sessionRef.current = id;
      if (previous && previous !== id) cancelSession(previous);
      textSentRef.current = Boolean(text);
      userSeekedRef.current = false;
      skippedPrimerRef.current = false;
      followRef.current = Boolean(text);
      recoverWhenReadyRef.current = null;
      recoverLockRef.current = false;
      cuesRef.current = [];
      speechStartRef.current = 0;
      durationRef.current = 0;
      positionRef.current = 0;
      completeRef.current = false;
      finishedRef.current = false;
      usingFileRef.current = false;
      reopenAutoplayRef.current = false;
      if (reopenListenerRef.current) {
        audio.removeEventListener('loadedmetadata', reopenListenerRef.current);
        reopenListenerRef.current = null;
      }
      titleRef.current = text ? clipTitle(text) : 'Preparing a reply';
      messageIdRef.current = messageId ?? null;
      activeRef.current = true;
      pendingArmRef.current = false;

      // Only show playback bar and buffering if we actually have text to speak!
      apiRef.current.setPlaybackActive(Boolean(text));
      apiRef.current.setIsPaused(false);
      apiRef.current.setIsBuffering(Boolean(text));
      apiRef.current.setPlaybackError(null);
      apiRef.current.setPosition(0);
      apiRef.current.setDuration(0);
      apiRef.current.setPlayingMessageId(messageId ?? null);
      if (messageId) {
        try {
          localStorage.setItem('eidos_last_spoken_msg_id', messageId);
        } catch {
          /* ignore */
        }
      }

      suppressRef.current = false;
      // Keep volume at 0 until actual speech text is ready
      audio.volume = text ? 1 : 0;
      audio.src = liveUrl(id, tokenRef.current);
      // play() has to happen in this turn, while the tap is still active.
      const started = audio.play();
      askPlaybackPermission();
      void started.catch((err: unknown) => {
        if (!activeRef.current || sessionRef.current !== id) return;
        if (text) {
          const message = err instanceof Error ? err.message : 'Playback was blocked';
          apiRef.current.setPlaybackError(
            `${message}. Leave this screen open until you hear audio, then you can switch away.`
          );
          apiRef.current.setIsBuffering(false);
        }
      });
      if (text) {
        publishMedia('playing');
      }
      startPolling(id);

      if (text) {
        void fetch(liveUrl(id, tokenRef.current), {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...authHeaders(tokenRef.current),
          },
          body: JSON.stringify({ text, voice: voiceRef.current }),
        })
          .then(async (res) => {
            if (res.ok || sessionRef.current !== id) return;
            const data = (await res.json().catch(() => ({}))) as { error?: string };
            apiRef.current.setPlaybackError(data.error || 'Could not start speech');
          })
          .catch(() => {
            if (sessionRef.current === id) apiRef.current.setPlaybackError('Could not reach the voice server');
          });
      }
    },
    [ensureAudio]
  );

  const postText = useCallback((id: string, text: string, messageId?: string) => {
    textSentRef.current = true;
    titleRef.current = clipTitle(text);
    messageIdRef.current = messageId ?? null;
    if (messageId) {
      apiRef.current.setPlayingMessageId(messageId);
      try {
        localStorage.setItem('eidos_last_spoken_msg_id', messageId);
      } catch {
        /* ignore */
      }
    }
    // Message is ready to vocalize! Make player visible
    apiRef.current.setPlaybackActive(true);
    apiRef.current.setIsPlaying(true);
    apiRef.current.setIsPaused(false);
    apiRef.current.setIsBuffering(true);
    apiRef.current.setPlaybackError(null);
    publishMedia('playing');
    void fetch(liveUrl(id, tokenRef.current), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders(tokenRef.current),
      },
      body: JSON.stringify({ text, voice: voiceRef.current }),
    })
      .then(async (res) => {
        if (res.ok || sessionRef.current !== id) return;
        const data = (await res.json().catch(() => ({}))) as { error?: string };
        apiRef.current.setPlaybackError(data.error || 'Could not start speech');
      })
      .catch(() => {
        if (sessionRef.current === id) apiRef.current.setPlaybackError('Could not reach the voice server');
      });
  }, []);

  const armBackgroundPlayback = useCallback(() => {
    const audio = audioRef.current;
    if (activeRef.current && textSentRef.current && audio && !audio.paused && !audio.ended) {
      pendingArmRef.current = true;
      return;
    }
    if (activeRef.current && sessionRef.current && !textSentRef.current && audio && !audio.paused) {
      return;
    }
    openStream(null);
  }, [openStream]);

  const releaseBackgroundArm = useCallback(() => {
    pendingArmRef.current = false;
    if (activeRef.current && !textSentRef.current) stopAudio();
  }, [stopAudio]);

  const playText = useCallback(
    (text: string, messageId?: string, voice?: string) => {
      const clean = cleanSpeechText(text);
      if (!clean) {
        if (activeRef.current && !textSentRef.current) stopAudio();
        return;
      }
      if (voice) voiceRef.current = voice;
      const current = audioRef.current;
      if (messageId && messageId === messageIdRef.current && activeRef.current && textSentRef.current && current) {
        const saved = completeRef.current || durationRef.current > speechStartRef.current + 0.05;
        if (saved) {
          if (!finishedRef.current && !current.paused && !current.ended && !current.error) {
            pauseAudioRef.current();
            return;
          }
          resumeAudioRef.current();
          return;
        }
      }
      if (
        messageId &&
        messageId === messageIdRef.current &&
        activeRef.current &&
        textSentRef.current &&
        current &&
        !current.paused &&
        !current.ended
      ) {
        stopAudio();
        return;
      }
      if (sessionRef.current && activeRef.current && !textSentRef.current) {
        postText(sessionRef.current, clean, messageId);
        return;
      }
      openStream(clean, messageId);
    },
    [openStream, postText, stopAudio]
  );

  const reopenAt = useCallback((audio: HTMLAudioElement, absoluteTime: number, autoplay: boolean) => {
    const id = sessionRef.current;
    if (!id) return;
    const target = Math.max(0, absoluteTime);
    reopenAutoplayRef.current = autoplay;
    if (autoplay) {
      apiRef.current.setIsPlaying(false);
      apiRef.current.setIsPaused(false);
      apiRef.current.setIsBuffering(true);
    }
    if (reopenListenerRef.current) {
      audio.removeEventListener('loadedmetadata', reopenListenerRef.current);
      reopenListenerRef.current = null;
    }
    const apply = () => {
      reopenListenerRef.current = null;
      const shouldPlay = reopenAutoplayRef.current;
      try {
        if (Math.abs((audio.currentTime || 0) - target) > 0.05) audio.currentTime = target;
      } catch {
        apiRef.current.setPlaybackError('This part is not ready to skip to yet.');
      }
      suppressRef.current = false;
      syncPosition();
      if (finishedRef.current) {
        apiRef.current.setIsBuffering(false);
        return;
      }
      if (!shouldPlay) {
        apiRef.current.setIsPlaying(false);
        apiRef.current.setIsPaused(true);
        apiRef.current.setIsBuffering(false);
        try {
          audio.pause();
        } catch {
          /* ignore */
        }
        publishMedia('paused');
        return;
      }
      apiRef.current.setIsPaused(false);
      void audio.play().catch((err: unknown) => {
        apiRef.current.setIsBuffering(false);
        const message = err instanceof Error ? err.message : 'Playback was blocked';
        apiRef.current.setPlaybackError(message);
      });
    };
    if (usingFileRef.current && audio.currentSrc.includes('/file') && audio.readyState >= 1 && Math.abs((durationRef.current || 0) - (Number.isFinite(audio.duration) ? audio.duration : 0)) < 0.3) {
      apply();
      return;
    }
    suppressRef.current = true;
    usingFileRef.current = true;
    reopenListenerRef.current = apply;
    audio.addEventListener('loadedmetadata', apply, { once: true });
    audio.src = fileUrl(id, tokenRef.current, durationRef.current || target);
    audio.load();
  }, []);
  reopenAtRef.current = reopenAt;

  const seekTo = useCallback((target: number) => {
    const audio = audioRef.current;
    if (!audio || !activeRef.current || !textSentRef.current) return;
    userSeekedRef.current = true;
    audio.volume = 1;
    const start = speechStartRef.current;
    const totalDuration = durationRef.current || (Number.isFinite(audio.duration) ? audio.duration : 0);
    const dur = Math.max(0, totalDuration - start);
    const end = Math.max(bufferedEnd(audio) - start, dur);
    const nextSpeechPos = Math.max(0, Math.min(target, end > 0.1 ? end - 0.05 : target));
    const fromFinished = finishedRef.current;
    const restingAtEnd = fromFinished && dur > 0 && nextSpeechPos >= dur - 0.2;
    if (restingAtEnd) {
      apiRef.current.setPosition(dur);
      return;
    }
    const haveRecording = dur > 0.05 || completeRef.current;
    const needFile = !usingFileRef.current || fromFinished || audio.ended || Boolean(audio.error);
    if (haveRecording && sessionRef.current && needFile) {
      const keepPlaying = !fromFinished && !audio.paused && !audio.ended && !audio.error;
      finishedRef.current = false;
      followRef.current = keepPlaying;
      if (!keepPlaying) {
        apiRef.current.setIsPlaying(false);
        apiRef.current.setIsPaused(true);
      }
      reopenAt(audio, start + nextSpeechPos, keepPlaying);
      return;
    }
    try {
      audio.currentTime = start + nextSpeechPos;
    } catch {
      if (sessionRef.current) {
        const keepPlaying = !audio.paused && !finishedRef.current;
        followRef.current = keepPlaying;
        reopenAt(audio, start + nextSpeechPos, keepPlaying);
        return;
      }
      apiRef.current.setPlaybackError('This part is not ready to skip to yet.');
      return;
    }
    syncPosition();
  }, [reopenAt]);

  const seekBy = useCallback(
    (delta: number) => {
      const audio = audioRef.current;
      if (!audio || !textSentRef.current) return;
      const start = speechStartRef.current;
      const fromElement = Math.max(0, (audio.currentTime || 0) - start);
      const currentSpeechPos = finishedRef.current ? Math.max(fromElement, positionRef.current) : fromElement;
      seekTo(currentSpeechPos + delta);
    },
    [seekTo]
  );

  const skipChunk = useCallback(
    (direction: -1 | 1) => {
      const audio = audioRef.current;
      if (!audio || !textSentRef.current) return;
      const start = speechStartRef.current;
      const fromElement = Math.max(0, (audio.currentTime || 0) - start);
      const now = finishedRef.current ? Math.max(fromElement, positionRef.current) : fromElement;
      const relativeCues = cuesRef.current.map((cue) => Math.max(0, cue - start));
      if (!relativeCues.length) {
        seekBy(direction * SEEK_SEC);
        return;
      }
      if (direction > 0) {
        const next = relativeCues.find((cue) => cue > now + 0.35);
        seekTo(next ?? now + SEEK_SEC);
        return;
      }
      const prev = [...relativeCues].reverse().find((cue) => cue < now - 0.35);
      seekTo(prev ?? relativeCues[0] ?? 0);
    },
    [seekBy, seekTo]
  );

  const resumeAudio = useCallback(() => {
    const audio = ensureAudio();
    if (!activeRef.current) return;
    followRef.current = true;
    if (textSentRef.current) {
      audio.volume = 1;
    }
    if (reopenListenerRef.current) {
      reopenAutoplayRef.current = true;
      apiRef.current.setIsPlaying(false);
      apiRef.current.setIsPaused(false);
      apiRef.current.setIsBuffering(true);
      return;
    }
    if (finishedRef.current || audio.error) {
      const start = speechStartRef.current;
      const fromBeginning = finishedRef.current;
      finishedRef.current = false;
      userSeekedRef.current = false;
      skippedPrimerRef.current = false;
      if (sessionRef.current && (completeRef.current || durationRef.current > 0 || audio.error)) {
        const at = fromBeginning ? start : start + Math.max(0, positionRef.current);
        reopenAt(audio, at, true);
        return;
      }
      try {
        audio.currentTime = start;
      } catch {
        /* ignore */
      }
    }
    void audio.play().catch(() => {
      if (sessionRef.current && textSentRef.current) {
        const start = speechStartRef.current;
        reopenAt(audio, start + Math.max(0, positionRef.current), true);
        return;
      }
      apiRef.current.setPlaybackError('Playback was blocked');
    });
  }, [ensureAudio, reopenAt]);
  resumeAudioRef.current = resumeAudio;

  const pauseAudio = useCallback(() => {
    followRef.current = false;
    audioRef.current?.pause();
  }, []);
  pauseAudioRef.current = pauseAudio;

  const toggleAutoSpeak = useCallback(() => {
    setAutoSpeak((prev) => {
      const next = !prev;
      localStorage.setItem('eidos_auto_speak', String(next));
      if (next) {
        setPlaybackSessionType();
        askPlaybackPermission();
      } else if (activeRef.current && !textSentRef.current) {
        stopAudio();
      }
      return next;
    });
  }, [stopAudio]);

  useEffect(() => {
    const onSeekBack = (details?: MediaSessionActionDetails) => seekBy(-(details?.seekOffset || SEEK_SEC));
    const onSeekForward = (details?: MediaSessionActionDetails) => seekBy(details?.seekOffset || SEEK_SEC);
    if (!('mediaSession' in navigator)) return;
    try {
      navigator.mediaSession.setActionHandler('play', () => resumeAudio());
      navigator.mediaSession.setActionHandler('pause', () => pauseAudio());
      navigator.mediaSession.setActionHandler('stop', () => stopAudio());
      navigator.mediaSession.setActionHandler('seekbackward', onSeekBack);
      navigator.mediaSession.setActionHandler('seekforward', onSeekForward);
      navigator.mediaSession.setActionHandler('previoustrack', () => skipChunk(-1));
      navigator.mediaSession.setActionHandler('nexttrack', () => skipChunk(1));
    } catch {
      /* Some browsers reject a subset of these actions. */
    }
  }, [pauseAudio, resumeAudio, seekBy, skipChunk, stopAudio]);

  useEffect(() => {
    return () => {
      stopPolling();
      cancelSession(sessionRef.current);
      audioRef.current?.pause();
      audioRef.current?.remove();
      audioRef.current = null;
    };
  }, []);

  return {
    isPlaying,
    playbackActive,
    isPaused,
    playingMessageId,
    isBuffering,
    position,
    duration,
    autoSpeak,
    toggleAutoSpeak,
    playbackError,
    playText,
    stopAudio,
    pauseAudio,
    resumeAudio,
    seekBy,
    skipChunk,
    armBackgroundPlayback,
    releaseBackgroundArm,
  };
}
