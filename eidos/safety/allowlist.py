"""Persistent command allowlist storage for EidosBot."""

from datetime import datetime
import json
import os
from pathlib import Path
import threading
from typing import Dict, List, Optional

from eidos.safety.risk_classifier import RiskClassifier, RiskLevel


class AllowlistManager:
    """Thread-safe persistent manager for user-approved Level 2 commands."""

    def __init__(self, storage_path: Optional[str] = None):
        if storage_path:
            self.file_path = Path(os.path.expanduser(storage_path))
        else:
            self.file_path = Path(os.path.expanduser("~/.eidos/allowlist.json"))

        self._lock = threading.Lock()
        self._ensure_storage_dir()
        self._cache: Dict[str, dict] = self._load()

    def _ensure_storage_dir(self):
        """Create storage directory if it doesn't exist."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> Dict[str, dict]:
        """Load allowlist from disk."""
        if not self.file_path.exists():
            return {}
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self):
        """Save current cache to disk atomically."""
        temp_path = self.file_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, indent=2)
        temp_path.replace(self.file_path)

    def is_allowed(self, command: str) -> bool:
        """Check if command is explicitly allowed."""
        cmd_clean = command.strip()
        if not cmd_clean:
            return False

        with self._lock:
            # Check exact match
            if cmd_clean in self._cache:
                return True
            
            # Check prefix/pattern match (e.g. if 'git status' or 'python3' is allowed)
            for allowed_cmd, entry in self._cache.items():
                if entry.get("allow_prefix", False) and cmd_clean.startswith(allowed_cmd):
                    return True

        return False

    def add(self, command: str, allow_prefix: bool = False, description: str = "") -> bool:
        """Add command to allowlist. Refuses Level 3 destructive commands."""
        cmd_clean = command.strip()
        if not cmd_clean:
            return False

        risk, reason = RiskClassifier.classify_shell_command(cmd_clean)
        if risk == RiskLevel.LEVEL_3_DESTRUCTIVE:
            raise ValueError(f"Cannot allowlist Level 3 destructive command: {reason}")

        now = datetime.now().isoformat()
        with self._lock:
            self._cache[cmd_clean] = {
                "command": cmd_clean,
                "allow_prefix": allow_prefix,
                "description": description or reason,
                "created_at": now,
                "last_used": None,
                "run_count": 0
            }
            self._save()
        return True

    def revoke(self, command: str) -> bool:
        """Revoke permission for a command."""
        cmd_clean = command.strip()
        with self._lock:
            if cmd_clean in self._cache:
                del self._cache[cmd_clean]
                self._save()
                return True
        return False

    def record_usage(self, command: str):
        """Record usage count and timestamp for allowed command."""
        cmd_clean = command.strip()
        with self._lock:
            entry = self._cache.get(cmd_clean)
            if not entry:
                # Check prefix
                for k, v in self._cache.items():
                    if v.get("allow_prefix", False) and cmd_clean.startswith(k):
                        entry = v
                        break
            if entry:
                entry["last_used"] = datetime.now().isoformat()
                entry["run_count"] = entry.get("run_count", 0) + 1
                self._save()

    def get_all(self) -> List[dict]:
        """Return list of all allowlisted commands."""
        with self._lock:
            return list(self._cache.values())

    def clear(self):
        """Clear all entries."""
        with self._lock:
            self._cache.clear()
            self._save()
