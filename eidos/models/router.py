"""Cognitive model router for intelligent local-first execution with cloud escalation."""

from enum import Enum
import logging
from typing import Any, Dict, List, Literal, Optional

from eidos.models.base import BaseModelClient, ModelResponse
from eidos.models.chutes_client import ChutesClient
from eidos.models.compute_policy import ComputePolicy
from eidos.models.meta_client import MetaClient
from eidos.models.ollama_client import OllamaClient

logger = logging.getLogger(__name__)

RequestPriority = Literal["foreground", "background"]


class ProviderPreference(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"
    AUTO = "auto"


class ModelRouter(BaseModelClient):
    """
    Intelligent router that directs queries to local Ollama or a cloud provider.
    - LOCAL: Prioritizes local RTX 5060 Ti inference (zero API cost, total privacy).
    - CLOUD: Directs queries to the selected cloud provider (Chutes AI or Meta Muse).
    - AUTO: Local-first with automatic escalation to that cloud provider on high
            visual complexity or when local models encounter errors.
    """

    def __init__(
        self,
        ollama_client: Optional[OllamaClient] = None,
        chutes_client: Optional[ChutesClient] = None,
        meta_client: Optional[MetaClient] = None,
        preference: str = "local",
        cloud_provider: str = "chutes",
        compute_policy: Optional[ComputePolicy] = None,
    ):
        self.ollama = ollama_client or OllamaClient()
        self.chutes = chutes_client or ChutesClient()
        self.meta = meta_client or MetaClient()
        self.preference = ProviderPreference(preference.lower())
        self.cloud_provider = (cloud_provider or "chutes").strip().lower()
        self.last_provider_used: str = "local"
        self.compute_policy = compute_policy or ComputePolicy()

    def set_preference(self, preference: str):
        """Update provider preference ('local', 'cloud', 'auto')."""
        self.preference = ProviderPreference(preference.lower())

    def set_cloud_provider(self, provider: str):
        """Select which configured cloud API cloud and auto modes call ('chutes' or 'meta')."""
        self.cloud_provider = (provider or "chutes").strip().lower()

    def _clients(self):
        return (self.ollama, self.chutes, self.meta)

    def _configured_cloud(self):
        """Return the selected cloud client when its API key is set."""
        if self.cloud_provider == "meta":
            client, name = self.meta, "meta"
        else:
            client, name = self.chutes, "chutes"
        if client is not None and getattr(client, "is_configured", False):
            return client, name
        return None, name

    def configured_cloud_client(self):
        """The cloud client cloud mode, auto fallback, and error retry should call."""
        client, _name = self._configured_cloud()
        return client

    def _cloud_display_name(self) -> str:
        if self.cloud_provider == "meta":
            return "Meta Model API"
        return "Chutes AI"

    @property
    def reasoning_model(self) -> str:
        """Model id recorded for the active route (cloud when forced, otherwise local)."""
        if self.preference == ProviderPreference.CLOUD:
            client, _name = self._configured_cloud()
            if client is not None:
                return getattr(client, "reasoning_model", "") or ""
        return getattr(self.ollama, "reasoning_model", "") or ""

    @property
    def recall_interceptor(self):
        for client in self._clients():
            found = getattr(client, "recall_interceptor", None)
            if found is not None:
                return found
        return None

    @recall_interceptor.setter
    def recall_interceptor(self, value):
        for client in self._clients():
            if hasattr(client, "recall_interceptor"):
                client.recall_interceptor = value

    @property
    def on_recall_callback(self):
        for client in self._clients():
            found = getattr(client, "on_recall_callback", None)
            if found is not None:
                return found
        return None

    @on_recall_callback.setter
    def on_recall_callback(self, value):
        for client in self._clients():
            if hasattr(client, "on_recall_callback"):
                client.on_recall_callback = value

    def abort(self) -> None:
        """Close whichever provider is generating the current reply."""
        for client in self._clients():
            abort = getattr(client, "abort", None)
            if not callable(abort):
                continue
            try:
                abort()
            except Exception:
                logger.debug("Model abort failed", exc_info=True)

    def reset_abort(self) -> None:
        """Clear a previous stop on local and cloud providers."""
        for client in self._clients():
            reset = getattr(client, "reset_abort", None)
            if not callable(reset):
                continue
            try:
                reset()
            except Exception:
                logger.debug("Model abort reset failed", exc_info=True)

    def _should_skip_local_ollama(self, priority: RequestPriority) -> bool:
        policy = self.compute_policy
        if policy.should_defer_local_ollama(priority):
            return True
        return False

    def _chat_via_cloud(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        model_override: Optional[str],
    ) -> Optional[ModelResponse]:
        client, name = self._configured_cloud()
        if client is None:
            return None
        self.last_provider_used = name
        return client.chat(messages=messages, tools=tools, model_override=model_override)

    def _vision_via_cloud(
        self,
        prompt: str,
        image_base64: str,
        system_prompt: Optional[str],
        model_override: Optional[str],
    ) -> Optional[ModelResponse]:
        client, name = self._configured_cloud()
        if client is None:
            return None
        self.last_provider_used = name
        return client.vision_chat(
            prompt=prompt,
            image_base64=image_base64,
            system_prompt=system_prompt,
            model_override=model_override,
        )

    def chat_background(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model_override: Optional[str] = None,
    ) -> ModelResponse:
        """Background LLM (reflector, consolidator): prefer cloud when compute is busy."""
        return self.chat(
            messages=messages,
            tools=tools,
            model_override=model_override,
            priority="background",
        )

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model_override: Optional[str] = None,
        priority: RequestPriority = "foreground",
    ) -> ModelResponse:
        """Route chat request to appropriate provider."""
        cloud_name = self._cloud_display_name()
        if self.preference == ProviderPreference.CLOUD:
            cloud = self._chat_via_cloud(messages, tools, model_override)
            if cloud is not None:
                return cloud
            logger.warning(
                "Cloud preferred but %s API key not configured. Falling back to Ollama.",
                cloud_name,
            )

        if priority == "background" and self.compute_policy.should_use_cloud_for_background():
            cloud = self._chat_via_cloud(messages, tools, model_override)
            if cloud is not None:
                logger.info(
                    "Background chat using %s (local compute busy or prefer_cloud).",
                    cloud_name,
                )
                return cloud

        if self._should_skip_local_ollama(priority):
            cloud = self._chat_via_cloud(messages, tools, model_override)
            if cloud is not None:
                logger.info(
                    "Skipping local Ollama for %s chat; inference slot held or foreground busy.",
                    priority,
                )
                return cloud
            if priority == "background":
                return ModelResponse(
                    content="",
                    model=model_override or "local",
                    raw={"error": "local_busy", "skipped_ollama": True},
                )

        try:
            self.last_provider_used = "ollama"
            response = self.ollama.chat(messages=messages, tools=tools, model_override=model_override)
            if isinstance(response.raw, dict) and response.raw.get("cancelled"):
                return response

            has_error = (
                (bool(response.content) and response.content.startswith("Error communicating with Ollama"))
                or (isinstance(response.raw, dict) and bool(response.raw.get("error")))
            )

            if not has_error and (response.content or response.has_tool_calls or response.thinking):
                return response
            elif not has_error and self.preference != ProviderPreference.AUTO:
                return response

            if self.preference == ProviderPreference.AUTO:
                cloud = self._chat_via_cloud(messages, tools, model_override)
                if cloud is not None:
                    logger.info(
                        "Local Ollama model failed or reported error. Escalating chat to %s.",
                        cloud_name,
                    )
                    return cloud

            return response
        except Exception as e:
            if self.preference in (ProviderPreference.AUTO, ProviderPreference.CLOUD):
                cloud = self._chat_via_cloud(messages, tools, model_override)
                if cloud is not None:
                    logger.warning("Ollama exception (%s). Escalating to %s.", e, cloud_name)
                    return cloud
            raise

    def vision_chat(
        self,
        prompt: str,
        image_base64: str,
        system_prompt: Optional[str] = None,
        model_override: Optional[str] = None,
        dense_grounding: bool = False,
        priority: RequestPriority = "foreground",
    ) -> ModelResponse:
        """
        Route vision / visual grounding query.
        Cloud mode, and auto mode with dense grounding, use the selected cloud model.
        """
        cloud_name = self._cloud_display_name()
        use_cloud_first = self.preference == ProviderPreference.CLOUD or (
            self.preference == ProviderPreference.AUTO
            and dense_grounding
            and self.configured_cloud_client() is not None
        )
        if use_cloud_first:
            cloud = self._vision_via_cloud(prompt, image_base64, system_prompt, model_override)
            if cloud is not None:
                return cloud

        if priority == "background" and self.compute_policy.should_use_cloud_for_background():
            cloud = self._vision_via_cloud(prompt, image_base64, system_prompt, model_override)
            if cloud is not None:
                return cloud

        if self._should_skip_local_ollama(priority):
            cloud = self._vision_via_cloud(prompt, image_base64, system_prompt, model_override)
            if cloud is not None:
                logger.info("Skipping local Ollama vision; using %s.", cloud_name)
                return cloud

        try:
            self.last_provider_used = "ollama"
            response = self.ollama.vision_chat(
                prompt=prompt,
                image_base64=image_base64,
                system_prompt=system_prompt,
                model_override=model_override,
            )
            if isinstance(response.raw, dict) and response.raw.get("cancelled"):
                return response

            has_error = (
                (bool(response.content) and response.content.startswith("Error communicating with Ollama"))
                or (isinstance(response.raw, dict) and bool(response.raw.get("error")))
            )

            if not has_error and (response.content or response.has_tool_calls or response.thinking):
                return response
            elif not has_error and self.preference != ProviderPreference.AUTO:
                return response

            if self.preference == ProviderPreference.AUTO:
                cloud = self._vision_via_cloud(prompt, image_base64, system_prompt, model_override)
                if cloud is not None:
                    logger.info("Local vision returned error. Escalating to %s.", cloud_name)
                    return cloud

            return response
        except Exception as e:
            cloud = self._vision_via_cloud(prompt, image_base64, system_prompt, model_override)
            if cloud is not None:
                logger.warning("Local vision exception (%s). Escalating to %s.", e, cloud_name)
                return cloud
            raise
