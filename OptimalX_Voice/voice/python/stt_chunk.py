"""Slice long Parakeet recordings under sherpa-onnx's per-call limit.

The INT8 Parakeet TDT 0.6B v2 offline model rejects one decode longer than
240 seconds. Slices stay under that cap. Edges overlap by a short margin so a
word on the cut is heard twice, then the transcripts are stitched.
"""

from __future__ import annotations

from typing import Sequence

MODEL_MAX_SECONDS = 240.0
# Single decode stays this far under the model cap.
SAFE_SINGLE_PASS_SECONDS = 60.0
CHUNK_SECONDS = 60.0
OVERLAP_SECONDS = 1.0
SILENCE_SEARCH_SECONDS = 20.0
MIN_CHUNK_SECONDS = 20.0
# Quietest ~300 ms when frames are 20 ms.
SILENCE_WINDOW_FRAMES = 15


def plan_slices(
    n_samples: int,
    sample_rate: int,
    *,
    rms: Sequence[float] | None = None,
    frame_size: int = 0,
    chunk_seconds: float = CHUNK_SECONDS,
    overlap_seconds: float = OVERLAP_SECONDS,
    safe_single_pass_seconds: float = SAFE_SINGLE_PASS_SECONDS,
    silence_search_seconds: float = SILENCE_SEARCH_SECONDS,
    min_chunk_seconds: float = MIN_CHUNK_SECONDS,
    silence_window_frames: int = SILENCE_WINDOW_FRAMES,
) -> list[tuple[int, int]]:
    """Return half-open sample ranges covering ``[0, n_samples)``.

    Audio that already fits in one safe pass is a single range. Longer audio
    is cut near ``chunk_seconds``, shifted earlier when the search window
    contains a real pause. The next range starts ``overlap_seconds`` before
    that cut.
    """
    if n_samples <= 0 or sample_rate <= 0:
        return []

    chunk_seconds = min(float(chunk_seconds), float(safe_single_pass_seconds))
    if chunk_seconds <= overlap_seconds:
        raise ValueError("chunk_seconds must be longer than the overlap")
    if safe_single_pass_seconds >= MODEL_MAX_SECONDS:
        raise ValueError("safe_single_pass_seconds must stay under the 240s model cap")

    safe_samples = int(safe_single_pass_seconds * sample_rate)
    chunk_samples = max(1, int(chunk_seconds * sample_rate))
    overlap_samples = max(0, int(overlap_seconds * sample_rate))
    search_samples = max(0, int(silence_search_seconds * sample_rate))
    min_samples = max(1, int(min_chunk_seconds * sample_rate))
    min_advance = max(1, min_samples)

    use_rms = bool(rms) and frame_size > 0
    slices: list[tuple[int, int]] = []
    start = 0
    guard = (n_samples // min_advance) + 2

    while start < n_samples and guard > 0:
        guard -= 1
        remaining = n_samples - start
        if remaining <= safe_samples:
            slices.append((start, n_samples))
            break

        target = min(n_samples, start + chunk_samples)
        search_from = max(start + min_samples, target - search_samples)
        if use_rms and target > search_from:
            cut = _quietest_sample(
                rms,
                frame_size,
                search_from,
                target,
                silence_window_frames,
            )
        else:
            cut = target

        if cut <= start or cut > target:
            cut = target
        if cut >= n_samples:
            slices.append((start, n_samples))
            break

        slices.append((start, cut))
        # Overlap the cut so a word on the boundary is decoded twice.
        # Never jump past `cut` — that would drop the audio after an early pause.
        next_start = cut - overlap_samples
        if next_start <= start:
            next_start = cut
        if next_start <= start:
            next_start = min(n_samples, start + min_advance)
        start = next_start
    else:
        if start < n_samples:
            slices.append((start, n_samples))

    return _clamp_slices(slices, n_samples, safe_samples)


def stitch_transcripts(parts: Sequence[str], max_words: int = 12) -> str:
    """Join chunk transcripts, dropping a repeated overlap phrase."""
    text = ""
    for part in parts:
        piece = " ".join(str(part).split())
        if not piece:
            continue
        text = piece if not text else _merge_overlap(text, piece, max_words=max_words)
    return text


def _quietest_sample(
    rms: Sequence[float],
    frame_size: int,
    search_from: int,
    search_to: int,
    window_frames: int,
) -> int:
    """Sample index of the quietest pause in ``[search_from, search_to)``.

    Returns ``search_to`` when the window has no pause clearly quieter than
    its median, so steady audio is cut at the chunk target.
    """
    if search_to <= search_from or frame_size <= 0 or not rms:
        return search_to

    i0 = max(0, search_from // frame_size)
    i1 = min(len(rms), (search_to + frame_size - 1) // frame_size)
    if i1 <= i0:
        return search_to

    window = max(1, min(int(window_frames), i1 - i0))
    energies: list[float] = []
    acc = float(sum(rms[i0 : i0 + window]))
    energies.append(acc)
    for i in range(i0 + 1, i1 - window + 1):
        acc += float(rms[i + window - 1]) - float(rms[i - 1])
        energies.append(acc)

    if not energies:
        return search_to

    ordered = sorted(energies)
    median = ordered[len(ordered) // 2]
    best_at = min(range(len(energies)), key=energies.__getitem__)
    best_e = energies[best_at]
    if median <= 1e-8 or best_e > median * 0.65:
        return search_to

    center_frame = i0 + best_at + window // 2
    return min(search_to, max(search_from, center_frame * frame_size))


# A single shared function word is not evidence of the 1s slice overlap.
_WEAK_OVERLAP = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "to",
        "of",
        "in",
        "on",
        "for",
        "is",
        "it",
        "that",
        "this",
        "i",
        "you",
        "we",
        "he",
        "she",
        "they",
    }
)


def _merge_overlap(prev: str, nxt: str, max_words: int) -> str:
    prev_words = prev.split()
    next_words = nxt.split()
    if not prev_words:
        return nxt
    if not next_words:
        return prev

    limit = min(len(prev_words), len(next_words), max(1, max_words))
    prev_norm = [_normalize_token(word) for word in prev_words]
    next_norm = [_normalize_token(word) for word in next_words]
    best = 0
    for count in range(limit, 0, -1):
        if prev_norm[-count:] == next_norm[:count] and any(prev_norm[-count:]):
            best = count
            break

    one_word = best == 1 and (
        best == len(next_words)
        or best == len(prev_words)
        or prev_norm[-1] not in _WEAK_OVERLAP
    )
    strong = best >= 2 or one_word
    if strong:
        return " ".join(prev_words + next_words[best:])
    return f"{prev} {nxt}".strip()


def _normalize_token(word: str) -> str:
    return word.lower().strip(".,!?;:\"'`“”()[]{}")


def _clamp_slices(
    slices: list[tuple[int, int]],
    n_samples: int,
    safe_samples: int,
) -> list[tuple[int, int]]:
    """Drop empty ranges and hard-split anything still over the safe cap."""
    out: list[tuple[int, int]] = []
    for start, end in slices:
        start = max(0, min(n_samples, int(start)))
        end = max(start, min(n_samples, int(end)))
        if end <= start:
            continue
        if safe_samples > 0 and end - start > safe_samples:
            cursor = start
            while cursor < end:
                nxt = min(end, cursor + safe_samples)
                out.append((cursor, nxt))
                if nxt <= cursor:
                    break
                cursor = nxt
            continue
        out.append((start, end))
    return out
