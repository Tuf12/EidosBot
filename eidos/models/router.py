"""Cognitive model router for intelligent local-first execution with cloud escalation."""

from enum import Enum
import logging
from typing import Any, Dict, List, Optional

from eidos.models.base import BaseModelClient, ModelResponse
from eidos.models.chutes_client import ChutesClient
from eidos.models.ollama_client import OllamaClient

logger = logging.getLogger(__name__)


class ProviderPreference(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"
    AUTO = "auto"


class ModelRouter(BaseModelClient):
    """
    Intelligent router that directs queries to local Ollama or cloud Chutes AI.
    - LOCAL: Prioritizes local RTX 5060 Ti inference (zero API cost, total privacy).
    - CLOUD: Directs queries to Chutes AI (Qwen3.5-397B, Kimi K2.6, Gemma 4 31B Turbo).
    - AUTO: Local-first with automatic escalation to Chutes on high visual complexity
            or when local models encounter errors.
    """

    def __init__(
        self,
        ollama_client: Optional[OllamaClient] = None,
        chutes_client: Optional[ChutesClient] = None,
        preference: str = "local",
    ):
        self.ollama = ollama_client or OllamaClient()
        self.chutes = chutes_client or ChutesClient()
        self.preference = ProviderPreference(preference.lower())
        self.last_provider_used: str = "local"

    def set_preference(self, preference: str):
        """Update provider preference ('local', 'cloud', 'auto')."""
        self.preference = ProviderPreference(preference.lower())

    @property
    def recall_interceptor(self):
        return getattr(self.ollama, "recall_interceptor", None) or getattr(self.chutes, "recall_interceptor", None)

    @recall_interceptor.setter
    def recall_interceptor(self, value):
        if hasattr(self.ollama, "recall_interceptor"):
            self.ollama.recall_interceptor = value
        if hasattr(self.chutes, "recall_interceptor"):
            self.chutes.recall_interceptor = value

    @property
    def on_recall_callback(self):
        return getattr(self.ollama, "on_recall_callback", None) or getattr(self.chutes, "on_recall_callback", None)

    @on_recall_callback.setter
    def on_recall_callback(self, value):
        if hasattr(self.ollama, "on_recall_callback"):
            self.ollama.on_recall_callback = value
        if hasattr(self.chutes, "on_recall_callback"):
            self.chutes.on_recall_callback = value

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model_override: Optional[str] = None,
    ) -> ModelResponse:
        """Route chat request to appropriate provider."""
        # 1. Cloud-only requested
        if self.preference == ProviderPreference.CLOUD:
            if self.chutes.is_configured:
                self.last_provider_used = "chutes"
                return self.chutes.chat(messages=messages, tools=tools, model_override=model_override)
            else:
                logger.warning("Cloud preferred but Chutes API key not configured. Falling back to Ollama.")

        # 2. Local-only or Auto: start with Local Ollama
        try:
            self.last_provider_used = "ollama"
            response = self.ollama.chat(messages=messages, tools=tools, model_override=model_override)

            # Check if local response succeeded
            if response.content and not response.content.startswith("Error communicating with Ollama"):
                return response
            elif response.has_tool_calls:
                return response

            # If failed and AUTO mode is enabled with Chutes configured -> Escalate
            if self.preference == ProviderPreference.AUTO and self.chutes.is_configured:
                logger.info("Local Ollama model failed or reported error. Escalating chat to Chutes AI.")
                self.last_provider_used = "chutes"
                return self.chutes.chat(messages=messages, tools=tools, model_override=model_override)

            return response
        except Exception as e:
            if self.preference in (ProviderPreference.AUTO, ProviderPreference.CLOUD) and self.chutes.is_configured:
                logger.warning("Ollama exception (%s). Escalating to Chutes AI.", e)
                self.last_provider_used = "chutes"
                return self.chutes.chat(messages=messages, tools=tools, model_override=model_override)
            raise

    def vision_chat(
        self,
        prompt: str,
        image_base64: str,
        system_prompt: Optional[str] = None,
        model_override: Optional[str] = None,
        dense_grounding: bool = False,
    ) -> ModelResponse:
        """
        Route vision / visual grounding query.
        If dense_grounding is True and AUTO mode is on, escalates to Chutes Qwen3.5-397B MoE.
        """
        if self.preference == ProviderPreference.CLOUD or (
            self.preference == ProviderPreference.AUTO and dense_grounding and self.chutes.is_configured
        ):
            if self.chutes.is_configured:
                self.last_provider_used = "chutes"
                return self.chutes.vision_chat(
                    prompt=prompt,
                    image_base64=image_base64,
                    system_prompt=system_prompt,
                    model_override=model_override,
                )

        # Attempt local vision model first
        try:
            self.last_provider_used = "ollama"
            response = self.ollama.vision_chat(
                prompt=prompt,
                image_base64=image_base64,
                system_prompt=system_prompt,
                model_override=model_override,
            )

            # Check if local vision gave meaningful output
            if response.content and not response.content.startswith("Error communicating with Ollama"):
                return response

            # Escalate if local vision failed
            if self.preference == ProviderPreference.AUTO and self.chutes.is_configured:
                logger.info("Local vision returned error. Escalating to Chutes AI.")
                self.last_provider_used = "chutes"
                return self.chutes.vision_chat(
                    prompt=prompt,
                    image_base64=image_base64,
                    system_prompt=system_prompt,
                    model_override=model_override,
                )

            return response
        except Exception as e:
            if self.chutes.is_configured:
                logger.warning("Local vision exception (%s). Escalating to Chutes AI.", e)
                self.last_provider_used = "chutes"
                return self.chutes.vision_chat(
                    prompt=prompt,
                    image_base64=image_base64,
                    system_prompt=system_prompt,
                    model_override=model_override,
                )
            raise
