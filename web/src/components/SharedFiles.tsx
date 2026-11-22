import { useCallback, useEffect, useRef, useState } from 'react';
import { SharedFile } from '../types/gateway';
import { codeToSpeech, markdownToPlainText, MarkdownRenderer, ReadAloudControl } from './MarkdownRenderer';
import { ChevronDown, ChevronRight, Download, Eye, FileText, Loader2, Maximize2, Minimize2, RotateCcw, Square, Volume2, X, ZoomIn, ZoomOut } from 'lucide-react';

interface SharedFilesProps {
  files: SharedFile[];
  token: string;
  readAloud?: Omit<ReadAloudControl, 'playbackPrefix'>;
}

type PreviewKind = 'image' | 'pdf' | 'text' | 'html' | 'binary';

const TEXT_EXT = new Set([
  'md', 'txt', 'json', 'csv', 'py', 'ts', 'tsx', 'js', 'jsx', 'yaml', 'yml',
  'log', 'css', 'xml', 'toml', 'rst', 'sh',
]);

function isHtmlPreview(file: SharedFile): boolean {
  const ext = file.name.split('.').pop()?.toLowerCase() || '';
  const mime = (file.mime || '').toLowerCase();
  return ext === 'html' || ext === 'htm' || mime === 'text/html';
}

function fileUrl(token: string, id: string, download = false): string {
  const query = new URLSearchParams({ token });
  if (download) query.set('download', '1');
  return `/api/files/${encodeURIComponent(id)}?${query.toString()}`;
}

