import { useEffect, useRef, useState } from 'react';
import { applyTextScale, readTextScale, saveTextScale } from '../textScale';

function touchDistance(touches: TouchList): number {
  const first = touches[0];
  const second = touches[1];
  return Math.hypot(first.clientX - second.clientX, first.clientY - second.clientY);
}

/** The desktop mirror handles its own pinch-to-zoom. Leave those gestures alone. */
function gestureTargetsScreen(event: Event): boolean {
  const path = typeof event.composedPath === 'function' ? event.composedPath() : [];
  if (path.some((node) => node instanceof Element && node.hasAttribute('data-eidos-screen'))) {
    return true;
  }
  const target = event.target;
  return target instanceof Element && Boolean(target.closest('[data-eidos-screen]'));
}

/**
 * Pinch (and trackpad pinch) changes text size instead of zooming the page.
 * The chosen size is remembered on this device.
 */
export function usePinchTextScale() {
  const [scale, setScale] = useState(() => applyTextScale(readTextScale()));
  const [showBadge, setShowBadge] = useState(false);
  const scaleRef = useRef(scale);

  useEffect(() => {
    let pinching = false;
    let usingGesture = false;
    let startDistance = 0;
    let startScale = scaleRef.current;
    let frame = 0;
    let hideTimer = 0;
    let saveTimer = 0;

    const reveal = (next: number) => {
      scaleRef.current = applyTextScale(next);
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        setScale(scaleRef.current);
        setShowBadge(true);
      });
    };

    const hideBadgeSoon = () => {
      window.clearTimeout(hideTimer);
      hideTimer = window.setTimeout(() => setShowBadge(false), 700);
    };

    const commit = () => {
      const saved = saveTextScale(scaleRef.current);
      scaleRef.current = saved;
      setScale(saved);
      hideBadgeSoon();
    };

    const onTouchStart = (event: TouchEvent) => {
      if (gestureTargetsScreen(event)) return;
      if (event.touches.length !== 2) return;
      pinching = true;
      startDistance = touchDistance(event.touches);
      startScale = scaleRef.current;
    };

    const onTouchMove = (event: TouchEvent) => {
      if (gestureTargetsScreen(event)) return;
      if (usingGesture || !pinching || event.touches.length !== 2 || startDistance < 8) return;
      event.preventDefault();
      reveal(startScale * (touchDistance(event.touches) / startDistance));
    };

    const onTouchEnd = (event: TouchEvent) => {
      if (gestureTargetsScreen(event)) return;
      if (!pinching || event.touches.length >= 2) return;
      pinching = false;
      commit();
    };

    const onGestureStart = (event: Event) => {
      if (gestureTargetsScreen(event)) return;
      event.preventDefault();
      usingGesture = true;
      pinching = true;
      startScale = scaleRef.current;
    };

    const onGestureChange = (event: Event) => {
      if (gestureTargetsScreen(event)) return;
      event.preventDefault();
      const factor = (event as Event & { scale?: number }).scale;
      if (!factor || factor <= 0) return;
      reveal(startScale * factor);
    };

    const onGestureEnd = (event: Event) => {
      if (gestureTargetsScreen(event)) return;
      event.preventDefault();
      if (!usingGesture) return;
      usingGesture = false;
      pinching = false;
      commit();
    };

    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey || gestureTargetsScreen(event)) return;
      event.preventDefault();
      const factor = Math.exp(-event.deltaY * 0.002);
      reveal(scaleRef.current * factor);
      window.clearTimeout(saveTimer);
      saveTimer = window.setTimeout(commit, 160);
    };

    document.addEventListener('touchstart', onTouchStart, { passive: true, capture: true });
    document.addEventListener('touchmove', onTouchMove, { passive: false, capture: true });
    document.addEventListener('touchend', onTouchEnd, { capture: true });
    document.addEventListener('touchcancel', onTouchEnd, { capture: true });
    document.addEventListener('gesturestart', onGestureStart, { passive: false, capture: true });
    document.addEventListener('gesturechange', onGestureChange, { passive: false, capture: true });
    document.addEventListener('gestureend', onGestureEnd, { passive: false, capture: true });
    window.addEventListener('wheel', onWheel, { passive: false });

    return () => {
      document.removeEventListener('touchstart', onTouchStart, { capture: true });
      document.removeEventListener('touchmove', onTouchMove, { capture: true });
      document.removeEventListener('touchend', onTouchEnd, { capture: true });
      document.removeEventListener('touchcancel', onTouchEnd, { capture: true });
      document.removeEventListener('gesturestart', onGestureStart, { capture: true });
      document.removeEventListener('gesturechange', onGestureChange, { capture: true });
      document.removeEventListener('gestureend', onGestureEnd, { capture: true });
      window.removeEventListener('wheel', onWheel);
      window.cancelAnimationFrame(frame);
      window.clearTimeout(hideTimer);
      window.clearTimeout(saveTimer);
    };
  }, []);

  return { scale, showBadge };
}
