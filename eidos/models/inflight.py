"""Track the HTTP call that is generating a reply so Stop can close it.

Each agent run gets its own ticket. Stopping one cloud request closes that
socket and leaves another run's request alone. A kill closes every ticket.
"""

from contextvars import ContextVar
import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Set by AgentLoop for the worker that is inside run_task.
model_run_id: ContextVar[Optional[str]] = ContextVar("eidos_model_run_id", default=None)


class InflightRequest:
    """One in-flight model HTTP call.

    Stop sets the cancel flag and closes the live session or response so a
    blocked read returns instead of waiting for the model to finish.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session: Any = None
        self._response: Any = None
        self._cancelled = threading.Event()

    def reset(self) -> None:
        """Allow the next task to start a fresh request."""
        self._cancelled.clear()

    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def arm(self, session: Any) -> None:
        """Remember a session so Stop can close it before the response exists."""
        with self._lock:
            self._session = session
            cancel = self._cancelled.is_set()
        if cancel:
            self._close(session)

    def bind(self, response: Any) -> None:
        """Remember a streaming response so Stop can close the socket."""
        with self._lock:
            self._response = response
            cancel = self._cancelled.is_set()
        if cancel:
            self._close(response)

    def release(self, *, session: Any = None, response: Any = None) -> None:
        with self._lock:
            if session is not None and self._session is session:
                self._session = None
            if response is not None and self._response is response:
                self._response = None

    def abort(self) -> None:
        """Mark this call cancelled and close whatever is currently open."""
        self._cancelled.set()
        with self._lock:
            session = self._session
            response = self._response
        self._close(response)
        self._close(session)

    @staticmethod
    def _close(obj: Optional[Any]) -> None:
        if obj is None:
            return
        try:
            obj.close()
        except Exception:
            logger.debug("Failed to close in-flight model request", exc_info=True)


class InflightBoard:
    """Cancel tickets keyed by agent run.

    Cloud clients use one board so two API calls do not share a socket.
    ``abort(None)`` closes every ticket. ``abort(run_id)`` closes one.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tickets: Dict[str, InflightRequest] = {}

    def _key(self, run_id: Optional[str]) -> str:
        if run_id is not None:
            return run_id
        return model_run_id.get() or ""

    def current(self, run_id: Optional[str] = None) -> InflightRequest:
        key = self._key(run_id)
        with self._lock:
            ticket = self._tickets.get(key)
            if ticket is None:
                ticket = InflightRequest()
                self._tickets[key] = ticket
            return ticket

    def reset(self, run_id: Optional[str] = None) -> None:
        """Replace this run's ticket. Other runs keep theirs."""
        key = self._key(run_id)
        with self._lock:
            self._tickets[key] = InflightRequest()

    def abort(self, run_id: Optional[str] = None) -> None:
        if run_id is None:
            self.abort_all()
            return
        with self._lock:
            ticket = self._tickets.get(run_id)
        if ticket is not None:
            ticket.abort()

    def abort_all(self) -> None:
        with self._lock:
            tickets = list(self._tickets.values())
        for ticket in tickets:
            ticket.abort()

    def drop(self, run_id: str) -> None:
        with self._lock:
            self._tickets.pop(run_id, None)
