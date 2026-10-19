import React, { useState, useRef, useEffect, useCallback } from 'react';
import { ChatMessage, SharedFile } from '../types/gateway';
import { ThoughtAccordion } from './ThoughtAccordion';
import { MarkdownRenderer, copyToClipboard, markdownToPlainText } from './MarkdownRenderer';
import {
  Send,
  Volume2,
  VolumeX,
  Loader2,
  Square,
  Mic,
  MicOff,
  Bot,
  User,
  Copy,
  Check,
  AlertCircle,
  X,
  Maximize2,
  Minimize2,
  Plus,
  RefreshCw,
  Paperclip,
  Pencil,
  Pause,
  Play,
  RotateCcw,
  RotateCw,
  SkipBack,
  SkipForward,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import { SharedFiles } from './SharedFiles';

function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
  const whole = Math.floor(seconds);
  const minutes = Math.floor(whole / 60);
  const remain = whole % 60;
  return `${minutes}:${remain.toString().padStart(2, '0')}`;
}

interface ChatViewProps {
  messages: ChatMessage[];
  sharedFiles?: SharedFile[];
  onSendMessage: (text: string) => void;
  onEditRetry?: (messageIndex: number, prompt: string, messageId: string) => void;
  retryNotice?: string | null;
  onDismissRetryNotice?: () => void;
  token: string;
  isThinking: boolean;
  isPlaying?: boolean;
  playbackActive?: boolean;
  isPaused?: boolean;
  position?: number;
  duration?: number;
  playingMessageId?: string | null;
  isBuffering?: boolean;
  autoSpeak?: boolean;
  onToggleAutoSpeak?: () => void;
  onPlayAudio?: (text: string, messageId: string) => void;
  onStopAudio?: () => void;
  onPauseAudio?: () => void;
  onResumeAudio?: () => void;
  onSeekBy?: (seconds: number) => void;
  onSkipChunk?: (direction: -1 | 1) => void;
  /** Start the background audio session in the send gesture, before the reply exists. */
  onPreparePlayback?: () => void;
  /** Drop that session when the send is cancelled or fails before a reply. */
  onReleasePreparedPlayback?: () => void;
  onStopTask?: () => void;
  playbackError?: string | null;
  conversationTitle?: string;
  onNewChat?: () => void;
  onRefreshSync?: () => void;
  isSyncing?: boolean;
  onRecordingChange?: (isRecording: boolean) => void;
}

function parseEmbeddedThoughts(
  content: string,
  existingThoughts: string[] = []
): { cleanContent: string; allThoughts: string[] } {
  let clean = content || '';
  const extracted: string[] = [...existingThoughts];

  // Extract <thought>...</thought> or <thinking>...</thinking>
  clean = clean.replace(/<(?:thought|thinking)>([\s\S]*?)<\/(?:thought|thinking)>/gi, (_, thoughtText) => {
    if (thoughtText.trim()) extracted.push(thoughtText.trim());
    return '';
  });

  // Extract <recall>...</recall>
  clean = clean.replace(/<recall>([\s\S]*?)<\/recall>/gi, (_, recallText) => {
    if (recallText.trim()) extracted.push(`[Memory Recall] ${recallText.trim()}`);
    return '';
  });

  // Extract unclosed <thought>... to end of string if generation cut off
  clean = clean.replace(/<(?:thought|thinking)>([\s\S]*)$/gi, (_, thoughtText) => {
    if (thoughtText.trim()) extracted.push(thoughtText.trim());
    return '';
  });

  return { cleanContent: clean.trim(), allThoughts: extracted };
}

