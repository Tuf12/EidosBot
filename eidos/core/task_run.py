"""One in-flight agent task.

A cloud run keeps its own stop flag, transcript, and scope so another
conversation can run at the same time. Local Ollama still admits one run.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import threading
from typing import Any, Dict, List, Optional


@dataclass
class TaskRun:
    run_id: str
    conversation_id: str
    stop: threading.Event
    history: List[Dict[str, Any]]
    scope: Any = None
    trace: Dict[str, Any] = field(default_factory=dict)
    finished: bool = False


current_run: ContextVar[Optional[TaskRun]] = ContextVar("eidos_task_run", default=None)
