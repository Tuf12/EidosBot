import React, { useEffect, useRef, useState } from 'react';
import {
  Keyboard,
  Maximize2,
  Monitor,
  MousePointer,
  Pause,
  Play,
  RefreshCw,
  RotateCw,
  X,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';

interface ScreenViewportProps {
  token: string;
  /** When false the screen tab is mounted but hidden, so refresh and fullscreen pause. */
  isActive?: boolean;
}

function lockLandscape() {
  const orientation = screen.orientation as ScreenOrientation & {
    lock?: (orientation: 'landscape') => Promise<void>;
  };
  if (!orientation?.lock) return Promise.resolve();
  return orientation.lock('landscape').catch(() => {
    // Phones that stay portrait still get a sideways picture.
  });
}

const MIN_ZOOM = 1;
const MAX_ZOOM = 6;

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function touchDistance(touches: TouchList) {
  const first = touches[0];
  const second = touches[1];
  return Math.hypot(first.clientX - second.clientX, first.clientY - second.clientY);
}

/** Size the screenshot so it fits the stage. A quarter turn uses the phone's long side. */
function fitLayout(stageW: number, stageH: number, aspect: number, quarterTurn: boolean) {
  if (stageW < 8 || stageH < 8 || !Number.isFinite(aspect) || aspect <= 0) {
    return { lw: 0, lh: 0 };
  }
  if (!quarterTurn) {
    const lw = Math.min(stageW, stageH * aspect);
    return { lw, lh: lw / aspect };
  }
  const lh = Math.min(stageW, stageH / aspect);
  return { lw: lh * aspect, lh };
}

/** Map a point relative to the stage center back into untransformed image pixels. */
function screenToLocal(
  mx: number,
  my: number,
  panX: number,
  panY: number,
  zoom: number,
  quarterTurn: boolean,
) {
  let x = mx - panX;
  let y = my - panY;
  if (quarterTurn) {
    const rotatedX = y;
    const rotatedY = -x;
    x = rotatedX;
    y = rotatedY;
  }
  return { x: x / zoom, y: y / zoom };
}

function panLimits(
  zoom: number,
  view: {
    lw: number;
    lh: number;
    stageW: number;
    stageH: number;
    quarterTurn: boolean;
    obstructionBottom?: number;
  },
) {
  const visualW = (view.quarterTurn ? view.lh : view.lw) * zoom;
  const visualH = (view.quarterTurn ? view.lw : view.lh) * zoom;
  const obstruction = Math.max(0, view.obstructionBottom || 0);
  // While the phone keyboard covers the picture, allow enough travel that any
  // part of the desktop can be pulled into the strip that is still visible.
  if (obstruction > 8) {
    const halfW = visualW / 2;
    const halfH = visualH / 2;
    return {
      minX: -view.stageW / 2 - halfW,
      maxX: view.stageW / 2 + halfW,
      minY: -view.stageH / 2 - halfH,
      maxY: view.stageH / 2 - obstruction + halfH,
    };
  }
  const maxX = Math.max(0, (visualW - view.stageW) / 2);
  const maxY = Math.max(0, (visualH - view.stageH) / 2);
  return { minX: -maxX, maxX, minY: -maxY, maxY };
}

async function requestStageFullscreen(el: HTMLElement) {
  if (el.requestFullscreen) {
    await el.requestFullscreen();
    return;
  }
  const webkit = (el as HTMLElement & { webkitRequestFullscreen?: () => void }).webkitRequestFullscreen;
  webkit?.call(el);
}

export const ScreenViewport: React.FC<ScreenViewportProps> = ({ token, isActive = true }) => {
  const [timestamp, setTimestamp] = useState<number>(Date.now());
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [refreshIntervalMs, setRefreshIntervalMs] = useState(2000);
  const [interactiveMode, setInteractiveMode] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [orientReady, setOrientReady] = useState(false);
  const [rotateOverride, setRotateOverride] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(false);
  const [clickRipple, setClickRipple] = useState<{ x: number; y: number } | null>(null);
  const [natural, setNatural] = useState({ w: 16, h: 9 });
  const [stageSize, setStageSize] = useState({ w: 0, h: 0 });
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [seen, setSeen] = useState(isActive);
  const [typing, setTyping] = useState(false);
  const [keyboardLift, setKeyboardLift] = useState(0);
  const [coveredHeight, setCoveredHeight] = useState(0);
  const [typeError, setTypeError] = useState('');

  const stageRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<number | null>(null);
  const zoomRef = useRef(1);
  const panRef = useRef({ x: 0, y: 0 });
  const nativeFs = useRef(false);
  const suppressClick = useRef(false);
  const interactiveRef = useRef(interactiveMode);
  const fullscreenRef = useRef(fullscreen);
  const typeInputRef = useRef<HTMLInputElement>(null);
  const sentTextRef = useRef('');
  const typeQueueRef = useRef(Promise.resolve());
  const typingRef = useRef(false);
  const keepKeyboard = useRef(false);
  const skipClick = useRef(false);
  const typeRefreshRef = useRef(0);
  const typeBarRef = useRef<HTMLDivElement>(null);
  const lookAtRef = useRef<{ x: number; y: number; panX: number; panY: number } | null>(null);
  const userMovedPicture = useRef(false);
  interactiveRef.current = interactiveMode;
  fullscreenRef.current = fullscreen;
  typingRef.current = typing;

  const aspect = natural.w / natural.h;
  const autoTurn = fullscreen && orientReady && stageSize.h > stageSize.w + 8 && aspect > 1;
  const quarterTurn = rotateOverride ?? autoTurn;
  const fit = fitLayout(stageSize.w, stageSize.h, aspect, quarterTurn);
  const obstructionBottom = typing ? Math.max(coveredHeight, 220) : 0;

  const viewRef = useRef({
    lw: 0,
    lh: 0,
    stageW: 0,
    stageH: 0,
    quarterTurn: false,
    naturalW: 16,
    naturalH: 9,
    obstructionBottom: 0,
  });
  viewRef.current = {
    lw: fit.lw,
    lh: fit.lh,
    stageW: stageSize.w,
    stageH: stageSize.h,
    quarterTurn,
    naturalW: natural.w,
    naturalH: natural.h,
    obstructionBottom,
  };

  const commitView = (nextZoom: number, panX: number, panY: number) => {
    const z = clamp(nextZoom, MIN_ZOOM, MAX_ZOOM);
    const limits = panLimits(z, viewRef.current);
    const px = clamp(panX, limits.minX, limits.maxX);
    const py = clamp(panY, limits.minY, limits.maxY);
    zoomRef.current = z;
    panRef.current = { x: px, y: py };
    setZoom(z);
    setPan({ x: px, y: py });
  };

  const setZoomAround = (nextZoom: number, mx = 0, my = 0) => {
    const prev = zoomRef.current;
    const z = clamp(nextZoom, MIN_ZOOM, MAX_ZOOM);
    const local = screenToLocal(mx, my, panRef.current.x, panRef.current.y, prev, viewRef.current.quarterTurn);
    let x = local.x * z;
    let y = local.y * z;
    if (viewRef.current.quarterTurn) {
      const rotatedX = -y;
      const rotatedY = x;
      x = rotatedX;
      y = rotatedY;
    }
    commitView(z, mx - x, my - y);
  };

  const resetView = () => {
    commitView(1, 0, 0);
  };

  const refreshScreenshot = () => {
    setLoading(true);
    setTimestamp(Date.now());
  };

  const exitFullscreen = () => {
    nativeFs.current = false;
    try {
      screen.orientation?.unlock();
    } catch {
      // Unlock throws when this page never locked the orientation.
    }
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => undefined);
    }
    setOrientReady(false);
    setRotateOverride(null);
    setFullscreen(false);
    resetView();
  };

  const enterFullscreen = async () => {
    setFullscreen(true);
    setOrientReady(false);
    setRotateOverride(null);
    resetView();
    const stage = stageRef.current;
    if (!stage) {
      setOrientReady(true);
      return;
    }
    const fullscreenAttempt = requestStageFullscreen(stage)
      .then(() => {
        nativeFs.current = document.fullscreenElement === stage;
      })
      .catch(() => {
        nativeFs.current = false;
      });
    const orientationAttempt = lockLandscape();
    await Promise.all([fullscreenAttempt, orientationAttempt]);
    await new Promise<void>((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
    });
    setOrientReady(true);
  };

  useEffect(() => {
    const onChange = () => {
      if (!nativeFs.current) return;
      if (document.fullscreenElement !== stageRef.current) {
        nativeFs.current = false;
        try {
          screen.orientation?.unlock();
        } catch {
          // Already unlocked.
        }
        setOrientReady(false);
        setRotateOverride(null);
        setFullscreen(false);
        zoomRef.current = 1;
        panRef.current = { x: 0, y: 0 };
        setZoom(1);
        setPan({ x: 0, y: 0 });
      }
    };
    document.addEventListener('fullscreenchange', onChange);
    document.addEventListener('webkitfullscreenchange', onChange as EventListener);
    return () => {
      document.removeEventListener('fullscreenchange', onChange);
      document.removeEventListener('webkitfullscreenchange', onChange as EventListener);
    };
  }, []);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const measure = () => {
      const rect = stage.getBoundingClientRect();
      setStageSize({ w: rect.width, h: rect.height });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(stage);
    return () => observer.disconnect();
  }, [fullscreen]);

  useEffect(() => {
    if (!isActive) setTyping(false);
    if (!isActive && fullscreenRef.current) {
      nativeFs.current = false;
      try {
        screen.orientation?.unlock();
      } catch {
        // Already unlocked.
      }
      if (document.fullscreenElement) {
        document.exitFullscreen().catch(() => undefined);
      }
      setOrientReady(false);
      setRotateOverride(null);
      setFullscreen(false);
      zoomRef.current = 1;
      panRef.current = { x: 0, y: 0 };
      setZoom(1);
      setPan({ x: 0, y: 0 });
    }
  }, [isActive]);

  useEffect(() => {
    if (!typing) {
      setKeyboardLift(0);
      setCoveredHeight(0);
      userMovedPicture.current = false;
      typeInputRef.current?.blur();
      return;
    }
    const viewport = window.visualViewport;
    const place = () => {
      const keyboard = viewport
        ? Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop)
        : 0;
      setKeyboardLift(Math.round(keyboard));
      const stage = stageRef.current;
      const barHeight = typeBarRef.current?.getBoundingClientRect().height ?? 0;
      const visibleBottom = viewport ? viewport.offsetTop + viewport.height : window.innerHeight;
      const stageBottom = stage ? stage.getBoundingClientRect().bottom : visibleBottom;
      const covered = Math.max(0, stageBottom - visibleBottom) + barHeight;
      setCoveredHeight(Math.round(covered));
    };
    place();
    viewport?.addEventListener('resize', place);
    viewport?.addEventListener('scroll', place);
    const bar = typeBarRef.current;
    const observer = bar ? new ResizeObserver(place) : null;
    if (bar && observer) observer.observe(bar);
    return () => {
      viewport?.removeEventListener('resize', place);
      viewport?.removeEventListener('scroll', place);
      observer?.disconnect();
    };
  }, [typing]);

  useEffect(() => {
    if (!isActive) return;
    setSeen(true);
    setTimestamp(Date.now());
  }, [isActive]);

  useEffect(() => {
    if (autoRefresh && isActive) {
      timerRef.current = window.setInterval(() => {
        setTimestamp(Date.now());
      }, refreshIntervalMs);
    } else if (timerRef.current) {
      clearInterval(timerRef.current);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [autoRefresh, refreshIntervalMs, isActive]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;

    let pinch: { dist: number; zoom: number } | null = null;
    let drag: { x: number; y: number; panX: number; panY: number; moved: boolean } | null = null;
    let usingGesture = false;
    let gestureZoom = 1;

    const pointFromClient = (clientX: number, clientY: number) => {
      const rect = stage.getBoundingClientRect();
      return {
        x: clientX - (rect.left + rect.width / 2),
        y: clientY - (rect.top + rect.height / 2),
      };
    };

    const fromControl = (event: Event) => {
      const target = event.target;
      return target instanceof Element && Boolean(target.closest('button, select'));
    };

    const onTouchStart = (event: TouchEvent) => {
      if (fromControl(event)) return;
      if (event.touches.length === 2) {
        drag = null;
        pinch = { dist: touchDistance(event.touches), zoom: zoomRef.current };
      } else if (event.touches.length === 1) {
        pinch = null;
        drag = {
          x: event.touches[0].clientX,
          y: event.touches[0].clientY,
          panX: panRef.current.x,
          panY: panRef.current.y,
          moved: false,
        };
      }
    };

    const onTouchMove = (event: TouchEvent) => {
      if (usingGesture) return;
      if (event.touches.length === 2 && pinch && pinch.dist > 8) {
        suppressClick.current = true;
        event.preventDefault();
        const mid = pointFromClient(
          (event.touches[0].clientX + event.touches[1].clientX) / 2,
          (event.touches[0].clientY + event.touches[1].clientY) / 2,
        );
        setZoomAround(pinch.zoom * (touchDistance(event.touches) / pinch.dist), mid.x, mid.y);
        return;
      }
      const canDrag = zoomRef.current > 1.01 || typingRef.current;
      if (event.touches.length === 1 && drag && canDrag) {
        const dx = event.touches[0].clientX - drag.x;
        const dy = event.touches[0].clientY - drag.y;
        if (!drag.moved && Math.hypot(dx, dy) < 8) return;
        drag.moved = true;
        userMovedPicture.current = true;
        suppressClick.current = true;
        event.preventDefault();
        commitView(zoomRef.current, drag.panX + dx, drag.panY + dy);
      }
    };

    const onTouchEnd = (event: TouchEvent) => {
      if (event.touches.length < 2) pinch = null;
      if (event.touches.length === 0) drag = null;
    };

    const onGestureStart = (event: Event) => {
      event.preventDefault();
      usingGesture = true;
      pinch = null;
      gestureZoom = zoomRef.current;
    };

    const onGestureChange = (event: Event) => {
      event.preventDefault();
      suppressClick.current = true;
      const factor = (event as Event & { scale?: number }).scale;
      if (!factor || factor <= 0) return;
      setZoomAround(gestureZoom * factor);
    };

    const onGestureEnd = (event: Event) => {
      event.preventDefault();
      usingGesture = false;
    };

    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      event.stopPropagation();
      const mid = pointFromClient(event.clientX, event.clientY);
      const factor = Math.exp(-event.deltaY * 0.0015);
      setZoomAround(zoomRef.current * factor, mid.x, mid.y);
    };

    const onPointerDown = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse' || event.button !== 0) return;
      if ((event.target as HTMLElement).closest('button, select')) return;
      drag = {
        x: event.clientX,
        y: event.clientY,
        panX: panRef.current.x,
        panY: panRef.current.y,
        moved: false,
      };
    };

    const onPointerMove = (event: PointerEvent) => {
      const canDrag = zoomRef.current > 1.01 || typingRef.current;
      if (event.pointerType !== 'mouse' || !drag || !canDrag) return;
      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) < 4) return;
      drag.moved = true;
      userMovedPicture.current = true;
      suppressClick.current = true;
      commitView(zoomRef.current, drag.panX + dx, drag.panY + dy);
    };

    const onPointerUp = (event: PointerEvent) => {
      if (event.pointerType === 'mouse') drag = null;
    };

    stage.addEventListener('touchstart', onTouchStart, { passive: true });
    stage.addEventListener('touchmove', onTouchMove, { passive: false });
    stage.addEventListener('touchend', onTouchEnd);
    stage.addEventListener('touchcancel', onTouchEnd);
    stage.addEventListener('gesturestart', onGestureStart as EventListener, { passive: false });
    stage.addEventListener('gesturechange', onGestureChange as EventListener, { passive: false });
    stage.addEventListener('gestureend', onGestureEnd as EventListener, { passive: false });
    stage.addEventListener('wheel', onWheel, { passive: false });
    stage.addEventListener('pointerdown', onPointerDown);
    stage.addEventListener('pointermove', onPointerMove);
    stage.addEventListener('pointerup', onPointerUp);
    stage.addEventListener('pointercancel', onPointerUp);
    return () => {
      stage.removeEventListener('touchstart', onTouchStart);
      stage.removeEventListener('touchmove', onTouchMove);
      stage.removeEventListener('touchend', onTouchEnd);
      stage.removeEventListener('touchcancel', onTouchEnd);
      stage.removeEventListener('gesturestart', onGestureStart as EventListener);
      stage.removeEventListener('gesturechange', onGestureChange as EventListener);
      stage.removeEventListener('gestureend', onGestureEnd as EventListener);
      stage.removeEventListener('wheel', onWheel);
      stage.removeEventListener('pointerdown', onPointerDown);
      stage.removeEventListener('pointermove', onPointerMove);
      stage.removeEventListener('pointerup', onPointerUp);
      stage.removeEventListener('pointercancel', onPointerUp);
    };
    // Handlers read the latest view through refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fullscreen]);

  useEffect(() => {
    const limits = panLimits(zoomRef.current, viewRef.current);
    const px = clamp(panRef.current.x, limits.minX, limits.maxX);
    const py = clamp(panRef.current.y, limits.minY, limits.maxY);
    if (px === panRef.current.x && py === panRef.current.y) return;
    panRef.current = { x: px, y: py };
    setPan({ x: px, y: py });
  }, [quarterTurn, stageSize.w, stageSize.h, zoom, obstructionBottom]);

  useEffect(() => {
    if (obstructionBottom < 24 || userMovedPicture.current) return;
    const look = lookAtRef.current;
    if (!look) return;
    const visibleCenterY = -obstructionBottom / 2;
    commitView(zoomRef.current, look.panX, look.panY + (visibleCenterY - look.y));
  }, [obstructionBottom]);

  const injectClickAt = async (clientX: number, clientY: number) => {
    if (!interactiveRef.current && !typingRef.current) {
      if (!fullscreenRef.current && zoomRef.current <= 1.02) {
        void enterFullscreen();
      }
      return;
    }

    const stage = stageRef.current;
    const view = viewRef.current;
    if (!stage || view.lw < 8 || view.lh < 8) return;

    const rect = stage.getBoundingClientRect();
    const mx = clientX - (rect.left + rect.width / 2);
    const my = clientY - (rect.top + rect.height / 2);
    lookAtRef.current = { x: mx, y: my, panX: panRef.current.x, panY: panRef.current.y };
    const local = screenToLocal(mx, my, panRef.current.x, panRef.current.y, zoomRef.current, view.quarterTurn);
    const localX = local.x + view.lw / 2;
    const localY = local.y + view.lh / 2;
    if (localX < 0 || localY < 0 || localX > view.lw || localY > view.lh) return;

    const hostX = Math.round((localX / view.lw) * view.naturalW);
    const hostY = Math.round((localY / view.lh) * view.naturalH);

    setClickRipple({ x: clientX - rect.left, y: clientY - rect.top });
    window.setTimeout(() => setClickRipple(null), 600);

    try {
      await fetch('/api/click', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ x: hostX, y: hostY, button: 'left' }),
      });
      window.setTimeout(refreshScreenshot, 150);
    } catch (err) {
      console.debug('Click injection failed:', err);
    }
  };

  const handleStageClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (skipClick.current) {
      skipClick.current = false;
      return;
    }
    if ((event.target as HTMLElement).closest('button, select, input, textarea')) return;
    if (suppressClick.current) {
      suppressClick.current = false;
      return;
    }
    void injectClickAt(event.clientX, event.clientY);
  };

  const sendTyping = (body: { text?: string; backspaces?: number; key?: string }) => {
    typeQueueRef.current = typeQueueRef.current
      .catch(() => undefined)
      .then(async () => {
        const response = await fetch('/api/type', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(body),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.ok === false) {
          const message = typeof data.error === 'string' ? data.error : 'Could not type on the desktop';
          setTypeError(message);
          return;
        }
        setTypeError('');
        window.clearTimeout(typeRefreshRef.current);
        typeRefreshRef.current = window.setTimeout(() => {
          if (typingRef.current) setTimestamp(Date.now());
        }, 450);
      });
  };

  const onDraftInput = (event: React.FormEvent<HTMLInputElement>) => {
    const next = event.currentTarget.value;
    const prev = sentTextRef.current;
    let same = 0;
    const limit = Math.min(prev.length, next.length);
    while (same < limit && prev[same] === next[same]) same += 1;
    const backspaces = prev.length - same;
    const text = next.slice(same);
    sentTextRef.current = next;
    if (backspaces || text) sendTyping({ backspaces, text });
  };

  const onTypeKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      sendTyping({ key: 'enter' });
      return;
    }
    if (event.key === 'Tab') {
      event.preventDefault();
      sendTyping({ key: 'tab' });
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      sendTyping({ key: 'esc' });
      return;
    }
    if (event.key === 'Backspace' && event.currentTarget.value === '') {
      event.preventDefault();
      sendTyping({ key: 'backspace' });
    }
  };

  const sendHostKey = (key: string) => (event: React.PointerEvent) => {
    event.preventDefault();
    if (key === 'backspace') {
      const input = typeInputRef.current;
      if (input && input.value) {
        input.value = input.value.slice(0, -1);
        sentTextRef.current = input.value;
      }
    }
    sendTyping({ key });
    typeInputRef.current?.focus();
  };

  const imageUrl = `/api/screenshot?token=${encodeURIComponent(token)}&t=${timestamp}`;
  const zoomLabel = `${Math.round(zoom * 100)}%`;

  const zoomControls = (
    <div className="flex items-center gap-1">
      <button
        type="button"
        onClick={() => setZoomAround(zoomRef.current / 1.25)}
        disabled={zoom <= MIN_ZOOM + 0.01}
        className="p-1.5 rounded-lg bg-[#1e1f29] border border-[#44475a] text-[#f8f8f2] hover:bg-[#44475a] active:scale-95 transition disabled:opacity-40"
        title="Zoom out"
      >
        <ZoomOut className="w-3.5 h-3.5" />
      </button>
      <button
        type="button"
        onClick={resetView}
        className="min-w-[3rem] px-1.5 py-1 rounded-lg bg-[#1e1f29] border border-[#44475a] text-[10px] font-semibold text-[#f8f8f2] hover:bg-[#44475a]"
        title="Fit the screen"
      >
        {zoomLabel}
      </button>
      <button
        type="button"
        onClick={() => setZoomAround(zoomRef.current * 1.25)}
        disabled={zoom >= MAX_ZOOM - 0.01}
        className="p-1.5 rounded-lg bg-[#1e1f29] border border-[#44475a] text-[#f8f8f2] hover:bg-[#44475a] active:scale-95 transition disabled:opacity-40"
        title="Zoom in"
      >
        <ZoomIn className="w-3.5 h-3.5" />
      </button>
      <button
        type="button"
        onClick={() => setRotateOverride((current) => (current === null ? !quarterTurn : !current))}
        className={`p-1.5 rounded-lg border active:scale-95 transition ${
          quarterTurn
            ? 'bg-[#bd93f9]/20 text-[#bd93f9] border-[#bd93f9]'
            : 'bg-[#1e1f29] text-[#f8f8f2] border-[#44475a] hover:bg-[#44475a]'
        }`}
        title={quarterTurn ? 'Show the desktop upright' : 'Turn the desktop sideways'}
      >
        <RotateCw className="w-3.5 h-3.5" />
      </button>
    </div>
  );

  return (
    <div className={`flex flex-col h-full bg-[#1e1f29] ${fullscreen ? 'p-0' : 'p-3 sm:p-4'}`}>
      {!fullscreen && (
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3 bg-[#282a36] border border-[#44475a] p-2.5 rounded-xl">
          <div className="flex items-center gap-2">
            <Monitor className="w-4 h-4 text-[#bd93f9]" />
            <span className="text-xs font-bold text-[#f8f8f2]">Desktop Mirror</span>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={() => {
                if (typing) {
                  setTyping(false);
                  typeInputRef.current?.blur();
                  return;
                }
                setInteractiveMode(true);
                setTyping(true);
                typeInputRef.current?.focus();
              }}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-semibold transition active:scale-95 ${
                typing
                  ? 'bg-[#8be9fd]/20 text-[#8be9fd] border border-[#8be9fd]'
                  : 'bg-[#1e1f29] text-[#6272a4] border border-[#44475a]'
              }`}
              title="Type on the desktop with this phone's keyboard"
            >
              <Keyboard className="w-3 h-3" />
              <span>{typing ? 'Typing' : 'Type'}</span>
            </button>

            <button
              type="button"
              onClick={() => setInteractiveMode(!interactiveMode)}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-semibold transition active:scale-95 ${
                interactiveMode
                  ? 'bg-[#ffb86c]/20 text-[#ffb86c] border border-[#ffb86c]'
                  : 'bg-[#1e1f29] text-[#6272a4] border border-[#44475a]'
              }`}
              title={interactiveMode ? 'Tap-to-Click Active' : 'Enable Tap-to-Click'}
            >
              <MousePointer className="w-3 h-3" />
              <span>{interactiveMode ? 'Touch Click' : 'View Only'}</span>
            </button>

            <button
              type="button"
              onClick={() => setAutoRefresh(!autoRefresh)}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-semibold transition active:scale-95 ${
                autoRefresh
                  ? 'bg-[#50fa7b]/20 text-[#50fa7b] border border-[#50fa7b]/50'
                  : 'bg-[#1e1f29] text-[#6272a4] border border-[#44475a]'
              }`}
            >
              {autoRefresh ? <Pause className="w-3 h-3" /> : <Play className="w-3 h-3" />}
              <span>{autoRefresh ? `${refreshIntervalMs / 1000}s` : 'Auto'}</span>
            </button>

            {autoRefresh && (
              <select
                value={refreshIntervalMs}
                onChange={(e) => setRefreshIntervalMs(Number(e.target.value))}
                className="bg-[#1e1f29] text-[#f8f8f2] border border-[#44475a] text-[10px] rounded-md px-1.5 py-1 focus:outline-none"
              >
                <option value={1000}>1s</option>
                <option value={2000}>2s</option>
                <option value={5000}>5s</option>
              </select>
            )}

            <button
              type="button"
              onClick={refreshScreenshot}
              className="p-1.5 rounded-lg bg-[#1e1f29] border border-[#44475a] text-[#f8f8f2] hover:bg-[#44475a] active:scale-95 transition"
              title="Refresh screenshot"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin text-[#bd93f9]' : ''}`} />
            </button>

            {zoomControls}

            <button
              type="button"
              onClick={() => void enterFullscreen()}
              className="p-1.5 rounded-lg bg-[#1e1f29] border border-[#44475a] text-[#f8f8f2] hover:bg-[#44475a] active:scale-95 transition"
              title="Fullscreen, turned horizontal"
            >
              <Maximize2 className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}

      <div
        ref={stageRef}
        data-eidos-screen
        onClick={handleStageClick}
        onContextMenu={(event) => event.preventDefault()}
        onPointerDown={(event) => {
          if (!typingRef.current) return;
          if ((event.target as HTMLElement).closest('button, input, textarea, select')) return;
          if (document.activeElement === typeInputRef.current) {
            event.preventDefault();
            keepKeyboard.current = true;
          }
        }}
        onPointerUp={(event) => {
          if (!keepKeyboard.current) return;
          keepKeyboard.current = false;
          skipClick.current = true;
          if (suppressClick.current) {
            suppressClick.current = false;
            return;
          }
          void injectClickAt(event.clientX, event.clientY);
          window.setTimeout(() => typeInputRef.current?.focus(), 0);
        }}
        className={`relative flex items-center justify-center overflow-hidden touch-none ${
          fullscreen
            ? 'fixed inset-0 z-[80] bg-black'
            : 'flex-1 min-h-0 bg-[#14151b] border border-[#44475a] rounded-xl'
        } ${typing || zoom > 1 ? 'cursor-grab' : interactiveMode ? 'cursor-crosshair' : 'cursor-pointer'}`}
      >
        {seen && (
        <img
          src={imageUrl}
          alt="Live Desktop View"
          draggable={false}
          onLoad={(event) => {
            setLoading(false);
            const img = event.currentTarget;
            if (img.naturalWidth > 0 && img.naturalHeight > 0) {
              setNatural({ w: img.naturalWidth, h: img.naturalHeight });
            }
          }}
          onError={() => setLoading(false)}
          style={
            fit.lw > 0
              ? {
                  width: fit.lw,
                  height: fit.lh,
                  maxWidth: 'none',
                  maxHeight: 'none',
                  transform: `translate(calc(-50% + ${pan.x}px), calc(-50% + ${pan.y}px)) rotate(${quarterTurn ? 90 : 0}deg) scale(${zoom})`,
                  transformOrigin: 'center center',
                }
              : { maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }
          }
          className={`select-none pointer-events-none ${fit.lw > 0 ? 'absolute left-1/2 top-1/2' : ''}`}
        />
        )}

        {clickRipple && (
          <span
            style={{ left: clickRipple.x - 12, top: clickRipple.y - 12 }}
            className="absolute w-6 h-6 rounded-full border-2 border-[#50fa7b] bg-[#50fa7b]/40 animate-ping pointer-events-none"
          />
        )}

        {loading && (
          <div className="absolute top-3 right-3 bg-[#1e1f29]/90 border border-[#44475a] px-2 py-1 rounded-md text-[10px] text-[#6272a4] flex items-center gap-1.5 shadow-md pointer-events-none">
            <RefreshCw className="w-3 h-3 animate-spin text-[#bd93f9]" />
            Updating...
          </div>
        )}

        {fullscreen && (
          <div
            className="absolute left-2 right-2 z-10 flex items-center justify-between gap-2"
            style={{ top: 'max(0.5rem, env(safe-area-inset-top))' }}
          >
            <div className="flex items-center gap-1 rounded-xl bg-[#1e1f29]/95 border border-[#44475a] p-1">
              {zoomControls}
            </div>
            <button
              type="button"
              onClick={exitFullscreen}
              className="p-2 rounded-xl bg-[#1e1f29]/95 border border-[#44475a] text-[#f8f8f2] hover:text-[#ff5555]"
              title="Close fullscreen"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        )}

        {fullscreen && !typing && (
          <div
            className="absolute left-3 right-3 flex justify-center pointer-events-none"
            style={{ bottom: 'max(0.75rem, env(safe-area-inset-bottom))' }}
          >
            <div className="bg-[#1e1f29]/90 border border-[#44475a] text-[#6272a4] px-2 py-0.5 rounded-md text-[10px] font-semibold shadow-md">
              {quarterTurn ? 'Horizontal · ' : ''}
              {interactiveMode ? 'Tap to click · pinch to zoom' : 'Pinch to zoom · drag to look around'}
            </div>
          </div>
        )}
      </div>

      <div
        ref={typeBarRef}
        data-eidos-screen
        className={
          typing
            ? 'fixed inset-x-0 z-[90] mx-auto px-2'
            : 'fixed left-0 bottom-0 z-[90] h-px w-px overflow-hidden opacity-0 pointer-events-none'
        }
        style={
          typing
            ? {
                bottom: `calc(${keyboardLift}px + env(safe-area-inset-bottom))`,
                maxWidth: fullscreen ? 'none' : '42rem',
              }
            : undefined
        }
      >
        <div className={typing ? 'bg-[#282a36]/95 border border-[#44475a] rounded-xl p-2 shadow-lg' : ''}>
            <input
              ref={typeInputRef}
              type="text"
              enterKeyHint="enter"
              autoCapitalize="sentences"
              autoCorrect="on"
              autoComplete="off"
              spellCheck
              placeholder="Type with your Samsung keyboard"
              aria-label="Type on the desktop"
              onInput={onDraftInput}
              onKeyDown={onTypeKeyDown}
              className="w-full bg-[#1e1f29] border border-[#44475a] text-[#f8f8f2] rounded-lg px-3 py-2 focus:outline-none focus:border-[#8be9fd]"
              style={{ fontSize: 16 }}
            />
            {typing && (
            <div className="mt-1.5 flex items-center gap-1">
              <button type="button" onPointerDown={sendHostKey('backspace')} className="px-2 py-1 rounded-lg bg-[#1e1f29] border border-[#44475a] text-[10px] font-semibold text-[#f8f8f2]">
                Delete
              </button>
              <button type="button" onPointerDown={sendHostKey('tab')} className="px-2 py-1 rounded-lg bg-[#1e1f29] border border-[#44475a] text-[10px] font-semibold text-[#f8f8f2]">
                Tab
              </button>
              <button type="button" onPointerDown={sendHostKey('esc')} className="px-2 py-1 rounded-lg bg-[#1e1f29] border border-[#44475a] text-[10px] font-semibold text-[#f8f8f2]">
                Esc
              </button>
              <button type="button" onPointerDown={sendHostKey('enter')} className="px-2 py-1 rounded-lg bg-[#8be9fd]/20 border border-[#8be9fd] text-[10px] font-semibold text-[#8be9fd]">
                Enter
              </button>
              <button
                type="button"
                onPointerDown={(event) => {
                  event.preventDefault();
                  if (typeInputRef.current) typeInputRef.current.value = '';
                  sentTextRef.current = '';
                  typeInputRef.current?.focus();
                }}
                className="ml-auto px-2 py-1 rounded-lg bg-[#1e1f29] border border-[#44475a] text-[10px] font-semibold text-[#6272a4]"
                title="Clears this box only. It does not delete text on the computer."
              >
                Clear
              </button>
            </div>
            )}
            {typing && (
              <p className={`mt-1 text-[10px] ${typeError ? 'text-[#ff5555]' : 'text-[#6272a4]'}`}>
                {typeError || 'Drag the picture to see where you are typing. A tap still clicks.'}
              </p>
            )}
          </div>
        </div>
    </div>
  );
};
