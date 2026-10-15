export const TEXT_SCALE_KEY = 'eidos_text_scale';
export const TEXT_SCALE_MIN = 0.85;
export const TEXT_SCALE_MAX = 1.75;
export const TEXT_SCALE_DEFAULT = 1;

export function clampTextScale(value: number): number {
  if (!Number.isFinite(value)) return TEXT_SCALE_DEFAULT;
  return Math.min(TEXT_SCALE_MAX, Math.max(TEXT_SCALE_MIN, value));
}

export function readTextScale(): number {
  try {
    const raw = localStorage.getItem(TEXT_SCALE_KEY);
    if (!raw) return TEXT_SCALE_DEFAULT;
    return clampTextScale(parseFloat(raw));
  } catch {
    return TEXT_SCALE_DEFAULT;
  }
}

/** Apply a text-only scale. Layout, icons, and spacing stay at their normal size. */
export function applyTextScale(value: number): number {
  const next = clampTextScale(value);
  const root = document.documentElement;
  root.style.setProperty('--eidos-text-scale', String(next));
  root.classList.toggle('eidos-text-scale', Math.abs(next - TEXT_SCALE_DEFAULT) > 0.005);
  return next;
}

export function saveTextScale(value: number): number {
  const next = applyTextScale(Math.round(clampTextScale(value) * 100) / 100);
  try {
    if (Math.abs(next - TEXT_SCALE_DEFAULT) <= 0.005) {
      localStorage.removeItem(TEXT_SCALE_KEY);
    } else {
      localStorage.setItem(TEXT_SCALE_KEY, next.toFixed(2));
    }
  } catch {
    // Private mode or a full store should not break pinch gestures.
  }
  return next;
}
