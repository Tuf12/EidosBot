"""Stop a desktop task when the same action stops changing anything.

A long run is normal. A loop is the same tool, the same arguments, and the
same result — or pointer clicks that never move the focused window.
The first time a pattern trips, the caller shows the model a correction.
The next time that same pattern trips, the caller ends the task.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

COORD_GRID = 16
REPEAT_THRESHOLD = 3
REPEAT_WINDOW = 6
REPEAT_WINDOW_HITS = 4
FAILURE_THRESHOLD = 3
GUI_UNCHANGED_THRESHOLD = 4
PERCEPTION_SPIN_THRESHOLD = 3

_COORD_KEYS = frozenset({"x", "y"})
_VOLATILE_KEYS = frozenset({"timestamp", "request_id"})
_DIGIT_RUN = re.compile(r"\d+")


@dataclass(frozen=True)
class StallVerdict:
    """One stalled pattern. `stop` is the second strike for this signature."""

    signature: str
    detail: str
    stop: bool

    def nudge_text(self) -> str:
        return (
            f"Stall check: {self.detail}. "
            "Do something different, or call finish_task and say what is blocked."
        )

    def stop_headline(self) -> str:
        return f"Stopped because {self.detail}."


class StallTracker:
    """Rolling record of tool results for one task."""

    POINTER_ACTS = frozenset({"click", "double_click", "click_element"})
    PERCEPTION_ACTS = frozenset({
        "take_screenshot",
        "get_active_window",
        "browser_screenshot",
        "get_current_time",
    })

    def __init__(self) -> None:
        self._fingerprints: list[str] = []
        self._fail_key = ""
        self._fail_run = 0
        self._gui_unchanged = 0
        self._perception_run = 0
        self._nudged: set[str] = set()

    def commit_nudge(self, signature: str) -> None:
        """Remember that the model has already been told about this pattern."""
        self._nudged.add(signature)

    def observe(
        self,
        name: str,
        arguments: Any,
        result: Any,
        window_before: Optional[Dict[str, Any]],
        window_after: Optional[Dict[str, Any]],
    ) -> Optional[StallVerdict]:
        """Update streaks from one tool result. Return a verdict when a pattern trips."""
        if not name or name == "finish_task":
            return None

        failed, error_class = _outcome_class(result)
        self._note_fingerprint(name, arguments, failed, error_class)
        self._note_failure(name, failed, error_class)
        self._note_gui(name, window_before, window_after)
        self._note_perception(name)

        signature, detail = self._match(name, error_class)
        if signature is None or detail is None:
            return None
        return StallVerdict(
            signature=signature,
            detail=detail,
            stop=signature in self._nudged,
        )

    def _note_fingerprint(self, name: str, arguments: Any, failed: bool, error_class: str) -> None:
        payload = _normalize_data(arguments if isinstance(arguments, dict) else {"value": arguments})
        blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
        self._fingerprints.append(f"{name}|{int(failed)}|{error_class}|{blob}")
        if len(self._fingerprints) > REPEAT_WINDOW:
            del self._fingerprints[:-REPEAT_WINDOW]

    def _note_failure(self, name: str, failed: bool, error_class: str) -> None:
        if not failed:
            self._fail_key = ""
            self._fail_run = 0
            return
        key = f"{name}|{error_class}"
        if key == self._fail_key:
            self._fail_run += 1
        else:
            self._fail_key = key
            self._fail_run = 1

    def _note_gui(
        self,
        name: str,
        window_before: Optional[Dict[str, Any]],
        window_after: Optional[Dict[str, Any]],
    ) -> None:
        if name in self.PERCEPTION_ACTS:
            return
        if name not in self.POINTER_ACTS:
            self._gui_unchanged = 0
            return
        before = _window_key(window_before)
        after = _window_key(window_after)
        if before is not None and before == after:
            self._gui_unchanged += 1
        else:
            self._gui_unchanged = 0

    def _note_perception(self, name: str) -> None:
        if name in self.PERCEPTION_ACTS:
            self._perception_run += 1
        else:
            self._perception_run = 0

    def _match(self, name: str, error_class: str) -> tuple[Optional[str], Optional[str]]:
        if self._fail_run >= FAILURE_THRESHOLD:
            short = error_class if len(error_class) <= 80 else error_class[:79] + "…"
            return (
                f"failure:{self._fail_key}",
                f"{name} failed {self._fail_run} times in a row ({short})",
            )
        repeat_detail = _repeat_detail(name, self._fingerprints)
        if repeat_detail is not None:
            return f"repeat:{self._fingerprints[-1]}", repeat_detail
        if self._gui_unchanged >= GUI_UNCHANGED_THRESHOLD:
            return (
                "gui:unchanged",
                f"{name} did not change the focused window across {self._gui_unchanged} clicks",
            )
        if self._perception_run >= PERCEPTION_SPIN_THRESHOLD:
            return (
                "perception:spin",
                f"{name} ran {self._perception_run} times in a row without another action",
            )
        return None, None


def _repeat_detail(name: str, fingerprints: list[str]) -> Optional[str]:
    if (
        len(fingerprints) >= REPEAT_THRESHOLD
        and len(set(fingerprints[-REPEAT_THRESHOLD:])) == 1
    ):
        return (
            f"{name} ran {REPEAT_THRESHOLD} times in a row "
            "with the same arguments and the same result"
        )
    if (
        len(fingerprints) >= REPEAT_WINDOW_HITS
        and fingerprints[-REPEAT_WINDOW:].count(fingerprints[-1]) >= REPEAT_WINDOW_HITS
    ):
        return (
            f"{name} repeated the same arguments and the same result "
            f"{REPEAT_WINDOW_HITS} times in the last {REPEAT_WINDOW} actions"
        )
    return None


def _outcome_class(result: Any) -> tuple[bool, str]:
    if not isinstance(result, dict):
        return False, "ok"
    if result.get("denied") is True:
        return True, "denied"
    exit_code = result.get("exit_code")
    if exit_code not in (None, 0):
        return True, f"exit:{exit_code}"
    if result.get("success") is False or (result.get("success") is not True and result.get("error")):
        return True, _norm_error(result.get("error") or result.get("stderr") or "failed")
    return False, "ok"


def _norm_error(value: Any) -> str:
    text = " ".join(str(value).split())
    text = _DIGIT_RUN.sub("#", text)
    return text[:80] or "failed"


def _grid_coord(value: Any) -> int:
    return int(float(value) // COORD_GRID) * COORD_GRID


def _normalize_data(value: Any, key: str = "") -> Any:
    if key in _COORD_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool):
        return _grid_coord(value)
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        return {
            str(item_key): _normalize_data(item_value, str(item_key))
            for item_key, item_value in sorted(value.items(), key=lambda item: str(item[0]))
            if str(item_key) not in _VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_normalize_data(item) for item in value]
    return value


def _window_key(info: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(info, dict):
        return None
    title = str(info.get("title") or info.get("active_window") or "").strip()
    class_name = str(info.get("class_name") or info.get("active_window_class") or "").strip()
    geometry = info.get("geometry") if isinstance(info.get("geometry"), dict) else {}

    def cell(raw: Any) -> int:
        try:
            return _grid_coord(raw)
        except (TypeError, ValueError):
            return 0

    x = cell(geometry.get("x"))
    y = cell(geometry.get("y"))
    width = cell(geometry.get("width") if geometry.get("width") is not None else geometry.get("w"))
    height = cell(geometry.get("height") if geometry.get("height") is not None else geometry.get("h"))
    if not title and not class_name and not any((x, y, width, height)):
        return None
    return f"{title}|{class_name}|{x}|{y}|{width}|{height}"