function formatSize(bytes: number): string {
  if (!bytes) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function previewKind(file: SharedFile): PreviewKind {
  const ext = file.name.split('.').pop()?.toLowerCase() || '';
  const mime = (file.mime || '').toLowerCase();
  if (isHtmlPreview(file)) return 'html';
  if (ext === 'svg' || mime.includes('svg')) return 'html';
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'].includes(ext) || mime.startsWith('image/')) return 'image';
  if (ext === 'pdf' || mime === 'application/pdf') return 'pdf';
  if (mime.startsWith('text/') || mime.includes('json') || TEXT_EXT.has(ext)) return 'text';
  return 'binary';
}

function isFileReadable(file: SharedFile): boolean {
  if (file.missing) return false;
  const kind = previewKind(file);
  return kind === 'text' || kind === 'html' || kind === 'pdf';
}

function extractSpeechFromHtml(html: string): string {
  try {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    doc.querySelectorAll('script, style, noscript, svg, nav, footer, header, [aria-hidden="true"]').forEach((el) => el.remove());
    const body = doc.body;
    if (!body) return '';
    const raw = body.innerText || body.textContent || '';
    return raw.replace(/\r\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  } catch {
    return html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
  }
}

const MAX_SPEECH_CHARS = 29000;

async function getFileSpeechText(file: SharedFile, token: string, existingText?: string): Promise<string> {
  const kind = previewKind(file);
  let rawText = existingText || '';

  if (!rawText) {
    const isPdf = kind === 'pdf';
    const base = fileUrl(token, file.id, false);
    const url = isPdf ? `${base}&text=1` : base;
    const res = await fetch(url);
    if (!res.ok) {
      throw new Error(`Could not fetch file for speech (${res.status})`);
    }
    rawText = await res.text();
  }

  let speechText = '';
  const ext = file.name.split('.').pop()?.toLowerCase() || '';

  if (kind === 'html') {
    speechText = extractSpeechFromHtml(rawText);
  } else if (kind === 'pdf') {
    speechText = rawText.replace(/\x0c/g, '\n').replace(/\r\n/g, '\n').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
  } else if (file.name.toLowerCase().endsWith('.md') || (file.mime || '').includes('markdown')) {
    speechText = markdownToPlainText(rawText);
  } else if (['py', 'ts', 'tsx', 'js', 'jsx', 'sh', 'sql', 'css', 'json', 'yaml', 'yml'].includes(ext)) {
    speechText = codeToSpeech(rawText, ext);
  } else {
    speechText = rawText.replace(/\r\n/g, '\n').trim();
  }

  if (!speechText.trim()) {
    speechText = `${file.name}. This file has no readable text content.`;
  } else {
    speechText = `${file.name}.\n\n${speechText}`;
  }

  if (speechText.length > MAX_SPEECH_CHARS) {
    speechText = `${speechText.slice(0, MAX_SPEECH_CHARS).replace(/\s+\S*$/, '').trim()}. Remaining content omitted.`;
  }

  return speechText;
}

export function SharedFiles({ files, token, readAloud }: SharedFilesProps) {
  const [openFile, setOpenFile] = useState<SharedFile | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [loadingFileId, setLoadingFileId] = useState<string | null>(null);

  const activePlayingId = readAloud?.playingMessageId || '';
  const isPlayingAny = Boolean(readAloud?.playbackActive);
  const isBufferingAny = Boolean(readAloud?.isBuffering);

  const isReadingFile = (fileId: string) => activePlayingId === `file:${fileId}` && isPlayingAny;
  const isBufferingFile = (fileId: string) =>
    loadingFileId === fileId || (activePlayingId === `file:${fileId}` && isBufferingAny);

  const handleToggleReadFile = async (file: SharedFile) => {
    if (!readAloud?.onPlayAudio) return;
    const playbackId = `file:${file.id}`;
    if (activePlayingId === playbackId && (isPlayingAny || isBufferingAny)) {
      readAloud.onStopAudio?.();
      return;
    }
    setLoadingFileId(file.id);
    try {
      const speech = await getFileSpeechText(file, token);
      readAloud.onPlayAudio(speech, playbackId);
    } catch (err) {
      console.error('Failed to read file aloud:', err);
    } finally {
      setLoadingFileId(null);
    }
  };

  if (!files.length && !openFile) return null;

  return (
    <>
      {files.length > 0 && (
        <div className="mx-3 mt-2 rounded-xl border border-[#44475a] bg-[#282a36] overflow-hidden">
          <button
            type="button"
            onClick={() => setCollapsed((value) => !value)}
            aria-expanded={!collapsed}
            aria-controls="shared-files-list"
            title={collapsed ? 'Expand shared files' : 'Collapse shared files'}
            className={`w-full flex items-center justify-between gap-2 px-3 py-1.5 text-left hover:bg-[#44475a]/30 transition ${
              collapsed ? '' : 'border-b border-[#44475a]/60'
            }`}
          >
            <span className="text-[11px] font-bold text-[#bd93f9]">
              Shared files ({files.length})
            </span>
            {collapsed ? (
              <ChevronRight className="w-3.5 h-3.5 text-[#bd93f9] shrink-0" />
            ) : (
              <ChevronDown className="w-3.5 h-3.5 text-[#bd93f9] shrink-0" />
            )}
          </button>
          {!collapsed && (
            <div id="shared-files-list" className="max-h-36 overflow-y-auto">
              {files.map((file) => (
                <div
                  key={file.id}
                  className="flex items-center gap-2 px-3 py-2 border-b border-[#44475a]/30 last:border-b-0"
                >
                  <FileText className="w-3.5 h-3.5 text-[#8be9fd] shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div className="text-xs text-[#f8f8f2] truncate">{file.name}</div>
                    <div className="text-[10px] text-[#6272a4] truncate">
                      {file.direction === 'user' ? 'You sent' : 'From Eidos'}
                      {' · '}
                      {formatSize(file.size)}
                      {file.missing ? ' · missing on disk' : ''}
                      {file.note ? ` · ${file.note}` : ''}
                    </div>
                  </div>
                  {readAloud?.onPlayAudio && isFileReadable(file) && (
                    <button
                      type="button"
                      onClick={() => void handleToggleReadFile(file)}
                      title={
                        isReadingFile(file.id)
                          ? 'Stop reading'
                          : isBufferingFile(file.id)
                          ? 'Preparing audio…'
                          : 'Read aloud with Kokoro TTS'
                      }
                      aria-label={
                        isReadingFile(file.id)
                          ? 'Stop reading'
                          : isBufferingFile(file.id)
                          ? 'Preparing audio'
                          : 'Read aloud with Kokoro TTS'
                      }
                      className={`p-1.5 rounded-lg transition active:scale-95 ${
                        isBufferingFile(file.id)
                          ? 'text-[#bd93f9] bg-[#bd93f9]/10 animate-pulse'
                          : isReadingFile(file.id)
                          ? 'text-[#ff5555] bg-[#ff5555]/20 hover:bg-[#ff5555]/30'
                          : 'text-[#bd93f9] hover:bg-[#bd93f9]/10'
                      }`}
                    >
                      {isBufferingFile(file.id) ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : isReadingFile(file.id) ? (
                        <Square className="w-3.5 h-3.5 fill-current" />
                      ) : (
                        <Volume2 className="w-3.5 h-3.5" />
                      )}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => setOpenFile(file)}
                    disabled={file.missing}
                    title="View"
                    className="p-1.5 rounded-lg text-[#50fa7b] hover:bg-[#50fa7b]/10 disabled:opacity-30 disabled:cursor-not-allowed"
                  >
                    <Eye className="w-3.5 h-3.5" />
                  </button>
                  <a
                    href={file.missing ? undefined : fileUrl(token, file.id, true)}
                    download={file.name}
                    title="Download"
                    className={`p-1.5 rounded-lg text-[#8be9fd] hover:bg-[#8be9fd]/10 ${file.missing ? 'pointer-events-none opacity-30' : ''}`}
                  >
                    <Download className="w-3.5 h-3.5" />
                  </a>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {openFile && (
        <FileViewer
          file={openFile}
          token={token}
          readAloud={readAloud}
          onClose={() => setOpenFile(null)}
        />
      )}
    </>
  );
}

function FileViewer({
  file,
  token,
  readAloud,
  onClose,
}: {
  file: SharedFile;
  token: string;
  readAloud?: Omit<ReadAloudControl, 'playbackPrefix'>;
  onClose: () => void;
}) {
  const kind = previewKind(file);
  const [text, setText] = useState('');
  const [showHtmlSource, setShowHtmlSource] = useState(false);
  const [loading, setLoading] = useState(kind === 'text' || (kind === 'html' && showHtmlSource));
  const [error, setError] = useState<string | null>(file.missing ? 'This file is no longer on disk.' : null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isExtractingSpeech, setIsExtractingSpeech] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isPinching, setIsPinching] = useState(false);
  const [interactMode, setInteractMode] = useState(false);

  const zoomRef = useRef(1);
  zoomRef.current = zoom;
  const panRef = useRef({ x: 0, y: 0 });
  panRef.current = pan;

  const containerRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const url = fileUrl(token, file.id, false);
  const isMarkdown = file.name.toLowerCase().endsWith('.md') || (file.mime || '').includes('markdown');
  const htmlPreview = kind === 'html';

  const MIN_ZOOM = 1;
  const MAX_ZOOM = 5;

  const setZoomAround = useCallback((nextZoom: number, focusClientX?: number, focusClientY?: number) => {
    const stage = stageRef.current;
    if (!stage) return;
    const rect = stage.getBoundingClientRect();
    const clampedZoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextZoom));

    if (clampedZoom <= 1.01) {
      zoomRef.current = 1;
      panRef.current = { x: 0, y: 0 };
      setZoom(1);
      setPan({ x: 0, y: 0 });
      return;
    }

    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const fx = (focusClientX !== undefined ? focusClientX : cx) - cx;
    const fy = (focusClientY !== undefined ? focusClientY : cy) - cy;

    const prevZoom = zoomRef.current;
    const prevPan = panRef.current;

    const contentX = (fx - prevPan.x) / prevZoom;
    const contentY = (fy - prevPan.y) / prevZoom;

    const newPanX = fx - contentX * clampedZoom;
    const newPanY = fy - contentY * clampedZoom;

    const contentH = contentRef.current ? Math.max(rect.height, contentRef.current.scrollHeight) : rect.height;
    const contentW = contentRef.current ? Math.max(rect.width, contentRef.current.scrollWidth) : rect.width;

    const maxPanX = Math.max(0, (contentW * clampedZoom - rect.width) / 2);
    const maxPanY = Math.max(0, (contentH * clampedZoom - rect.height) / 2);

    const clampedPanX = Math.min(maxPanX, Math.max(-maxPanX, newPanX));
    const clampedPanY = Math.min(maxPanY, Math.max(-maxPanY, newPanY));

    zoomRef.current = clampedZoom;
    panRef.current = { x: clampedPanX, y: clampedPanY };
    setZoom(clampedZoom);
    setPan({ x: clampedPanX, y: clampedPanY });
  }, []);

  const resetZoom = useCallback(() => {
    zoomRef.current = 1;
    panRef.current = { x: 0, y: 0 };
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, []);

  useEffect(() => {
    if (!isFullscreen) {
      resetZoom();
      setInteractMode(false);
    }
  }, [isFullscreen, resetZoom]);

  useEffect(() => {
    resetZoom();
    setInteractMode(false);
  }, [file.id, resetZoom]);

  const filePlaybackId = `file:${file.id}`;
  const isThisFileActive = (readAloud?.playingMessageId || '') === filePlaybackId;
  const isReadingThisFile = isThisFileActive && Boolean(readAloud?.playbackActive);
  const isBufferingThisFile = isExtractingSpeech || (isThisFileActive && Boolean(readAloud?.isBuffering));
  const canReadThisFile = Boolean(readAloud?.onPlayAudio && isFileReadable(file));

  const handleToggleReadAloud = async () => {
    if (!readAloud?.onPlayAudio) return;
    if (isReadingThisFile || isBufferingThisFile) {
      readAloud.onStopAudio?.();
      return;
    }
    setIsExtractingSpeech(true);
    try {
      const speech = await getFileSpeechText(file, token, text || undefined);
      readAloud.onPlayAudio(speech, filePlaybackId);
    } catch (err) {
      console.error('Failed to read file aloud:', err);
    } finally {
      setIsExtractingSpeech(false);
    }
  };

  const enterFullscreen = useCallback(async () => {
    setIsFullscreen(true);
    const el = containerRef.current;
    if (el && document.fullscreenEnabled && !document.fullscreenElement) {
      try {
        if (el.requestFullscreen) {
          await el.requestFullscreen();
        } else {
          const webkit = (el as HTMLElement & { webkitRequestFullscreen?: () => Promise<void> | void }).webkitRequestFullscreen;
          await webkit?.call(el);
        }
      } catch {
        // Native fullscreen was denied or unsupported; CSS fullscreen still active
      }
    }
  }, []);

  const exitFullscreen = useCallback(async () => {
    setIsFullscreen(false);
    if (document.fullscreenElement) {
      try {
        if (document.exitFullscreen) {
          await document.exitFullscreen();
        } else {
          const webkit = (document as Document & { webkitExitFullscreen?: () => Promise<void> | void }).webkitExitFullscreen;
          await webkit?.call(document);
        }
      } catch {
        // Ignore error
      }
    }
  }, []);

  const toggleFullscreen = useCallback(() => {
    if (isFullscreen) {
      void exitFullscreen();
    } else {
      void enterFullscreen();
    }
  }, [isFullscreen, enterFullscreen, exitFullscreen]);

  const handleClose = useCallback(() => {
    if (document.fullscreenElement) {
      try {
        document.exitFullscreen().catch(() => undefined);
      } catch {
        // Ignore error
      }
    }
    onClose();
  }, [onClose]);

  useEffect(() => {
    const handleFullscreenChange = () => {
      const isNativeFs = Boolean(document.fullscreenElement);
      if (!isNativeFs && isFullscreen) {
        setIsFullscreen(false);
      }
    };
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    document.addEventListener('webkitfullscreenchange', handleFullscreenChange);
    return () => {
      document.removeEventListener('fullscreenchange', handleFullscreenChange);
      document.removeEventListener('webkitfullscreenchange', handleFullscreenChange);
    };
  }, [isFullscreen]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (isFullscreen) {
          void exitFullscreen();
        } else {
          handleClose();
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [isFullscreen, exitFullscreen, handleClose]);

  useEffect(() => {
    return () => {
      if (document.fullscreenElement) {
        document.exitFullscreen().catch(() => undefined);
      }
    };
  }, []);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage || !isFullscreen) return;

    let pinch: {
      dist: number;
      zoom: number;
      panX: number;
      panY: number;
      midX: number;
      midY: number;
    } | null = null;

    let drag: {
      startX: number;
      startY: number;
      panX: number;
      panY: number;
    } | null = null;

    let lastTapTime = 0;
    let lastTapX = 0;
    let lastTapY = 0;

    const onTouchStart = (e: TouchEvent) => {
      if ((e.target as HTMLElement).closest('button, a, input, select')) return;

      if (e.touches.length === 2) {
        drag = null;
        setIsPinching(true);
        const rect = stage.getBoundingClientRect();
        const t1 = e.touches[0];
        const t2 = e.touches[1];
        const dist = Math.hypot(t1.clientX - t2.clientX, t1.clientY - t2.clientY);
        const midClientX = (t1.clientX + t2.clientX) / 2;
        const midClientY = (t1.clientY + t2.clientY) / 2;
        const midX = midClientX - (rect.left + rect.width / 2);
        const midY = midClientY - (rect.top + rect.height / 2);

        pinch = {
          dist: Math.max(dist, 10),
          zoom: zoomRef.current,
          panX: panRef.current.x,
          panY: panRef.current.y,
          midX,
          midY,
        };
      } else if (e.touches.length === 1 && zoomRef.current > 1.01) {
        pinch = null;
        setIsPinching(true);
        drag = {
          startX: e.touches[0].clientX,
          startY: e.touches[0].clientY,
          panX: panRef.current.x,
          panY: panRef.current.y,
        };
      }
    };

    const onTouchMove = (e: TouchEvent) => {
      if (e.touches.length === 2 && pinch) {
        e.preventDefault();
        const rect = stage.getBoundingClientRect();
        const t1 = e.touches[0];
        const t2 = e.touches[1];
        const currentDist = Math.hypot(t1.clientX - t2.clientX, t1.clientY - t2.clientY);
        const currentMidClientX = (t1.clientX + t2.clientX) / 2;
        const currentMidClientY = (t1.clientY + t2.clientY) / 2;
        const currentMidX = currentMidClientX - (rect.left + rect.width / 2);
        const currentMidY = currentMidClientY - (rect.top + rect.height / 2);

        const scaleFactor = currentDist / pinch.dist;
        const newZoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, pinch.zoom * scaleFactor));

        if (newZoom <= 1.01) {
          zoomRef.current = 1;
          panRef.current = { x: 0, y: 0 };
          setZoom(1);
          setPan({ x: 0, y: 0 });
          return;
        }

        const contentX = (pinch.midX - pinch.panX) / pinch.zoom;
        const contentY = (pinch.midY - pinch.panY) / pinch.zoom;

        const rawPanX = currentMidX - contentX * newZoom;
        const rawPanY = currentMidY - contentY * newZoom;

        const contentH = contentRef.current ? Math.max(rect.height, contentRef.current.scrollHeight) : rect.height;
        const contentW = contentRef.current ? Math.max(rect.width, contentRef.current.scrollWidth) : rect.width;

        const maxPanX = Math.max(0, (contentW * newZoom - rect.width) / 2);
        const maxPanY = Math.max(0, (contentH * newZoom - rect.height) / 2);

        const clampedPanX = Math.min(maxPanX, Math.max(-maxPanX, rawPanX));
        const clampedPanY = Math.min(maxPanY, Math.max(-maxPanY, rawPanY));

        zoomRef.current = newZoom;
        panRef.current = { x: clampedPanX, y: clampedPanY };
        setZoom(newZoom);
        setPan({ x: clampedPanX, y: clampedPanY });
      } else if (e.touches.length === 1 && drag && zoomRef.current > 1.01) {
        e.preventDefault();
        const rect = stage.getBoundingClientRect();
        const dx = e.touches[0].clientX - drag.startX;
        const dy = e.touches[0].clientY - drag.startY;

        const contentH = contentRef.current ? Math.max(rect.height, contentRef.current.scrollHeight) : rect.height;
        const contentW = contentRef.current ? Math.max(rect.width, contentRef.current.scrollWidth) : rect.width;

        const maxPanX = Math.max(0, (contentW * zoomRef.current - rect.width) / 2);
        const maxPanY = Math.max(0, (contentH * zoomRef.current - rect.height) / 2);

        const rawPanX = drag.panX + dx;
        const rawPanY = drag.panY + dy;

        const clampedPanX = Math.min(maxPanX, Math.max(-maxPanX, rawPanX));
        const clampedPanY = Math.min(maxPanY, Math.max(-maxPanY, rawPanY));

        panRef.current = { x: clampedPanX, y: clampedPanY };
        setPan({ x: clampedPanX, y: clampedPanY });
      }
    };

    const onTouchEnd = (e: TouchEvent) => {
      if (e.touches.length < 2) {
        pinch = null;
      }
      if (e.touches.length === 0) {
        drag = null;
        setIsPinching(false);

        if (e.changedTouches.length === 1) {
          const touch = e.changedTouches[0];
          const now = Date.now();
          const timeDiff = now - lastTapTime;
          const dist = Math.hypot(touch.clientX - lastTapX, touch.clientY - lastTapY);

          if (timeDiff < 300 && dist < 25) {
            e.preventDefault();
            if (zoomRef.current > 1.1) {
              resetZoom();
            } else {
              setZoomAround(2.5, touch.clientX, touch.clientY);
            }
            lastTapTime = 0;
            return;
          }
          lastTapTime = now;
          lastTapX = touch.clientX;
          lastTapY = touch.clientY;
        }
      }
    };

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const factor = Math.exp(-e.deltaY * 0.002);
      setZoomAround(zoomRef.current * factor, e.clientX, e.clientY);
    };

    const onPointerDown = (e: PointerEvent) => {
      if (e.pointerType !== 'mouse' || e.button !== 0) return;
      if ((e.target as HTMLElement).closest('button, a, input, select')) return;
      if (zoomRef.current > 1.01) {
        drag = {
          startX: e.clientX,
          startY: e.clientY,
          panX: panRef.current.x,
          panY: panRef.current.y,
        };
        setIsPinching(true);
      }
    };

    const onPointerMove = (e: PointerEvent) => {
      if (e.pointerType !== 'mouse' || !drag || zoomRef.current <= 1.01) return;
      const rect = stage.getBoundingClientRect();
      const dx = e.clientX - drag.startX;
      const dy = e.clientY - drag.startY;

      const contentH = contentRef.current ? Math.max(rect.height, contentRef.current.scrollHeight) : rect.height;
      const contentW = contentRef.current ? Math.max(rect.width, contentRef.current.scrollWidth) : rect.width;

      const maxPanX = Math.max(0, (contentW * zoomRef.current - rect.width) / 2);
      const maxPanY = Math.max(0, (contentH * zoomRef.current - rect.height) / 2);

      const rawPanX = drag.panX + dx;
      const rawPanY = drag.panY + dy;

      const clampedPanX = Math.min(maxPanX, Math.max(-maxPanX, rawPanX));
      const clampedPanY = Math.min(maxPanY, Math.max(-maxPanY, rawPanY));

      panRef.current = { x: clampedPanX, y: clampedPanY };
      setPan({ x: clampedPanX, y: clampedPanY });
    };

    const onPointerUp = (e: PointerEvent) => {
      if (e.pointerType === 'mouse') {
        drag = null;
        setIsPinching(false);
      }
    };

    stage.addEventListener('touchstart', onTouchStart, { passive: true });
    stage.addEventListener('touchmove', onTouchMove, { passive: false });
    stage.addEventListener('touchend', onTouchEnd, { passive: false });
    stage.addEventListener('touchcancel', onTouchEnd, { passive: false });
    stage.addEventListener('wheel', onWheel, { passive: false });
    stage.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);

    return () => {
      stage.removeEventListener('touchstart', onTouchStart);
      stage.removeEventListener('touchmove', onTouchMove);
      stage.removeEventListener('touchend', onTouchEnd);
      stage.removeEventListener('touchcancel', onTouchEnd);
      stage.removeEventListener('wheel', onWheel);
      stage.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
    };
  }, [isFullscreen, setZoomAround, resetZoom]);

  useEffect(() => {
    const needsText = kind === 'text' || (kind === 'html' && showHtmlSource);
    if (!needsText || file.missing) return;
    if (file.size > 1_500_000) {
      setLoading(false);
      setError('This file is too large to preview here. Download it instead.');
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(url)
      .then(async (res) => {
        if (!res.ok) throw new Error(`Could not open file (${res.status})`);
        return res.text();
      })
      .then((body) => {
        if (!cancelled) setText(body);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message || 'Could not open file');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [file.id, file.missing, file.size, kind, showHtmlSource, url]);

  useEffect(() => {
    setShowHtmlSource(false);
    setText('');
  }, [file.id]);

  return (
    <div
      ref={containerRef}
      className={`fixed inset-0 z-50 ${
        isFullscreen
          ? 'bg-[#1e1f29] flex flex-col p-0'
          : 'bg-black/70 flex items-end sm:items-center justify-center p-3'
      }`}
      onClick={(e) => {
        if (!isFullscreen && e.target === e.currentTarget) {
          handleClose();
        }
      }}
    >
      <div
        className={`w-full bg-[#1e1f29] flex flex-col overflow-hidden ${
          isFullscreen
            ? 'h-full flex-1 rounded-none border-0 shadow-none'
            : `max-h-[85dvh] border border-[#44475a] rounded-2xl shadow-2xl ${
                htmlPreview ? 'max-w-5xl' : 'max-w-2xl'
              }`
        }`}
      >
        <div className="flex items-center gap-2 px-3 py-2 border-b border-[#44475a] shrink-0">
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-[#f8f8f2] truncate">{file.name}</div>
            <div className="text-[10px] text-[#6272a4] truncate">
              {formatSize(file.size)}
              {file.path ? ` · ${file.path}` : ''}
            </div>
          </div>
          {htmlPreview && !file.missing && (
            <div className="flex rounded-lg border border-[#44475a] overflow-hidden shrink-0">
              <button
                type="button"
                onClick={() => setShowHtmlSource(false)}
                className={`px-2 py-1 text-[10px] font-semibold ${
                  !showHtmlSource ? 'bg-[#44475a] text-[#f8f8f2]' : 'text-[#6272a4] hover:text-[#f8f8f2]'
                }`}
              >
                Live
              </button>
              <button
                type="button"
                onClick={() => setShowHtmlSource(true)}
                className={`px-2 py-1 text-[10px] font-semibold ${
                  showHtmlSource ? 'bg-[#44475a] text-[#f8f8f2]' : 'text-[#6272a4] hover:text-[#f8f8f2]'
                }`}
              >
                Source
              </button>
            </div>
          )}
          {!file.missing && htmlPreview && (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="px-2.5 py-1.5 rounded-lg text-[11px] font-semibold text-[#bd93f9] border border-[#44475a] hover:border-[#bd93f9] hidden sm:inline shrink-0"
            >
              New tab
            </a>
          )}
          {canReadThisFile && (
            <button
              type="button"
              onClick={() => void handleToggleReadAloud()}
              title={
                isReadingThisFile
                  ? 'Stop reading'
                  : isBufferingThisFile
                  ? 'Preparing to read this file aloud'
                  : 'Read this file aloud with Kokoro TTS'
              }
              aria-label={
                isReadingThisFile
                  ? 'Stop reading'
                  : isBufferingThisFile
                  ? 'Preparing to read this file aloud'
                  : 'Read this file aloud'
              }
              className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-semibold transition shrink-0 active:scale-95 ${
                isBufferingThisFile
                  ? 'bg-[#bd93f9]/20 text-[#bd93f9] border border-[#bd93f9]/50 animate-pulse'
                  : isReadingThisFile
                  ? 'bg-[#ff5555]/20 text-[#ff5555] border border-[#ff5555]/50 hover:bg-[#ff5555]/30'
                  : 'text-[#bd93f9] border border-[#44475a] hover:border-[#bd93f9] hover:bg-[#bd93f9]/10'
              }`}
            >
              {isBufferingThisFile ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : isReadingThisFile ? (
                <Square className="w-3.5 h-3.5 fill-current" />
              ) : (
                <Volume2 className="w-3.5 h-3.5" />
              )}
              <span className="hidden sm:inline">
                {isBufferingThisFile ? 'Preparing…' : isReadingThisFile ? 'Stop' : 'Read aloud'}
              </span>
            </button>
          )}
          <a
            href={file.missing ? undefined : fileUrl(token, file.id, true)}
            download={file.name}
            className={`px-2.5 py-1.5 rounded-lg text-[11px] font-semibold text-[#8be9fd] border border-[#44475a] hover:border-[#8be9fd] shrink-0 ${file.missing ? 'pointer-events-none opacity-30' : ''}`}
          >
            Download
          </a>
          <button
            type="button"
            onClick={toggleFullscreen}
            title={isFullscreen ? 'Exit full screen' : 'Full screen'}
            aria-label={isFullscreen ? 'Exit full screen' : 'Full screen'}
            className="p-1.5 rounded-lg text-[#6272a4] hover:text-[#f8f8f2] hover:bg-[#44475a]/40 transition shrink-0"
          >
            {isFullscreen ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
          </button>
          <button
            type="button"
            onClick={handleClose}
            title="Close"
            className="p-1.5 rounded-lg text-[#6272a4] hover:text-[#f8f8f2] hover:bg-[#44475a]/40 shrink-0"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div
          ref={stageRef}
          data-eidos-zoomable
          className={`flex-1 relative overflow-hidden select-text ${
            isFullscreen ? 'touch-none' : 'overflow-auto p-3'
          } ${
            isFullscreen && zoom > 1.01 ? 'cursor-grab active:cursor-grabbing' : ''
          }`}
        >
          <div
            ref={contentRef}
            style={
              isFullscreen
                ? {
                    transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
                    transformOrigin: 'center center',
                    transition: isPinching ? 'none' : 'transform 0.2s cubic-bezier(0.2, 0, 0.2, 1)',
                  }
                : undefined
            }
            className={`w-full h-full flex flex-col ${
              isFullscreen
                ? `p-3 ${
                    kind === 'pdf' || (kind === 'html' && !showHtmlSource) || kind === 'image'
                      ? 'flex-1 min-h-0'
                      : 'overflow-auto'
                  }`
                : ''
            }`}
          >
            {loading && (
              <div className="flex items-center gap-2 text-xs text-[#6272a4] py-8 justify-center">
                <Loader2 className="w-4 h-4 animate-spin" />
                Opening…
              </div>
            )}
            {error && (
              <div className="text-xs text-[#ff5555] bg-[#ff5555]/10 border border-[#ff5555]/30 rounded-lg p-3">
                {error}
              </div>
            )}
            {!loading && !error && kind === 'image' && (
              <img
                src={url}
                alt={file.name}
                draggable={false}
                className={`max-w-full mx-auto rounded-lg select-none ${
                  isFullscreen ? 'max-h-full object-contain my-auto' : ''
                }`}
              />
            )}
            {!loading && !error && kind === 'pdf' && (
              <div className="relative w-full flex-1 min-h-0 h-full flex flex-col">
                <iframe
                  title={file.name}
                  src={url}
                  className={`w-full rounded-lg bg-white border-0 ${
                    isFullscreen ? 'flex-1 min-h-0 h-full' : 'h-[70dvh]'
                  }`}
                />
                {isFullscreen && (
                  <div
                    className="absolute inset-0 z-10"
                    title="Pinch to zoom, drag to pan"
                  />
                )}
              </div>
            )}
            {!loading && !error && kind === 'html' && !showHtmlSource && (
              <div className="relative w-full flex-1 min-h-0 h-full flex flex-col">
                <iframe
                  title={file.name}
                  src={url}
                  sandbox="allow-scripts allow-forms allow-popups allow-modals"
                  className={`w-full rounded-lg bg-white border-0 ${
                    isFullscreen ? 'flex-1 min-h-0 h-full' : 'h-[70dvh]'
                  }`}
                />
                {isFullscreen && !interactMode && (
                  <div
                    className="absolute inset-0 z-10"
                    title="Pinch to zoom, drag to pan (tap 'Interact' in controls to click inside)"
                  />
                )}
              </div>
            )}
            {!loading && !error && kind === 'html' && showHtmlSource && (
              <pre className="text-[11px] leading-relaxed whitespace-pre-wrap break-words text-[#f8f8f2] font-mono">
                {text}
              </pre>
            )}
            {!loading && !error && kind === 'text' && isMarkdown && (
              <MarkdownRenderer
                content={text}
                playbackPrefix={`file:${file.id}`}
                {...readAloud}
              />
            )}
            {!loading && !error && kind === 'text' && !isMarkdown && (
              <pre className="text-[11px] leading-relaxed whitespace-pre-wrap break-words text-[#f8f8f2] font-mono">
                {text}
              </pre>
            )}
            {!loading && !error && kind === 'binary' && (
              <div className="text-xs text-[#f8f8f2] leading-relaxed">
                This file does not preview in the browser. Download it to open it on this device.
              </div>
            )}
          </div>

          {isFullscreen && (
            <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-30 shadow-2xl backdrop-blur-md bg-[#1e1f29]/95 border border-[#44475a] rounded-xl flex items-center px-1.5 py-1 gap-1 text-[#f8f8f2] select-none">
              <button
                type="button"
                onClick={() => setZoomAround(zoomRef.current / 1.3)}
                disabled={zoom <= 1.01}
                title="Zoom out"
                className="p-1 rounded-lg text-[#6272a4] hover:text-[#f8f8f2] hover:bg-[#44475a]/50 disabled:opacity-30 disabled:hover:bg-transparent transition"
              >
                <ZoomOut className="w-3.5 h-3.5" />
              </button>
              <button
                type="button"
                onClick={resetZoom}
                title="Reset zoom to 100%"
                className="px-2 py-0.5 font-mono text-xs font-semibold text-[#bd93f9] hover:bg-[#44475a]/50 rounded-lg transition"
              >
                {Math.round(zoom * 100)}%
              </button>
              <button
                type="button"
                onClick={() => setZoomAround(zoomRef.current * 1.3)}
                disabled={zoom >= 4.99}
                title="Zoom in"
                className="p-1 rounded-lg text-[#6272a4] hover:text-[#f8f8f2] hover:bg-[#44475a]/50 disabled:opacity-30 disabled:hover:bg-transparent transition"
              >
                <ZoomIn className="w-3.5 h-3.5" />
              </button>
              {zoom > 1.01 && (
                <button
                  type="button"
                  onClick={resetZoom}
                  title="Reset zoom"
                  className="flex items-center gap-1 pl-1.5 pr-2 py-0.5 text-[11px] font-semibold text-[#8be9fd] hover:bg-[#44475a]/50 rounded-lg transition border-l border-[#44475a]/60"
                >
                  <RotateCcw className="w-3 h-3" />
                  <span>Reset</span>
                </button>
              )}
              {kind === 'html' && !showHtmlSource && (
                <button
                  type="button"
                  onClick={() => setInteractMode(!interactMode)}
                  title={interactMode ? 'Switch to Pan & Zoom' : 'Switch to Click / Interact'}
                  className={`px-2 py-0.5 text-[10px] font-semibold rounded-lg transition border-l border-[#44475a]/60 ${
                    interactMode
                      ? 'bg-[#50fa7b] text-[#1e1f29] font-bold'
                      : 'text-[#6272a4] hover:text-[#f8f8f2] hover:bg-[#44475a]/50'
                  }`}
                >
                  {interactMode ? 'Interacting' : 'Zoom mode'}
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
