"""Append-only audit logger for EidosBot actions."""

from datetime import datetime
import json
import os
from pathlib import Path
import threading
from typing import Any, Dict, Optional


class AuditLogger:
    """Logs all agent actions, risk assessments, and user decisions to disk."""

    def __init__(self, log_path: Optional[str] = None):
        if log_path:
            self.file_path = Path(os.path.expanduser(log_path))
        else:
            self.file_path = Path(os.path.expanduser("~/.eidos/audit.log"))

        self._lock = threading.Lock()
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def log(
        self,
        action_type: str,
        details: Dict[str, Any],
        risk_level: int,
        decision: str,
        user_notes: str = "",
        result_status: Optional[str] = None,
        duration_ms: Optional[float] = None
    ):
        """Append a structured JSON line to the audit log."""
        record = {
            "timestamp": datetime.now().isoformat(),
            "action_type": action_type,
            "details": details,
            "risk_level": risk_level,
            "decision": decision,  # 'auto_allowed', 'user_approved', 'user_denied', 'allowlisted', 'killed'
            "user_notes": user_notes,
            "result_status": result_status,
            "duration_ms": duration_ms
        }

        line = json.dumps(record) + "\n"
        with self._lock:
            try:
                with open(self.file_path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass
