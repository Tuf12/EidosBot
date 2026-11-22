"""Context-Aware Dynamic Task Scoping Resolver.

Detects ambient environmental execution context (Git repository, workspace root,
working directory, active GUI window, active skill, project job, and integration)
to construct runtime TaskScope boundaries with zero neural or GPU overhead.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Union

from eidos.memory.types import TaskScope

logger = logging.getLogger(__name__)


class ScopeResolver:
    """Detects and resolves environmental execution boundaries."""

    _git_root_cache: Dict[str, Optional[Path]] = {}

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the cached Git root resolutions."""
        cls._git_root_cache.clear()

    @classmethod
    def detect_git_root(cls, path: Union[str, Path, None] = None) -> Optional[Path]:
        """Ascend the directory tree to locate the nearest .git root directory or worktree file.

        Results are cached per resolved directory path to eliminate redundant filesystem I/O.
        """
        if path is None:
            raw_path = os.getcwd()
        else:
            raw_path = str(path)

        try:
            p = Path(raw_path).expanduser()
            if p.exists():
                p = p.resolve()
        except Exception as e:
            logger.debug("Failed to resolve path for git detection '%s': %s", raw_path, e)
            p = Path(raw_path).expanduser()

        cache_key = str(p)
        if cache_key in cls._git_root_cache:
            return cls._git_root_cache[cache_key]

        try:
            curr = p if p.is_dir() else p.parent
        except Exception:
            curr = p

        git_root: Optional[Path] = None
        try:
            for directory in [curr] + list(curr.parents):
                git_marker = directory / ".git"
                if git_marker.exists():  # Handles both .git directory and .git file (submodules/worktrees)
                    git_root = directory
                    break
        except (PermissionError, OSError) as e:
            logger.debug("Filesystem traversal restricted during git root detection at '%s': %s", curr, e)

        cls._git_root_cache[cache_key] = git_root
        return git_root

    @classmethod
    def detect_project_name(cls, path: Union[str, Path, None] = None) -> Optional[str]:
        """Detect Git root project name or nearest top-level project directory name."""
        git_root = cls.detect_git_root(path)
        if git_root is not None:
            return git_root.name

        if path is None:
            raw_path = os.getcwd()
        else:
            raw_path = str(path)

        try:
            p = Path(raw_path).expanduser()
            if p.exists():
                p = p.resolve()
            curr = p if p.is_dir() else p.parent
            name = curr.name.strip()
            return name if name else None
        except Exception as e:
            logger.debug("Failed to extract fallback directory project name for '%s': %s", raw_path, e)
            return None

    @classmethod
    def resolve_current_scope(
        cls,
        cwd: Union[str, Path, None] = None,
        active_window: Optional[Dict[str, Any]] = None,
        active_skill: Optional[Union[Dict[str, Any], str]] = None,
        active_job: Optional[Dict[str, Any]] = None,
    ) -> TaskScope:
        """Construct TaskScope from runtime desktop sensors, working directory, and active jobs."""
        if cwd is None:
            raw_cwd = os.getcwd()
        else:
            raw_cwd = str(cwd)

        try:
            p_cwd = Path(raw_cwd).expanduser()
            if p_cwd.exists():
                p_cwd = p_cwd.resolve()
            cwd_str = str(p_cwd)
        except Exception:
            cwd_str = str(raw_cwd)
            p_cwd = Path(raw_cwd)

        git_root = cls.detect_git_root(p_cwd)
        if git_root is not None:
            project_name = git_root.name
            workspace_path = str(git_root)
        else:
            project_name = cls.detect_project_name(p_cwd)
            workspace_path = cwd_str if p_cwd.exists() else None

        # Window sensing extraction
        window_class: Optional[str] = None
        window_title: Optional[str] = None
        if isinstance(active_window, dict):
            raw_class = (
                active_window.get("class_name")
                or active_window.get("wm_class")
                or active_window.get("window_class")
            )
            if raw_class and isinstance(raw_class, str) and raw_class.strip():
                window_class = raw_class.strip()

            raw_title = active_window.get("title") or active_window.get("window_title")
            if raw_title and isinstance(raw_title, str) and raw_title.strip():
                window_title = raw_title.strip()

        # Active skill extraction
        skill_name: Optional[str] = None
        if isinstance(active_skill, dict):
            raw_skill = active_skill.get("name")
            if raw_skill and isinstance(raw_skill, str) and raw_skill.strip():
                skill_name = raw_skill.strip()
        elif isinstance(active_skill, str) and active_skill.strip():
            skill_name = active_skill.strip()

        # Active job and integration extraction
        job_id: Optional[str] = None
        integration: Optional[str] = None
        if isinstance(active_job, dict):
            raw_job_id = active_job.get("job_id") or active_job.get("id")
            if raw_job_id and isinstance(raw_job_id, str) and raw_job_id.strip():
                job_id = raw_job_id.strip()

            raw_integration = active_job.get("integration") or active_job.get("app")
            if isinstance(raw_integration, list) and raw_integration:
                first_int = raw_integration[0]
                if isinstance(first_int, str) and first_int.strip():
                    integration = first_int.strip()
            elif isinstance(raw_integration, str) and raw_integration.strip():
                integration = raw_integration.strip()
            elif active_job.get("integrations") and isinstance(active_job["integrations"], list):
                first_int = active_job["integrations"][0]
                if isinstance(first_int, dict) and first_int.get("name"):
                    integration = str(first_int["name"]).strip()
                elif isinstance(first_int, str) and first_int.strip():
                    integration = first_int.strip()

        return TaskScope(
            project_name=project_name,
            workspace_path=workspace_path,
            cwd=cwd_str,
            active_skill=skill_name,
            window_class=window_class,
            window_title=window_title,
            job_id=job_id,
            integration=integration,
        )
