"""Strip markdown and tooling noise so Kokoro reads clean prose.

Ported from OptimalX Desktop electron/voice/sanitize.js — keep behavior in sync.
"""

from __future__ import annotations

import re

_ONES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
)
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")


def _integer_to_words(n: int) -> str:
    if n < 0:
        return ""
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, rem = divmod(n, 10)
        return f"{_TENS[tens]}-{_ONES[rem]}" if rem else _TENS[tens]
    if n < 1000:
        hundreds, rem = divmod(n, 100)
        head = f"{_ONES[hundreds]} hundred"
        return f"{head} {_integer_to_words(rem)}" if rem else head
    if n < 1_000_000:
        thousands, rem = divmod(n, 1000)
        head = f"{_integer_to_words(thousands)} thousand"
        return f"{head} {_integer_to_words(rem)}" if rem else head
    if n < 1_000_000_000:
        millions, rem = divmod(n, 1_000_000)
        head = f"{_integer_to_words(millions)} million"
        return f"{head} {_integer_to_words(rem)}" if rem else head
    return str(n)


def _currency_to_words(raw: str) -> str | None:
    normalized = str(raw or "").replace(",", "")
    match = re.fullmatch(r"(\d+)(?:\.(\d{1,2}))?", normalized)
    if not match:
        return None

    dollars = int(match.group(1))
    cents_raw = match.group(2) or ""
    cents = int(cents_raw.ljust(2, "0")[:2]) if cents_raw else 0

    parts: list[str] = []
    if dollars > 0:
        parts.append(f"{_integer_to_words(dollars)} dollar{'s' if dollars != 1 else ''}")
    if cents > 0:
        parts.append(f"{_integer_to_words(cents)} cent{'s' if cents != 1 else ''}")
    if not parts:
        return "zero dollars"
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return parts[0]


def sanitize_for_tts(text: str | None) -> str:
    """Return prose suitable for Kokoro TTS."""
    s = str(text or "")
    if not s.strip():
        return ""

    s = re.sub(r"```[\s\S]*?```", " ", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"^#{1,6}\s+", "", s, flags=re.MULTILINE)
    s = re.sub(r"(\*\*|__)(.*?)\1", r"\2", s)
    s = re.sub(r"(\*|_)(.*?)\1", r"\2", s)
    s = re.sub(r"~~(.*?)~~", r"\1", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"optimalx://[^\s)]+", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"^\s*[-*+]\s+", "", s, flags=re.MULTILINE)
    s = re.sub(r"^\s*\d+\.\s+", "", s, flags=re.MULTILINE)
    s = re.sub(r"https?://\S+", " ", s, flags=re.IGNORECASE)
    s = re.sub(
        r"(?:\b[A-Za-z]:)?(?:/|\\)[\w.\-~%$@#:]+(?:/|\\[\w.\-~%$@#:]+)*",
        " ",
        s,
    )
    s = s.replace("_", " ")

    def _replace_currency(match: re.Match[str]) -> str:
        spoken = _currency_to_words(match.group(1))
        return f" {spoken} " if spoken else match.group(0)

    s = re.sub(
        r"\$(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)",
        _replace_currency,
        s,
    )
    s = re.sub(r"[$#@%^&*\\/`~+=<>|{}\[\]();]", " ", s)
    s = re.sub(r'[""„«»"]', " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s
