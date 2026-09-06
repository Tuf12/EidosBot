"""Model subsystem for local and cloud inference."""

from eidos.models.base import BaseModelClient, ModelResponse, ToolCall
from eidos.models.ollama_client import OllamaClient

__all__ = ["BaseModelClient", "ModelResponse", "ToolCall", "OllamaClient"]
