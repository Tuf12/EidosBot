"""Internal clock for EidosBot — provides always-current local date and time."""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class ClockSnapshot:
    """Point-in-time view of the system clock."""

    iso: str
    date: str
    time: str
    weekday: str
    timezone: str
    unix_timestamp: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EidosClock:
    """Tracks the current local date and time for agent context and tools."""

    def __init__(self, timezone_name: Optional[str] = None):
        self._tz = self._resolve_timezone(timezone_name)

    @staticmethod
    def _resolve_timezone(timezone_name: Optional[str]) -> ZoneInfo:
        if timezone_name:
            try:
                return ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError:
                pass
        local_tz = datetime.now().astimezone().tzinfo
        if isinstance(local_tz, ZoneInfo):
            return local_tz
        if local_tz is not None:
            key = getattr(local_tz, "key", None) or str(local_tz)
            try:
                return ZoneInfo(key)
            except ZoneInfoNotFoundError:
                pass
        return ZoneInfo("UTC")

    @property
    def timezone_name(self) -> str:
        return str(self._tz)

    def now(self) -> datetime:
        return datetime.now(self._tz)

    def snapshot(self) -> ClockSnapshot:
        """Return a structured snapshot of the current date and time."""
        current = self.now()
        return ClockSnapshot(
            iso=current.isoformat(timespec="seconds"),
            date=current.strftime("%Y-%m-%d"),
            time=current.strftime("%H:%M:%S"),
            weekday=current.strftime("%A"),
            timezone=self.timezone_name,
            unix_timestamp=current.timestamp(),
        )

    def format_for_prompt(self) -> str:
        """Human-readable date/time string for LLM system prompts."""
        current = self.now()
        return current.strftime("%A, %B %d, %Y at %I:%M %p %Z")
