"""Helper utilities for locating local Kokoro TTS and Parakeet STT runtimes.

EidosBot shares OptimalX Desktop's voice stack: the same ``~/OptimalX/voice/venv``
(or ``~/.venvs/optimalx-voice``) Python and the same ``speak.py`` / ``speak_worker.py``
scripts. Run OptimalX ``voice/python/setup.sh`` once; both apps can use that venv.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import List, Optional

logger = logging.getLogger(__name__)

# eidos/voice/helpers.py → repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]


def voice_python_candidates() -> List[Path]:
    """Same order as OptimalX electron/voice/paths.js (resolvePythonBin)."""
    return [
        Path.home() / "OptimalX" / "voice" / "venv" / "bin" / "python",
        Path.home() / ".venvs" / "optimalx-voice" / "bin" / "python",
    ]


def voice_scripts_candidates() -> List[Path]:
    """Directories that contain speak.py + transcribe.py (Kokoro + Parakeet CLIs)."""
    return [
        _REPO_ROOT / "OptimalX_Voice" / "voice" / "python",
        Path.home() / "OptimalX" / "electron" / "voice" / "python",
        Path.home() / "Projects" / "OptimalXDesktop1.0" / "electron" / "voice" / "python",
        Path.home() / "OptimalXDesktop1.0" / "electron" / "voice" / "python",
    ]


@dataclass(frozen=True)
class VoiceRuntimePaths:
    """Resolved OptimalX-compatible voice Python + script directory."""

    python_path: str
    scripts_dir: Optional[str]
    python_auto_detected: bool
    scripts_auto_detected: bool

    @property
    def is_ready(self) -> bool:
        if not self.scripts_dir:
            return False
        scripts = Path(self.scripts_dir)
        speak = scripts / "speak.py"
        if not speak.exists() or not os.path.exists(self.python_path):
            return False
        return os.access(self.python_path, os.X_OK)


def resolve_voice_runtime(
    configured_python: Optional[str] = None,
    configured_scripts_dir: Optional[str] = None,
) -> VoiceRuntimePaths:
    """Resolve shared OptimalX voice venv + Kokoro/Parakeet script directory."""
    python_auto = True
    scripts_auto = True

    if configured_python:
        python_auto = False
        path = Path(os.path.expanduser(configured_python))
        if path.exists() and os.access(path, os.X_OK):
            python_path = str(path)
        else:
            logger.warning("Configured voice python not found or not executable: %s", configured_python)
            python_path = _find_voice_python_auto()
            python_auto = True
    else:
        python_path = _find_voice_python_auto()

    if configured_scripts_dir:
        scripts_auto = False
        scripts_path = Path(os.path.expanduser(configured_scripts_dir)).resolve()
        if (scripts_path / "speak.py").exists() and (scripts_path / "transcribe.py").exists():
            scripts_dir: Optional[str] = str(scripts_path)
        else:
            logger.warning("Configured voice scripts directory invalid: %s", configured_scripts_dir)
            scripts_dir = _find_voice_scripts_auto()
            scripts_auto = True
    else:
        scripts_dir = _find_voice_scripts_auto()

    runtime = VoiceRuntimePaths(
        python_path=python_path,
        scripts_dir=scripts_dir,
        python_auto_detected=python_auto,
        scripts_auto_detected=scripts_auto,
    )
    if runtime.is_ready:
        logger.info(
            "Voice runtime (shared with OptimalX): python=%s scripts=%s",
            runtime.python_path,
            runtime.scripts_dir,
        )
    else:
        logger.warning(
            "OptimalX voice runtime not fully available (python=%s, scripts=%s). "
            "Run OptimalX voice/python/setup.sh or set voice.python_path and "
            "voice.optimalx_voice_dir in config.yaml.",
            runtime.python_path,
            runtime.scripts_dir,
        )
    return runtime


def _find_voice_python_auto() -> str:
    for candidate in voice_python_candidates():
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    logger.warning(
        "No OptimalX voice venv found (%s or %s); falling back to %s. "
        "Kokoro/Parakeet require the OptimalX voice venv.",
        voice_python_candidates()[0],
        voice_python_candidates()[1],
        sys.executable,
    )
    return sys.executable


def _find_voice_scripts_auto() -> Optional[str]:
    for candidate in voice_scripts_candidates():
        if (candidate / "speak.py").exists() and (candidate / "transcribe.py").exists():
            return str(candidate.resolve())
    return None


def find_voice_python(configured_path: Optional[str] = None) -> str:
    """Find the OptimalX voice venv Python (Kokoro + Parakeet)."""
    return resolve_voice_runtime(configured_python=configured_path).python_path


def find_voice_scripts_dir(configured_dir: Optional[str] = None) -> Optional[str]:
    """Locate speak.py / transcribe.py (OptimalX voice/python tree)."""
    return resolve_voice_runtime(configured_scripts_dir=configured_dir).scripts_dir
