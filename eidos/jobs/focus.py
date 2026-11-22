"""Job focus manager for persistent primary and attached project jobs (PROJECT_JOBS.md)."""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Dict, List, Optional, Union
import yaml

logger = logging.getLogger(__name__)


class JobFocusManager:
    """Manages agent-wide focus state persisted in focus.yaml."""

    def __init__(self, jobs_dir: Union[str, Path]):
        self.jobs_dir = Path(os.path.abspath(os.path.expanduser(jobs_dir)))
        self.focus_file = self.jobs_dir / "focus.yaml"
        self._lock = threading.RLock()

    def get_focus(self) -> Dict[str, Any]:
        """Read and return current focus state from focus.yaml."""
        with self._lock:
            if not self.focus_file.is_file():
                return {"primary": None, "attached": [], "integration_focus": None}
            try:
                with open(self.focus_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if isinstance(data, dict):
                        primary = data.get("primary")
                        raw_att = data.get("attached") or []
                        attached = [str(a) for a in raw_att if a]
                        int_focus = data.get("integration_focus")
                        return {
                            "primary": str(primary) if primary else None,
                            "attached": attached,
                            "integration_focus": str(int_focus) if int_focus else None,
                        }
            except Exception as e:
                logger.debug("Failed reading focus.yaml at %s: %s", self.focus_file, e)
            return {"primary": None, "attached": [], "integration_focus": None}

    def get_primary(self) -> Optional[str]:
        """Return the active primary job_id or path, if any."""
        return self.get_focus().get("primary")

    def get_attached(self) -> List[str]:
        """Return the list of attached job IDs."""
        return self.get_focus().get("attached", [])

    def get_integration_focus(self) -> Optional[str]:
        """Return active standalone integration focus, if any (PROJECT_JOBS.md Section 13.2)."""
        return self.get_focus().get("integration_focus")

    def save_focus(
        self,
        primary: Optional[str],
        attached: Optional[List[str]] = None,
        integration_focus: Optional[str] = None,
    ) -> None:
        """Atomically persist focus state to focus.yaml."""
        with self._lock:
            self.jobs_dir.mkdir(parents=True, exist_ok=True)
            clean_att = [str(a) for a in (attached or []) if a and str(a) != str(primary)]
            # Standalone integration focus stays empty while a project job is primary
            if primary:
                integration_focus = None

            payload = {
                "primary": str(primary) if primary else None,
                "attached": clean_att,
                "integration_focus": str(integration_focus) if integration_focus else None,
            }
            tmp_fd, tmp_path = tempfile.mkstemp(
                prefix="focus_", suffix=".tmp", dir=str(self.jobs_dir)
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    yaml.dump(payload, f, sort_keys=False, allow_unicode=True)
                os.replace(tmp_path, self.focus_file)
            except Exception as e:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                logger.error("Failed to save focus.yaml to %s: %s", self.focus_file, e)
                raise

    def set_primary(self, job_id_or_path: Optional[str]) -> None:
        """Set or switch the primary job, moving previous primary to attached if distinct."""
        with self._lock:
            current = self.get_focus()
            old_primary = current.get("primary")
            attached = current.get("attached", [])
            new_primary = str(job_id_or_path) if job_id_or_path else None

            # If switching to a new primary, add old primary to attached list
            if old_primary and new_primary and old_primary != new_primary:
                if old_primary not in attached:
                    attached.insert(0, old_primary)

            # Ensure new primary is not in attached list
            if new_primary and new_primary in attached:
                attached.remove(new_primary)

            self.save_focus(primary=new_primary, attached=attached, integration_focus=None)

    def set_integration_focus(self, integration_name: Optional[str]) -> None:
        """Set standalone integration focus when no project job is open (PROJECT_JOBS.md Section 13.2)."""
        with self._lock:
            current = self.get_focus()
            attached = current.get("attached", [])
            name_clean = str(integration_name).strip() if integration_name else None
            # Standalone integration focus clears primary job
            self.save_focus(primary=None, attached=attached, integration_focus=name_clean)

    def clear_integration_focus(self) -> None:
        """Clear standalone integration focus."""
        with self._lock:
            current = self.get_focus()
            self.save_focus(
                primary=current.get("primary"),
                attached=current.get("attached", []),
                integration_focus=None,
            )

    def clear_primary(self) -> None:
        """Clear primary focus without removing attached jobs."""
        with self._lock:
            current = self.get_focus()
            self.save_focus(
                primary=None,
                attached=current.get("attached", []),
                integration_focus=current.get("integration_focus"),
            )

    def clear_all(self) -> None:
        """Clear both primary job and standalone integration focus."""
        with self._lock:
            current = self.get_focus()
            self.save_focus(primary=None, attached=current.get("attached", []), integration_focus=None)

    def attach(self, job_id_or_path: str) -> None:
        """Attach an additional job to focus state."""
        with self._lock:
            current = self.get_focus()
            primary = current.get("primary")
            target = str(job_id_or_path)
            if target == primary:
                return
            attached = current.get("attached", [])
            if target not in attached:
                attached.append(target)
            self.save_focus(primary=primary, attached=attached)

    def detach(self, job_id_or_path: str) -> None:
        """Detach a job from attached list."""
        with self._lock:
            current = self.get_focus()
            target = str(job_id_or_path)
            attached = [a for a in current.get("attached", []) if a != target]
            self.save_focus(primary=current.get("primary"), attached=attached)
