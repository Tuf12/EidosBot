import { useState, useRef, useCallback, useEffect } from 'react';

interface UseAudioPlaybackOptions {
  token: string;
  defaultVoice?: string;
}

/**
 * Split text into progressive playback pieces matching OptimalX chunking:
 * - Growing initial windows (80-140 chars, then 160-220 chars) so speech starts in ~1s
 * - Steady-state body window (up to ~350 chars) to stay ahead of playback without timing out
 */
export function splitTextIntoChunks(text: string): string[] {
  const normalized = text.replace(/\s+/g, ' ').trim();
  if (!normalized) return [];
  if (normalized.length <= 140) return [normalized];

  const windows: [number, number][] = [
    [80, 140],
    [160, 220],
  ];
  const bodyMax = 350;
  const minTailChars = 24;

  const sentenceBreakRegex = /[.!?]["')\]]*(?=\s|$)/g;

  function takeWindow(str: string, minChars: number, maxChars: number): string {
    if (str.length <= maxChars) return str;
    const windowText = str.slice(0, maxChars);

    // 1. Try finding a full sentence break within [minChars, maxChars]
    sentenceBreakRegex.lastIndex = 0;
    let match: RegExpExecArray | null;
    let bestBreak = -1;
    while ((match = sentenceBreakRegex.exec(windowText)) !== null) {
      const idx = match.index + match[0].length;
      if (idx >= minChars && idx <= maxChars) {
        bestBreak = idx;
      }
    }
    if (bestBreak > 0) return str.slice(0, bestBreak).trim();

    // 2. Try clause punctuation: semicolon, colon, dash, comma
    const clauseRegex = /[,;:—\-]["')\]]*(?=\s|$)/g;
    let bestClause = -1;
    while ((match = clauseRegex.exec(windowText)) !== null) {
      const idx = match.index + match[0].length;
      if (idx >= minChars && idx <= maxChars) {
        bestClause = idx;
      }
    }
    if (bestClause > 0) return str.slice(0, bestClause).trim();

    // 3. Fall back to last space
    const lastSpace = windowText.lastIndexOf(' ');
    if (lastSpace >= minChars) return str.slice(0, lastSpace).trim();
    if (lastSpace > 0) return str.slice(0, lastSpace).trim();

    return windowText.trim();
  }

  const pieces: string[] = [];
  let remaining = normalized;
  let stage = 0;

  while (remaining.length > 0) {
    const [minChars, maxChars] = stage < windows.length ? windows[stage] : [1, bodyMax];
    const piece = takeWindow(remaining, minChars, maxChars);
    if (!piece) break;
    remaining = remaining.slice(piece.length).trim();
    if (remaining.length > 0 && remaining.length < minTailChars) {
      pieces.push(`${piece} ${remaining}`.trim());
      remaining = '';
    } else {
      pieces.push(piece);
    }
    if (stage < windows.length) stage++;
  }

  return pieces.length > 0 ? pieces : [normalized];
}

export function useAudioPlayback({ token, defaultVoice = 'bm_george' }: UseAudioPlaybackOptions) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [playingMessageId, setPlayingMessageId] = useState<string | null>(null);
  const [isBuffering, setIsBuffering] = useState(false);
  const [autoSpeak, setAutoSpeak] = useState<boolean>(() => {
    return localStorage.getItem('eidos_auto_speak') === 'true';
  });
  const [playbackError, setPlaybackError] = useState<string | null>(null);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const activeUrlsRef = useRef<string[]>([]);
  const currentReqControllersRef = useRef<AbortController[]>([]);
  const sessionIdRef = useRef<number>(0);
  const wakeLockRef = useRef<any>(null);

  // Request screen wake lock so phone doesn't sleep while speech is actively playing
  const acquireWakeLock = useCallback(async () => {
    try {
      if ('wakeLock' in navigator && !wakeLockRef.current) {
        wakeLockRef.current = await (navigator as any).wakeLock.request('screen');
        wakeLockRef.current.addEventListener('release', () => {
          wakeLockRef.current = null;
        });
      }
    } catch {}
  }, []);

  const releaseWakeLock = useCallback(() => {
    try {
      if (wakeLockRef.current) {
        wakeLockRef.current.release().catch(() => {});
        wakeLockRef.current = null;
      }
    } catch {}
  }, []);

  // Update OS Media Session so mobile OS keeps background audio alive & shows lock screen controls
  const updateMediaSession = useCallback((title: string, state: 'playing' | 'paused' | 'none') => {
    if (!('mediaSession' in navigator)) return;
    try {
      if (state === 'none') {
        navigator.mediaSession.playbackState = 'none';
        return;
      }
      navigator.mediaSession.metadata = new MediaMetadata({
        title: title || 'EidosBot Response',
        artist: 'EidosBot',
        album: 'Voice Assistant',
        artwork: [
          { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' },
        ],
      });
      navigator.mediaSession.playbackState = state;
    } catch {}
  }, []);

  const toggleAutoSpeak = useCallback(() => {
    setAutoSpeak((prev) => {
      const next = !prev;
      localStorage.setItem('eidos_auto_speak', String(next));
      if (next) {
        // Unlock HTML5 audio playback context on mobile browser gesture
        try {
          const silentAudio = new Audio(
            'data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA'
          );
          silentAudio.play().catch(() => {});
        } catch {}
      }
      return next;
    });
  }, []);

  const stopAudio = useCallback(() => {
    // Invalidate any in-flight playback sessions
    sessionIdRef.current += 1;

    // Release wake lock and update media session
    releaseWakeLock();
    updateMediaSession('', 'none');

    // Abort all active chunk requests
    for (const controller of currentReqControllersRef.current) {
      try {
        controller.abort();
      } catch {}
    }
    currentReqControllersRef.current = [];

    // Pause and reset audio element
    if (audioRef.current) {
      try {
        audioRef.current.pause();
        audioRef.current.currentTime = 0;
        audioRef.current.src = '';
      } catch {}
    }

    // Revoke all created blob URLs to avoid memory leaks
    for (const url of activeUrlsRef.current) {
      try {
        URL.revokeObjectURL(url);
      } catch {}
    }
    activeUrlsRef.current = [];

    setIsPlaying(false);
    setIsBuffering(false);
    setPlayingMessageId(null);
  }, [releaseWakeLock, updateMediaSession]);

  // Register Media Session remote action handlers once
  useEffect(() => {
    if (!('mediaSession' in navigator)) return;
    try {
      navigator.mediaSession.setActionHandler('play', () => {
        if (audioRef.current && audioRef.current.paused) {
          audioRef.current.play().catch(() => {});
        }
      });
      navigator.mediaSession.setActionHandler('pause', () => {
        stopAudio();
      });
      navigator.mediaSession.setActionHandler('stop', () => {
        stopAudio();
      });
    } catch {}
  }, [stopAudio]);

  // Re-acquire wake lock if tab returns to foreground while audio is still playing
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible' && isPlaying) {
        acquireWakeLock();
      }
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [isPlaying, acquireWakeLock]);

  const playText = useCallback(
    async (text: string, messageId?: string, voice?: string) => {
      // Toggle off if already playing this message
      if (isPlaying && playingMessageId && messageId === playingMessageId) {
        stopAudio();
        return;
      }

      stopAudio();
      setPlaybackError(null);

      // Strip thoughts, recalls, code blocks, emojis, and markdown noise
      const cleanText = text
        .replace(/<thought>[\s\S]*?<\/thought>/gi, '')
        .replace(/<recall>[\s\S]*?<\/recall>/gi, '')
        .replace(/<[^>]+>/g, ' ')
        .replace(/```[\s\S]*?```/g, ' Code snippet omitted. ')
        .replace(/`([^`]+)`/g, '$1')
        .replace(/[#*_~>]/g, '')
        .replace(/[\u{1F300}-\u{1F9FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}]/gu, '')
        .replace(/\s+/g, ' ')
        .trim();

      if (!cleanText) return;

      const currentSessionId = sessionIdRef.current;
      const chunks = splitTextIntoChunks(cleanText);
      if (chunks.length === 0) return;

      setIsBuffering(true);
      if (messageId) {
        setPlayingMessageId(messageId);
        try {
          localStorage.setItem('eidos_last_spoken_msg_id', messageId);
        } catch {}
      }

      // Track URLs and in-flight fetch promises per chunk
      const chunkUrls: (string | null)[] = new Array(chunks.length).fill(null);
      const chunkPromises: (Promise<string> | null)[] = new Array(chunks.length).fill(null);

      const fetchChunk = (index: number): Promise<string> => {
        if (index < 0 || index >= chunks.length) {
          return Promise.reject(new Error(`Invalid chunk index ${index}`));
        }
        if (chunkPromises[index]) {
          return chunkPromises[index]!;
        }

        const controller = new AbortController();
        currentReqControllersRef.current.push(controller);

        const promise = (async () => {
          const ttsUrl = token ? `/api/tts?token=${encodeURIComponent(token)}` : '/api/tts';
          const res = await fetch(ttsUrl, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            body: JSON.stringify({
              text: chunks[index],
              voice: voice || defaultVoice,
            }),
            signal: controller.signal,
          });

          if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.error || `TTS synthesis failed (${res.status})`);
          }

          const blob = await res.blob();
          if (sessionIdRef.current !== currentSessionId) {
            return '';
          }
          const audioUrl = URL.createObjectURL(blob);
          activeUrlsRef.current.push(audioUrl);
          chunkUrls[index] = audioUrl;
          return audioUrl;
        })();

        chunkPromises[index] = promise;
        return promise;
      };

      // Sequential playback runner
      const playChunk = async (index: number) => {
        if (sessionIdRef.current !== currentSessionId) return;

        if (index >= chunks.length) {
          // All chunks completed
          releaseWakeLock();
          updateMediaSession('', 'none');
          setIsPlaying(false);
          setIsBuffering(false);
          setPlayingMessageId(null);
          return;
        }

        let audioUrl = chunkUrls[index];
        if (!audioUrl) {
          setIsBuffering(true);
          updateMediaSession(chunks[index] || 'EidosBot Response', 'paused');
          try {
            audioUrl = await fetchChunk(index);
          } catch (err: any) {
            if (err.name === 'AbortError' || sessionIdRef.current !== currentSessionId) {
              return;
            }
            console.error(`TTS fetch failed for chunk ${index}:`, err);
            setPlaybackError(err.message || 'TTS synthesis failed');
            stopAudio();
            return;
          }
        }

        if (sessionIdRef.current !== currentSessionId || !audioUrl) return;

        let audio = audioRef.current;
        if (!audio) {
          audio = new Audio();
          audioRef.current = audio;
        }

        audio.src = audioUrl;

        audio.oncanplay = () => {
          if (sessionIdRef.current === currentSessionId) {
            setIsBuffering(false);
          }
        };

        audio.onplay = () => {
          if (sessionIdRef.current === currentSessionId) {
            acquireWakeLock();
            updateMediaSession(chunks[index] || 'EidosBot Response', 'playing');
            setIsPlaying(true);
            setIsBuffering(false);
          }
        };

        audio.onended = () => {
          if (sessionIdRef.current === currentSessionId) {
            playChunk(index + 1);
          }
        };

        audio.onerror = (e) => {
          if (sessionIdRef.current !== currentSessionId) return;
          console.error(`Audio playback error on chunk ${index}:`, e);
          setPlaybackError('Failed to play audio');
          stopAudio();
        };

        try {
          await audio.play();
        } catch (err: any) {
          if (sessionIdRef.current !== currentSessionId) return;
          console.error(`Audio play() failed on chunk ${index}:`, err);
          setPlaybackError(err.message || 'Failed to start audio playback');
          stopAudio();
        }
      };

      // 1. Fetch chunk 0 immediately so speech starts with minimal latency (~1.2s)
      fetchChunk(0).catch(() => {});

      // 2. Proactively prefetch all subsequent chunks in a background pipeline
      // so by the time chunk 0 finishes playing, subsequent chunks are ALREADY in memory
      // as local Blob URLs, ensuring seamless playback even if screen turns off or user switches apps.
      (async () => {
        if (chunks.length > 1) {
          fetchChunk(1).catch(() => {});
        }
        for (let i = 2; i < chunks.length; i++) {
          if (sessionIdRef.current !== currentSessionId) break;
          try {
            await fetchChunk(i);
          } catch {
            // Deferred error handling inside playChunk
          }
        }
      })();

      // Begin playing from chunk 0
      playChunk(0);
    },
    [token, defaultVoice, isPlaying, playingMessageId, stopAudio, acquireWakeLock, releaseWakeLock, updateMediaSession]
  );

  useEffect(() => {
    return () => {
      stopAudio();
    };
  }, [stopAudio]);

  return {
    isPlaying,
    playingMessageId,
    isBuffering,
    autoSpeak,
    toggleAutoSpeak,
    playbackError,
    playText,
    stopAudio,
  };
}