export const ChatView: React.FC<ChatViewProps> = ({
  messages,
  sharedFiles = [],
  onSendMessage,
  onEditRetry,
  retryNotice,
  onDismissRetryNotice,
  token,
  isThinking,
  isPlaying = false,
  playbackActive = false,
  isPaused = false,
  position = 0,
  duration = 0,
  playingMessageId = null,
  isBuffering = false,
  autoSpeak = false,
  onToggleAutoSpeak,
  onPlayAudio,
  onStopAudio,
  onPauseAudio,
  onResumeAudio,
  onSeekBy,
  onSkipChunk,
  onPreparePlayback,
  onReleasePreparedPlayback,
  onStopTask,
  playbackError,
  conversationTitle = 'New Chat',
  onNewChat,
  onRefreshSync,
  isSyncing = false,
  onRecordingChange,
}) => {
  const [inputText, setInputText] = useState('');
  const [isExpanded, setIsExpanded] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);

  useEffect(() => {
    onRecordingChange?.(isRecording);
  }, [isRecording, onRecordingChange]);
  const [recordingDuration, setRecordingDuration] = useState(0);
  const [autoSubmitVoice, setAutoSubmitVoice] = useState<boolean>(() => {
    return localStorage.getItem('eidos_auto_submit_voice') !== 'false';
  });
  const [micNotice, setMicNotice] = useState<string | null>(null);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [attachNotice, setAttachNotice] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState('');

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const editRef = useRef<HTMLTextAreaElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const cancelledRef = useRef<boolean>(false);
  const recordingTimerRef = useRef<number | null>(null);
  const micArmedPlaybackRef = useRef(false);
  const preparePlaybackRef = useRef(onPreparePlayback);
  const releasePlaybackRef = useRef(onReleasePreparedPlayback);
  const autoSpeakRef = useRef(autoSpeak);
  const autoSubmitVoiceRef = useRef(autoSubmitVoice);
  preparePlaybackRef.current = onPreparePlayback;
  releasePlaybackRef.current = onReleasePreparedPlayback;
  autoSpeakRef.current = autoSpeak;
  autoSubmitVoiceRef.current = autoSubmitVoice;

  const releaseMicPlayback = () => {
    if (!micArmedPlaybackRef.current) return;
    micArmedPlaybackRef.current = false;
    releasePlaybackRef.current?.();
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isThinking]);

  // Recording duration timer
  useEffect(() => {
    if (isRecording) {
      setRecordingDuration(0);
      recordingTimerRef.current = window.setInterval(() => {
        setRecordingDuration((prev) => prev + 1);
      }, 1000);
    } else {
      if (recordingTimerRef.current) {
        clearInterval(recordingTimerRef.current);
        recordingTimerRef.current = null;
      }
      setRecordingDuration(0);
    }
    return () => {
      if (recordingTimerRef.current) {
        clearInterval(recordingTimerRef.current);
        recordingTimerRef.current = null;
      }
    };
  }, [isRecording]);

  // Cleanup active audio tracks on unmount
  useEffect(() => {
    return () => {
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        try {
          mediaRecorderRef.current.stop();
        } catch {}
      }
      if (mediaStreamRef.current) {
        mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      }
    };
  }, []);

  const formatDuration = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs < 10 ? '0' : ''}${secs}`;
  };

  const adjustTextareaHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const minHeight = isExpanded ? 160 : 40;
    const maxHeight = isExpanded ? 320 : 180;
    const targetHeight = Math.max(minHeight, Math.min(el.scrollHeight, maxHeight));
    el.style.height = `${targetHeight}px`;
  }, [isExpanded]);

  useEffect(() => {
    adjustTextareaHeight();
  }, [inputText, isExpanded, adjustTextareaHeight]);

  useEffect(() => {
    if (!editingId) return;
    const el = editRef.current;
    if (!el) return;
    el.focus();
    const end = el.value.length;
    el.setSelectionRange(end, end);
  }, [editingId]);

  useEffect(() => {
    if (editingId && !messages.some((message) => message.id === editingId)) {
      setEditingId(null);
      setEditDraft('');
    }
  }, [messages, editingId]);

  const beginEdit = (message: ChatMessage) => {
    if (isThinking || !onEditRetry) return;
    setEditingId(message.id);
    setEditDraft(message.content);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditDraft('');
  };

  const submitEdit = (messageIndex: number, messageId: string) => {
    const text = editDraft.trim();
    if (!text || !onEditRetry || isThinking) return;
    if (autoSpeak) onPreparePlayback?.();
    onEditRetry(messageIndex, text, messageId);
    setEditingId(null);
    setEditDraft('');
  };

  const retryFrom = (messageIndex: number) => {
    if (isThinking || !onEditRetry) return;
    let userIndex = messageIndex;
    const selected = messages[messageIndex];
    if (!selected || selected.role !== 'user') {
      for (let index = messageIndex; index >= 0; index -= 1) {
        if (messages[index]?.role === 'user' && messages[index].content.trim()) {
          userIndex = index;
          break;
        }
      }
    }
    const user = messages[userIndex];
    if (!user || user.role !== 'user' || !user.content.trim()) return;
    if (autoSpeak) onPreparePlayback?.();
    onEditRetry(userIndex, user.content, user.id);
  };

  const handleSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (isThinking || isUploading) return;
    const text = inputText.trim();
    if (!text && pendingFiles.length === 0) return;
    if (autoSpeak) onPreparePlayback?.();
    if (pendingFiles.length === 0) {
      onSendMessage(text);
      setInputText('');
      setIsExpanded(false);
      if (textareaRef.current) {
        textareaRef.current.style.height = '40px';
      }
      return;
    }
    void uploadAndSend(text);
  };

  const uploadAndSend = async (text: string) => {
    setIsUploading(true);
    setAttachNotice(null);
    try {
      const form = new FormData();
      if (text) form.append('message', text);
      pendingFiles.forEach((file) => form.append('file', file, file.name));
      const res = await fetch('/api/files', {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        body: form,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) {
        setAttachNotice(data.error || `Upload failed (${res.status})`);
        if (autoSpeak) onReleasePreparedPlayback?.();
        return;
      }
      setInputText('');
      setPendingFiles([]);
      setIsExpanded(false);
      if (textareaRef.current) {
        textareaRef.current.style.height = '40px';
      }
    } catch (err) {
      setAttachNotice(err instanceof Error ? err.message : 'Upload failed');
      if (autoSpeak) onReleasePreparedPlayback?.();
    } finally {
      setIsUploading(false);
    }
  };

  const handleCopy = async (id: string, text: string) => {
    await copyToClipboard(markdownToPlainText(text));
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const toggleAutoSubmitVoice = () => {
    setAutoSubmitVoice((prev) => {
      const next = !prev;
      localStorage.setItem('eidos_auto_submit_voice', String(next));
      return next;
    });
  };

  // Continuous audio capture via MediaRecorder -> Server-side Parakeet STT
  const startRecording = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setMicNotice('Audio recording is not supported in this browser. Ensure HTTPS is active.');
      return;
    }

    try {
      cancelledRef.current = false;
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      mediaStreamRef.current = stream;
      audioChunksRef.current = [];

      // Handle physical track termination (e.g. mic hardware disconnect)
      stream.getAudioTracks().forEach((track) => {
        track.onended = () => {
          if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
            stopRecording();
          }
        };
      });

      let mimeType = '';
      if (typeof MediaRecorder !== 'undefined') {
        if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
          mimeType = 'audio/webm;codecs=opus';
        } else if (MediaRecorder.isTypeSupported('audio/webm')) {
          mimeType = 'audio/webm';
        } else if (MediaRecorder.isTypeSupported('audio/ogg;codecs=opus')) {
          mimeType = 'audio/ogg;codecs=opus';
        } else if (MediaRecorder.isTypeSupported('audio/mp4')) {
          mimeType = 'audio/mp4';
        }
      }

      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      recorder.onstop = async () => {
        // Stop physical microphone hardware tracks
        stream.getTracks().forEach((track) => track.stop());
        setIsRecording(false);

        if (cancelledRef.current) {
          cancelledRef.current = false;
          audioChunksRef.current = [];
          releaseMicPlayback();
          return;
        }

        if (audioChunksRef.current.length === 0) return;

        const audioBlob = new Blob(audioChunksRef.current, {
          type: mimeType || 'audio/webm',
        });
        audioChunksRef.current = [];

        // Check if recording was too short / empty (< 600 bytes)
        if (audioBlob.size < 600) {
          setMicNotice('Recording was too short to transcribe.');
          setTimeout(() => setMicNotice(null), 3000);
          releaseMicPlayback();
          return;
        }

        setIsTranscribing(true);
        try {
          const sttUrl = token ? `/api/stt?token=${encodeURIComponent(token)}` : '/api/stt';
          const res = await fetch(sttUrl, {
            method: 'POST',
            headers: {
              'Content-Type': audioBlob.type,
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            body: audioBlob,
          });

          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error || `STT transcription failed (${res.status})`);
          }

          const data = await res.json();
          const transcribed = (data.text || '').trim();
          if (transcribed) {
            if (autoSubmitVoice) {
              setInputText('');
              micArmedPlaybackRef.current = false;
              onSendMessage(transcribed);
            } else {
              setInputText((prev) => (prev ? `${prev} ${transcribed}` : transcribed));
              releaseMicPlayback();
            }
          } else {
            setMicNotice('No speech detected in audio.');
            setTimeout(() => setMicNotice(null), 3000);
            releaseMicPlayback();
          }
        } catch (err: any) {
          console.error('Host Parakeet STT error:', err);
          setMicNotice(`Host Parakeet STT error: ${err.message || 'Transcription failed'}`);
          releaseMicPlayback();
        } finally {
          setIsTranscribing(false);
        }
      };

      // Request slices every 500ms
      recorder.start(500);
      setIsRecording(true);
      setMicNotice(null);
    } catch (err: any) {
      console.error('Microphone capture error:', err);
      setIsRecording(false);
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        const host = window.location.host;
        setMicNotice(
          `Microphone permission denied. In Chrome on Android, open chrome://flags/#unsafely-treat-insecure-origin-as-secure, enter http://${host}, tap Relaunch, and reload.`
        );
      } else {
        setMicNotice(`Could not access microphone: ${err.message}`);
      }
    }
  }, [token, autoSubmitVoice, onSendMessage]);

  const stopRecording = useCallback(() => {
    if (
      autoSpeakRef.current &&
      autoSubmitVoiceRef.current &&
      mediaRecorderRef.current &&
      mediaRecorderRef.current.state !== 'inactive'
    ) {
      micArmedPlaybackRef.current = true;
      preparePlaybackRef.current?.();
    }
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      try {
        mediaRecorderRef.current.stop();
      } catch {}
    }
  }, []);

  const cancelRecording = useCallback(() => {
    cancelledRef.current = true;
    audioChunksRef.current = [];
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      try {
        mediaRecorderRef.current.stop();
      } catch {}
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
    }
    setIsRecording(false);
  }, []);

  const toggleRecording = useCallback(() => {
    if (isRecording) {
      stopRecording();
    } else if (!isTranscribing) {
      startRecording();
    }
  }, [isRecording, isTranscribing, stopRecording, startRecording]);

  const handleStopSpeech = useCallback(() => {
    onStopAudio?.();
  }, [onStopAudio]);

  const handleStopTask = useCallback(() => {
    onStopTask?.();
  }, [onStopTask]);

  useEffect(() => {
    const isMobile =
      typeof navigator !== 'undefined' &&
      (/android|iphone|ipad|ipod/i.test(navigator.userAgent) ||
        ('maxTouchPoints' in navigator && navigator.maxTouchPoints > 1));

    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      // NEVER allow mobile/Android back gestures or virtual keyboard dismissals
      // (which emit Escape keydown events) to kill running tasks or cancel recordings!
      if (isMobile) return;

      if (e.key !== 'Escape') return;
      if (isThinking) {
        e.preventDefault();
        if (isRecording) stopRecording();
        handleStopTask();
        return;
      }
      if (isPlaying || isRecording) {
        e.preventDefault();
        if (isRecording) stopRecording();
        if (isPlaying) handleStopSpeech();
      }
    };
    window.addEventListener('keydown', handleGlobalKeyDown);
    return () => window.removeEventListener('keydown', handleGlobalKeyDown);
  }, [isThinking, isPlaying, isRecording, handleStopSpeech, handleStopTask, stopRecording]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Escape') {
      if (editingId) {
        e.preventDefault();
        cancelEdit();
        return;
      }
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="flex flex-col h-full bg-[#1e1f29]">
      {/* Top Banner for Error/Permissions if active */}
      {micNotice && (
        <div className="mx-3 mt-3 p-2.5 rounded-xl bg-[#ff5555]/15 border border-[#ff5555]/40 text-[11px] text-[#f8f8f2] flex items-start justify-between gap-2 shadow-lg animate-fadeIn">
          <div className="flex items-start gap-2">
            <AlertCircle className="w-4 h-4 text-[#ff5555] shrink-0 mt-0.5" />
            <span className="leading-relaxed">{micNotice}</span>
          </div>
          <button
            onClick={() => setMicNotice(null)}
            className="text-[#6272a4] hover:text-[#f8f8f2] text-xs font-bold px-1"
          >
            ✕
          </button>
        </div>
      )}

      {/* Messages Scroll Area */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center p-6 text-[#6272a4]">
            <div className="w-14 h-14 rounded-2xl bg-[#282a36] border border-[#44475a] flex items-center justify-center text-[#bd93f9] mb-3 shadow-lg">
              <Bot className="w-8 h-8" />
            </div>
            <h3 className="text-sm font-bold text-[#f8f8f2] mb-1">
              {conversationTitle || 'New Conversation'}
            </h3>
            <p className="text-xs max-w-xs leading-relaxed mb-4">
              Send a message, attach a document, or review the files Eidos puts together.
            </p>
            <div className="flex items-center gap-2">
              {onNewChat && (
                <button
                  type="button"
                  onClick={onNewChat}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-[#282a36] border border-[#44475a] text-xs font-semibold text-[#50fa7b] hover:border-[#50fa7b] hover:bg-[#44475a]/30 transition active:scale-95"
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>Start Fresh Chat</span>
                </button>
              )}
              {onRefreshSync && (
                <button
                  type="button"
                  onClick={onRefreshSync}
                  disabled={isSyncing}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-[#282a36] border border-[#44475a] text-xs font-semibold text-[#bd93f9] hover:border-[#bd93f9] hover:bg-[#44475a]/30 transition active:scale-95"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${isSyncing ? 'animate-spin' : ''}`} />
                  <span>Sync with Desktop</span>
                </button>
              )}
            </div>
          </div>
        ) : (
          messages.map((msg, messageIndex) => {
            const isUser = msg.role === 'user';
            const isEditing = isUser && editingId === msg.id;
            const { cleanContent, allThoughts } = isUser
              ? { cleanContent: msg.content, allThoughts: [] }
              : parseEmbeddedThoughts(msg.content, msg.thoughts || []);

            const isThisMsgBuffering = isBuffering && playingMessageId === msg.id;
            const isThisMsgActive = Boolean(
              playbackActive && playingMessageId === msg.id && (isPlaying || isPaused || isBuffering)
            );
            const isThisMsgPlaying = isThisMsgActive && !isThisMsgBuffering;
            const isErrorMsg = !isUser && (
              cleanContent.startsWith('Error communicating with') ||
              cleanContent.includes('429 Client Error') ||
              cleanContent.includes('Rate limit or concurrency quota exceeded') ||
              cleanContent.includes('Rate Limit Exceeded')
            );

            return (
              <div
                key={msg.id}
                className={`flex gap-2.5 ${isUser ? 'justify-end' : 'justify-start'} animate-fadeIn`}
              >
                {!isUser && (
                  <div className="w-7 h-7 rounded-lg bg-[#bd93f9]/20 border border-[#bd93f9]/40 flex items-center justify-center text-[#bd93f9] shrink-0 mt-0.5">
                    <Bot className="w-4 h-4" />
                  </div>
                )}

                <div
                  className={`min-w-0 rounded-2xl px-4 py-3 text-xs leading-relaxed relative group ${
                    isUser
                      ? 'max-w-[88%] sm:max-w-xl bg-[#bd93f9] text-[#1e1f29] font-medium rounded-tr-none'
                      : 'max-w-[calc(100%-2.75rem)] bg-[#282a36] border border-[#44475a] text-[#f8f8f2] rounded-tl-none shadow-md'
                  }`}
                >
                  {/* Collapsible Thoughts (for assistant messages) */}
                  {!isUser && allThoughts.length > 0 && (
                    <ThoughtAccordion
                      thoughts={allThoughts}
                      playbackId={`${msg.id}:reasoning`}
                      isPlaying={isPlaying && playingMessageId === `${msg.id}:reasoning`}
                      isBuffering={isBuffering && playingMessageId === `${msg.id}:reasoning`}
                      onPlayAudio={onPlayAudio}
                      onStopAudio={onStopAudio}
                    />
                  )}

                  {!isUser && (msg.actions?.length || 0) > 0 && (
                    <div className="mb-2 space-y-1">
                      {msg.actions!.map((action, index) => (
                        <div
                          key={`${action.tool}-${index}`}
                          className="rounded-lg border border-[#44475a] bg-[#1e1f29] px-2.5 py-1.5 text-[11px]"
                        >
                          <div className="font-semibold text-[#8be9fd]">{action.tool}</div>
                          {action.arguments ? (
                            <div className="text-[#6272a4] break-words whitespace-pre-wrap">{action.arguments}</div>
                          ) : null}
                          {action.result ? (
                            <div className="mt-0.5 text-[#f8f8f2]/80 break-words whitespace-pre-wrap">{action.result}</div>
                          ) : action.pending ? (
                            <div className="italic text-[#6272a4]">Running…</div>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Message Body with Rich Markdown */}
                  <div className="select-text font-sans text-xs">
                    {isEditing ? (
                      <textarea
                        ref={editRef}
                        value={editDraft}
                        onChange={(event) => setEditDraft(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Escape') {
                            event.preventDefault();
                            event.stopPropagation();
                            cancelEdit();
                            return;
                          }
                          if (event.key === 'Enter' && !event.shiftKey) {
                            event.preventDefault();
                            submitEdit(messageIndex, msg.id);
                          }
                        }}
                        className="w-full min-h-[72px] max-h-48 bg-[#1e1f29] text-[#f8f8f2] rounded-lg px-2.5 py-2 text-xs leading-relaxed resize-none focus:outline-none"
                      />
                    ) : isUser ? (
                      <div className="whitespace-pre-wrap break-words">{msg.content}</div>
                    ) : isErrorMsg ? (
                      <div className="p-2.5 rounded-lg bg-[#ff5555]/15 border border-[#ff5555]/40 text-[#ff5555] space-y-1">
                        <div className="font-semibold flex items-center gap-1.5 text-xs text-[#ff5555]">
                          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                          <span>Model Execution Error</span>
                        </div>
                        <div className="text-[11px] leading-relaxed break-words font-mono text-[#f8f8f2]/90">
                          {cleanContent}
                        </div>
                      </div>
                    ) : cleanContent ? (
                      <MarkdownRenderer
                        content={cleanContent}
                        playbackPrefix={msg.id}
                        playingMessageId={playingMessageId}
                        playbackActive={Boolean(playbackActive && (isPlaying || isPaused || isBuffering))}
                        isBuffering={isBuffering}
                        onPlayAudio={onPlayAudio}
                        onStopAudio={onStopAudio}
                      />
                    ) : msg.streaming && isThinking ? (
                      <span className="italic text-[#6272a4] animate-pulse">Deliberating response...</span>
                    ) : allThoughts.length > 0 ? (
                      <span className="italic text-[#6272a4]">Deliberation finished (no output message produced).</span>
                    ) : (
                      <span className="italic text-[#6272a4]">No response content.</span>
                    )}
                  </div>

                  {isUser && onEditRetry && (
                    isEditing ? (
                      <div className="mt-2 space-y-1.5">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            type="button"
                            onClick={cancelEdit}
                            className="px-2 py-1 rounded-lg text-[11px] font-semibold text-[#1e1f29]/70 hover:bg-[#1e1f29]/10"
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            onClick={() => submitEdit(messageIndex, msg.id)}
                            disabled={!editDraft.trim() || isThinking}
                            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-[#1e1f29] text-[#f8f8f2] text-[11px] font-semibold disabled:opacity-40"
                          >
                            <RotateCcw className="w-3 h-3" />
                            Save &amp; Retry
                          </button>
                        </div>
                        <p className="text-[10px] text-[#1e1f29]/60 text-right">
                          Enter to retry · Shift+Enter for a new line · Esc to cancel
                        </p>
                      </div>
                    ) : (
                      <div className="flex items-center justify-end gap-1 mt-2 pt-1 border-t border-[#1e1f29]/15">
                        <button
                          type="button"
                          onClick={() => beginEdit(msg)}
                          disabled={isThinking}
                          title={isThinking ? 'Wait for the current reply to finish' : 'Edit this prompt and run it again. Later messages in this chat are replaced.'}
                          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[11px] font-semibold text-[#1e1f29]/80 hover:bg-[#1e1f29]/10 disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                          <Pencil className="w-3 h-3" />
                          Edit
                        </button>
                        <button
                          type="button"
                          onClick={() => retryFrom(messageIndex)}
                          disabled={isThinking}
                          title={isThinking ? 'Wait for the current reply to finish' : 'Run this prompt again. Later messages in this chat are replaced.'}
                          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[11px] font-semibold text-[#1e1f29]/80 hover:bg-[#1e1f29]/10 disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                          <RotateCcw className="w-3 h-3" />
                          Retry
                        </button>
                      </div>
                    )
                  )}

                  {/* Action Row for Assistant Messages */}
                  {!isUser && !msg.streaming && (
                    <div className="flex items-center justify-end gap-2.5 mt-2 pt-1 border-t border-[#44475a]/40 text-[#6272a4]">
                      {onEditRetry && (
                        <button
                          type="button"
                          onClick={() => retryFrom(messageIndex)}
                          disabled={isThinking}
                          className="p-1 hover:text-[#f8f8f2] transition active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed"
                          title={isThinking ? 'Wait for the current reply to finish' : 'Run the prompt above again. Later messages in this chat are replaced.'}
                        >
                          <RotateCcw className="w-3.5 h-3.5" />
                        </button>
                      )}
                      {/* Copy message text */}
                      <button
                        onClick={() => handleCopy(msg.id, cleanContent || msg.content)}
                        className="p-1 hover:text-[#f8f8f2] transition active:scale-95"
                        title="Copy message as shown"
                      >
                        {copiedId === msg.id ? (
                          <Check className="w-3.5 h-3.5 text-[#50fa7b]" />
                        ) : (
                          <Copy className="w-3.5 h-3.5" />
                        )}
                      </button>

                      {/* Kokoro TTS Playback on Phone (Approach A) */}
                      {onPlayAudio && (
                        <button
                          onClick={() => {
                            if (isThisMsgActive) {
                              onStopAudio?.();
                            } else {
                              onPlayAudio(cleanContent || msg.content, msg.id);
                            }
                          }}
                          className={`p-1 rounded transition active:scale-95 flex items-center gap-1 ${
                            isThisMsgPlaying
                              ? 'text-[#ff5555] bg-[#ff5555]/10'
                              : isThisMsgBuffering
                              ? 'text-[#bd93f9]'
                              : 'hover:text-[#bd93f9]'
                          }`}
                          title={
                            isThisMsgBuffering
                              ? 'Synthesizing Kokoro speech...'
                              : isThisMsgPlaying
                              ? 'Stop speech playback'
                              : 'Read aloud on this device (Kokoro TTS)'
                          }
                        >
                          {isThisMsgBuffering ? (
                            <Loader2 className="w-3.5 h-3.5 animate-spin text-[#bd93f9]" />
                          ) : isThisMsgPlaying ? (
                            <Square className="w-3.5 h-3.5 fill-current text-[#ff5555]" />
                          ) : (
                            <Volume2 className="w-3.5 h-3.5" />
                          )}
                        </button>
                      )}
                    </div>
                  )}
                </div>

                {isUser && (
                  <div className="w-7 h-7 rounded-lg bg-[#50fa7b]/20 border border-[#50fa7b]/40 flex items-center justify-center text-[#50fa7b] shrink-0 mt-0.5">
                    <User className="w-4 h-4" />
                  </div>
                )}
              </div>
            );
          })
        )}

        {/* Live Typing / Thinking Indicator */}
        {isThinking && (
          <div className="flex items-center gap-2 text-xs text-[#6272a4] pl-2 animate-pulse">
            <span className="w-2 h-2 rounded-full bg-[#bd93f9]" />
            <span>EidosBot is thinking...</span>
          </div>
        )}

        {/* Transcribing audio indicator */}
        {isTranscribing && (
          <div className="flex items-center gap-2 text-xs text-[#50fa7b] pl-2 animate-pulse">
            <Loader2 className="w-3.5 h-3.5 animate-spin text-[#50fa7b]" />
            <span>Transcribing your speech with Parakeet STT...</span>
          </div>
        )}

        {playbackError && (
          <div className="text-[11px] text-[#ff5555] bg-[#ff5555]/10 border border-[#ff5555]/30 rounded-lg p-2 text-center">
            {playbackError}
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      <SharedFiles
        files={sharedFiles}
        token={token}
        readAloud={{
          playingMessageId,
          playbackActive: Boolean(playbackActive && (isPlaying || isPaused || isBuffering)),
          isBuffering,
          onPlayAudio,
          onStopAudio,
        }}
      />

      {/* Voice Controls Bar & Input Form */}
      <div className="p-3 bg-[#1e1f29] border-t border-[#44475a] space-y-2">
        {/* Quick Voice Bar */}
        <div className="flex items-center justify-between text-[11px] text-[#6272a4] px-1">
          <div className="flex items-center gap-3">
            {/* Auto-Read responses toggle */}
            {onToggleAutoSpeak && (
              <button
                type="button"
                onClick={onToggleAutoSpeak}
                className={`flex items-center gap-1.5 transition active:scale-95 ${
                  autoSpeak ? 'text-[#50fa7b] font-medium' : 'hover:text-[#f8f8f2]'
                }`}
                title="Automatically read assistant responses aloud via Kokoro TTS"
              >
                {autoSpeak ? <Volume2 className="w-3.5 h-3.5" /> : <VolumeX className="w-3.5 h-3.5" />}
                <span>Auto-Read: {autoSpeak ? 'ON' : 'OFF'}</span>
              </button>
            )}

            {/* Auto-Submit speech toggle */}
            <button
              type="button"
              onClick={toggleAutoSubmitVoice}
              className={`flex items-center gap-1.5 transition active:scale-95 ${
                autoSubmitVoice ? 'text-[#bd93f9] font-medium' : 'hover:text-[#f8f8f2]'
              }`}
              title="Send prompt automatically once dictation stops"
            >
              <span>Auto-Send Voice: {autoSubmitVoice ? 'ON' : 'OFF'}</span>
            </button>
          </div>
        </div>

        {/* Active Recording / Transcription Banner */}
        {isRecording && (
          <div className="flex items-center justify-between px-3 py-2 bg-[#ff5555]/15 border border-[#ff5555]/40 rounded-xl text-xs text-[#f8f8f2] shadow-sm animate-fadeIn">
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-[#ff5555] animate-ping" />
              <span className="font-semibold text-[#ff5555]">Recording ({formatDuration(recordingDuration)})</span>
              <span className="text-[#6272a4] text-[11px] hidden sm:inline">• Continuous Parakeet STT (won't cut out)</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={cancelRecording}
                className="px-2.5 py-1 rounded-lg bg-[#282a36] hover:bg-[#44475a] text-[#6272a4] hover:text-[#ff5555] text-[11px] font-medium transition active:scale-95"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={stopRecording}
                className="px-3 py-1 rounded-lg bg-[#ff5555] hover:bg-[#ff5555]/90 text-white font-semibold text-[11px] shadow-sm transition active:scale-95 flex items-center gap-1"
              >
                <Square className="w-3 h-3 fill-current" /> Finish
              </button>
            </div>
          </div>
        )}

        {isTranscribing && (
          <div className="flex items-center gap-2 px-3 py-2 bg-[#50fa7b]/15 border border-[#50fa7b]/40 rounded-xl text-xs text-[#50fa7b] animate-fadeIn">
            <Loader2 className="w-4 h-4 animate-spin text-[#50fa7b] shrink-0" />
            <span className="font-medium">Transcribing audio with Parakeet STT...</span>
          </div>
        )}

        {retryNotice && (
          <div className="text-[11px] text-[#ffb86c] bg-[#ffb86c]/10 border border-[#ffb86c]/30 rounded-lg p-2 flex items-start justify-between gap-2">
            <span>{retryNotice}</span>
            {onDismissRetryNotice && (
              <button
                type="button"
                onClick={onDismissRetryNotice}
                className="text-[#6272a4] hover:text-[#f8f8f2] font-bold px-1"
              >
                ✕
              </button>
            )}
          </div>
        )}

        {attachNotice && (
          <div className="text-[11px] text-[#ff5555] bg-[#ff5555]/10 border border-[#ff5555]/30 rounded-lg p-2">
            {attachNotice}
          </div>
        )}

        {/* Input Form Bar */}
        <form
          onSubmit={handleSubmit}
          className="max-w-3xl mx-auto bg-[#282a36] border border-[#44475a] focus-within:border-[#bd93f9] focus-within:ring-1 focus-within:ring-[#bd93f9]/40 rounded-2xl shadow-lg transition-all flex flex-col overflow-hidden"
        >
          {pendingFiles.length > 0 && (
            <div className="flex flex-wrap gap-1.5 px-3 pt-2">
              {pendingFiles.map((file, index) => (
                <span
                  key={`${file.name}-${index}`}
                  className="inline-flex items-center gap-1 max-w-full px-2 py-1 rounded-lg bg-[#44475a]/50 text-[11px] text-[#f8f8f2]"
                >
                  <Paperclip className="w-3 h-3 text-[#bd93f9] shrink-0" />
                  <span className="truncate">{file.name}</span>
                  <button
                    type="button"
                    title="Remove file"
                    onClick={() => setPendingFiles((prev) => prev.filter((_, i) => i !== index))}
                    className="text-[#6272a4] hover:text-[#ff5555]"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </span>
              ))}
            </div>
          )}
          {/* Text input area */}
          <div className="px-3.5 pt-2.5 pb-1 relative">
            <textarea
              ref={textareaRef}
              rows={1}
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                isRecording
                  ? 'Listening... Speak freely at your pace (tap Finish when done)'
                  : isTranscribing
                  ? 'Transcribing with Parakeet STT...'
                  : 'Message EidosBot... (Shift+Enter for newline)'
              }
              className={`w-full bg-transparent text-sm text-[#f8f8f2] placeholder-[#6272a4] leading-relaxed resize-none focus:outline-none overflow-y-auto transition-[height] duration-100 ease-out ${
                isExpanded ? 'min-h-[160px] max-h-80' : 'min-h-[40px] max-h-48'
              }`}
            />
          </div>

          {/* Action Toolbar */}
          <div className="flex items-center justify-between px-2.5 py-1.5 border-t border-[#44475a]/30 bg-[#21232d]/50">
            {/* Left Actions */}
            <div className="flex items-center gap-1">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(e) => {
                  const chosen = Array.from(e.target.files || []);
                  if (chosen.length) {
                    setPendingFiles((prev) => [...prev, ...chosen]);
                    setAttachNotice(null);
                  }
                  e.target.value = '';
                }}
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploading}
                title="Attach a file for Eidos to read"
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-xs font-medium text-[#8be9fd] hover:bg-[#8be9fd]/10 transition active:scale-95 disabled:opacity-40"
              >
                <Paperclip className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Attach</span>
              </button>
              {/* Dictate Button */}
              <button
                type="button"
                onClick={toggleRecording}
                disabled={isTranscribing}
                title={
                  isRecording
                    ? 'Recording in progress... Tap to finish and transcribe'
                    : isTranscribing
                    ? 'Transcribing with Parakeet STT...'
                    : 'Push to Dictate (Continuous Parakeet STT)'
                }
                className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-xs font-medium transition active:scale-95 ${
                  isRecording
                    ? 'bg-[#ff5555] text-white animate-pulse shadow-[0_0_10px_#ff5555]'
                    : isTranscribing
                    ? 'bg-[#44475a]/60 text-[#50fa7b] cursor-wait'
                    : 'text-[#bd93f9] hover:bg-[#bd93f9]/10'
                }`}
              >
                {isRecording ? (
                  <>
                    <Square className="w-3.5 h-3.5 fill-current" />
                    <span>Stop ({formatDuration(recordingDuration)})</span>
                  </>
                ) : isTranscribing ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    <span className="hidden sm:inline">Transcribing...</span>
                  </>
                ) : (
                  <>
                    <Mic className="w-3.5 h-3.5" />
                    <span className="hidden sm:inline">Dictate</span>
                  </>
                )}
              </button>
            </div>

            {/* Right Actions */}
            <div className="flex items-center gap-1.5">
              {/* Character count */}
              {inputText.length > 0 && (
                <span className="text-[11px] text-[#6272a4] hidden sm:inline mr-1">
                  {inputText.length} chars
                </span>
              )}

              {/* Expand / Minimize Toggle */}
              <button
                type="button"
                onClick={() => setIsExpanded((prev) => !prev)}
                title={isExpanded ? 'Collapse editor' : 'Expand editor for multi-line drafting'}
                className={`p-2 rounded-xl transition active:scale-95 ${
                  isExpanded
                    ? 'text-[#bd93f9] bg-[#bd93f9]/15'
                    : 'text-[#6272a4] hover:text-[#f8f8f2] hover:bg-[#44475a]/40'
                }`}
              >
                {isExpanded ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
              </button>

              {/* Submit, stop audio, or stop the running task. Audio stop never cancels the reply. */}
              {isThinking || isPlaying ? (
                <>
                  {isPlaying && (
                    <button
                      type="button"
                      onClick={handleStopSpeech}
                      title="Stop audio playback. The reply stays in the chat."
                      className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-[#44475a] text-[#f8f8f2] font-bold text-xs hover:bg-[#6272a4] transition active:scale-95 shadow-md"
                    >
                      <VolumeX className="w-3.5 h-3.5" />
                      <span>Stop audio</span>
                    </button>
                  )}
                  {isThinking && (
                    <button
                      type="button"
                      onClick={handleStopTask}
                      title="Stop the current task (Esc)"
                      className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-[#ff5555] text-white font-bold text-xs hover:bg-[#ff5555]/90 transition active:scale-95 shadow-md animate-pulse"
                    >
                      <Square className="w-3.5 h-3.5 fill-current" />
                      <span>Stop</span>
                    </button>
                  )}
                </>
              ) : (
                <button
                  type="submit"
                  disabled={(!inputText.trim() && pendingFiles.length === 0) || isUploading}
                  title={pendingFiles.length ? 'Send message and files' : 'Send Message (Enter)'}
                  className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-[#bd93f9] text-[#1e1f29] font-medium text-xs hover:bg-[#bd93f9]/90 disabled:opacity-30 disabled:cursor-not-allowed transition active:scale-95 shadow-md"
                >
                  <span>{isUploading ? 'Sending…' : 'Send'}</span>
                  <Send className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};
