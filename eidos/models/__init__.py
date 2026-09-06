"""Model subsystem for local and cloud inference."""

from eidos.models.base import BaseModelClient, ModelResponse, ToolCall
from eidos.models.chutes_client import ChutesClient
from eidos.models.ollama_client import OllamaClient
from eidos.models.router import ModelRouter, ProviderPreference

__all__ = [
    "BaseModelClient",
    "ModelResponse",
    "ToolCall",
    "OllamaClient",
    "ChutesClient",
    "ModelRouter",
    "ProviderPreference",
]
