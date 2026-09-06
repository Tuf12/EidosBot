"""Base data structures and interfaces for model interactions."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolCall:
    """Represents a tool requested by the model."""
    name: str
    arguments: Dict[str, Any]
    id: Optional[str] = None


@dataclass
class ModelResponse:
    """Standardized response from an LLM."""
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    thinking: str = ""
    model: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class BaseModelClient:
    """Abstract interface for local and cloud models."""

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model_override: Optional[str] = None
    ) -> ModelResponse:
        raise NotImplementedError

    def vision_chat(
        self,
        prompt: str,
        image_base64: str,
        system_prompt: Optional[str] = None,
        model_override: Optional[str] = None
    ) -> ModelResponse:
        raise NotImplementedError
