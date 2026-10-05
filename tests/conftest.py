import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest

# Guard against headless / sandboxed test environments where X11 display connection fails
try:
    import pyautogui  # noqa: F401
except Exception:
    for mod in ["pyautogui", "mouseinfo", "Xlib", "Xlib.display", "Xlib.protocol", "Xlib.support", "Xlib.error"]:
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()

import eidos.memory.conversation_store as cs_mod
import eidos.memory.episodic_store as es_mod
import eidos.memory.trajectory_logger as tl_mod
import eidos.memory.user_store as us_mod


@pytest.fixture(autouse=True)
def isolate_eidos_environment(monkeypatch, tmp_path):
    """Ensure all tests operate in isolated tmp directories and do not touch ~/.eidos."""
    eidos_tmp = tmp_path / ".eidos"
    eidos_tmp.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(cs_mod, "DEFAULT_CONVERSATIONS_DB_PATH", eidos_tmp / "eidos.db")
    monkeypatch.setattr(es_mod, "DEFAULT_VECTOR_STORE_DIR", eidos_tmp / "memory" / "vector_store")
    monkeypatch.setattr(tl_mod, "DEFAULT_TRAJECTORIES_PATH", eidos_tmp / "trajectories" / "episodes.jsonl")
    monkeypatch.setattr(us_mod, "DEFAULT_USER_PROFILE_PATH", eidos_tmp / "user_profile.yaml")
