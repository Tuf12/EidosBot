"""Foreground vs background compute rules for local Ollama vs Chutes."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from eidos.models.inference_slot import holder as inference_slot_holder

DEFAULT_IDLE_SECONDS = 900.0


def compute_settings_from_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Read ``models.compute`` with safe defaults."""
    models = (config or {}).get("models") or {}
    compute = models.get("compute") or {}
    mem = (config or {}).get("memory") or {}
    refl = mem.get("reflector") or {}
    idle = compute.get("idle_seconds", refl.get("idle_seconds", DEFAULT_IDLE_SECONDS))
    try:
        idle_seconds = max(0.0, float(idle))
    except (TypeError, ValueError):
        idle_seconds = DEFAULT_IDLE_SECONDS
    return {
        "idle_seconds": idle_seconds,
        "background_prefer_cloud": bool(
            compute.get(
                "background_prefer_cloud",
                refl.get("prefer_cloud_when_busy", True),
            )
        ),
        "defer_local_when_slot_held": bool(compute.get("defer_local_when_slot_held", True)),
    }


class ComputePolicy:
    """Tracks user/agent activity and decides local vs cloud for background LLM work."""

    def __init__(
        self,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        background_prefer_cloud: bool = True,
        defer_local_when_slot_held: bool = True,
        is_task_active: Optional[Callable[[], bool]] = None,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.idle_seconds = idle_seconds
        self.background_prefer_cloud = background_prefer_cloud
        self.defer_local_when_slot_held = defer_local_when_slot_held
        self._is_task_active = is_task_active or (lambda: False)
        self._monotonic = monotonic
        self._last_user_activity = self._monotonic()

    def touch_user_activity(self) -> None:
        """Record user or agent foreground interaction."""
        self._last_user_activity = self._monotonic()

    def seconds_since_activity(self) -> float:
        return max(0.0, self._monotonic() - self._last_user_activity)

    def inference_slot_held(self) -> bool:
        return inference_slot_holder() is not None

    def is_foreground_busy(self) -> bool:
        """True while the user is active, a task runs, or GPU inference slot is taken."""
        if self._is_task_active():
            return True
        if self.inference_slot_held():
            return True
        if self.idle_seconds <= 0:
            return False
        return self.seconds_since_activity() < self.idle_seconds

    def allow_background_local_llm(self) -> bool:
        """Background reflector/consolidator may use local Ollama."""
        return not self.is_foreground_busy()

    def should_defer_local_ollama(self, priority: str = "foreground") -> bool:
        """Skip local Ollama this request (use Chutes) when compute is contended."""
        if not self.defer_local_when_slot_held and priority != "background":
            return False
        if self.inference_slot_held():
            return True
        if priority == "background":
            if self.background_prefer_cloud and self.is_foreground_busy():
                return True
        return False

    def should_use_cloud_for_background(self) -> bool:
        if not self.background_prefer_cloud:
            return False
        return self.is_foreground_busy() or self.inference_slot_held()

    def apply_settings(self, settings: Dict[str, Any]) -> None:
        if "idle_seconds" in settings:
            try:
                self.idle_seconds = max(0.0, float(settings["idle_seconds"]))
            except (TypeError, ValueError):
                pass
        if "background_prefer_cloud" in settings:
            self.background_prefer_cloud = bool(settings["background_prefer_cloud"])
        if "defer_local_when_slot_held" in settings:
            self.defer_local_when_slot_held = bool(settings["defer_local_when_slot_held"])

    @classmethod
    def from_config(
        cls,
        config: Optional[Dict[str, Any]],
        is_task_active: Optional[Callable[[], bool]] = None,
    ) -> ComputePolicy:
        settings = compute_settings_from_config(config)
        return cls(
            idle_seconds=settings["idle_seconds"],
            background_prefer_cloud=settings["background_prefer_cloud"],
            defer_local_when_slot_held=settings["defer_local_when_slot_held"],
            is_task_active=is_task_active,
        )
