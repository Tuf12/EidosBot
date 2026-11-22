"""Target application integrations and external bridges."""

from eidos.integrations.mcp_client import MCPClientManager
from eidos.integrations.mcp_config import IntegrationRecord, MCPConfigManager, MCPServerConfig
from eidos.integrations.optimalx import OptimalXBridge
from eidos.integrations.web_research import WebResearchEngine

__all__ = [
    "IntegrationRecord",
    "MCPClientManager",
    "MCPConfigManager",
    "MCPServerConfig",
    "OptimalXBridge",
    "WebResearchEngine",
]


