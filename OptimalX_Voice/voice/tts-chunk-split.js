'use strict';

/** First utterance: start audio fast. */
const HEAD1_MIN = 80;
const HEAD1_MAX = 140;
/** Second utterance: cover remaining synth of the first 380-char body. */
const HEAD2_MIN = 160;
const HEAD2_MAX = 220;
/** Steady-state window after the two heads. */
const BODY_MAX = 380;
/** Fold a tiny leftover onto the previous piece instead of a last hop. */
const MIN_TAIL_CHARS = 24;

const WINDOWS = [
  { min: HEAD1_MIN, max: HEAD1_MAX },
  { min: HEAD2_MIN, max: HEAD2_MAX },
];

function normalizeText(text) {
  return String(text || '').replace(/\s+/g, ' ').trim();
}

function sentenceBreaks(windowText) {
  const ends = [];
  const re = /[.!?]["')\]]*(?=\s|$)/g;
  let match;
  while ((match = re.exec(windowText)) !== null) {
    ends.push(match.index + match[0].length);
  }
  return ends;
}

function takeWindow(text, min, max) {
  if (!text) return '';
  if (text.length <= max) return text;

  const windowText = text.slice(0, max);
  const ends = sentenceBreaks(windowText);
  const inRange = ends.filter((index) => index >= min && index <= max);
  if (inRange.length) {
    return text.slice(0, inRange[inRange.length - 1]).trimEnd();
  }

  const spaceFromMin = windowText.lastIndexOf(' ');
  if (spaceFromMin >= min) {
    return text.slice(0, spaceFromMin).trimEnd();
  }
  if (spaceFromMin > 0) {
    return text.slice(0, spaceFromMin).trimEnd();
  }
  return windowText;
}

function splitForTtsPlayback(text) {
  let remaining = normalizeText(text);
  if (!remaining) return [];

  const pieces = [];
  let stage = 0;
  while (remaining) {
    const window = WINDOWS[stage] || { min: 1, max: BODY_MAX };
    let piece = takeWindow(remaining, window.min, window.max);
    if (!piece) break;
    remaining = remaining.slice(piece.length).trim();
    if (remaining && remaining.length < MIN_TAIL_CHARS) {
      piece = `${piece} ${remaining}`.trim();
      remaining = '';
    }
    pieces.push(piece);
    if (stage < WINDOWS.length) stage += 1;
  }
  return pieces;
}

module.exports = {
  HEAD1_MIN,
  HEAD1_MAX,
  HEAD2_MIN,
  HEAD2_MAX,
  BODY_MAX,
  MIN_TAIL_CHARS,
  splitForTtsPlayback,
  takeWindow,
  normalizeText,
};
