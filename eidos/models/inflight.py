"""Track the HTTP call that is generating a reply so Stop can close it."""

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)


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
