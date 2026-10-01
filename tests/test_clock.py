"""Tests for EidosBot internal clock."""

from datetime import datetime
from zoneinfo import ZoneInfo

from eidos.core.clock import EidosClock
from eidos.core.context import build_system_prompt


def test_clock_snapshot_fields():
    clock = EidosClock(timezone_name="UTC")
    snap = clock.snapshot()

    assert snap.date
    assert snap.time
    assert snap.weekday
    assert snap.timezone == "UTC"
    assert snap.unix_timestamp > 0


def test_clock_respects_configured_timezone():
    clock = EidosClock(timezone_name="America/Chicago")
    assert clock.timezone_name == "America/Chicago"
    assert clock.now().tzinfo == ZoneInfo("America/Chicago")


def test_format_for_prompt_includes_date_parts():
    clock = EidosClock(timezone_name="UTC")
    formatted = clock.format_for_prompt()

    assert any(char.isdigit() for char in formatted)
    assert len(formatted) > 10


def test_build_system_prompt_includes_current_time():
    prompt = build_system_prompt(
        {"title": "Test", "class_name": "test", "geometry": {}},
        "/tmp",
        clock=EidosClock(timezone_name="UTC"),
    )

    assert "Current Date & Time:" in prompt
    assert "UTC" in prompt
    assert "internal clock" in prompt.lower()


def test_snapshot_to_dict_roundtrip():
    clock = EidosClock(timezone_name="UTC")
    data = clock.snapshot().to_dict()

    assert set(data.keys()) == {"iso", "date", "time", "weekday", "timezone", "unix_timestamp"}
    datetime.fromisoformat(data["iso"])
