"""Target application integrations and external bridges."""

from eidos.integrations.cursor_ide import CursorAdapter
from eidos.integrations.optimalx import OptimalXBridge
from eidos.integrations.web_research import WebResearchEngine

__all__ = ["OptimalXBridge", "CursorAdapter", "WebResearchEngine"]
