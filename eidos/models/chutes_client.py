"""Chutes AI cloud client for accelerated vision grounding and complex planning."""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import requests

from eidos.models.base import BaseModelClient, ModelResponse, ToolCall

logger = logging.getLogger(__name__)


class ChutesClient(BaseModelClient):
    """
    Client for Chutes AI endpoints (https://llm.chutes.ai/v1).
    Provides access to open-weight models (e.g. Qwen3.5-397B MoE, Gemma 4 31B Turbo, Kimi K2.6)
    with OpenAI-compatible chat, tool calling, and multimodal vision support.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://llm.chutes.ai/v1",
        vision_model: str = "Qwen/Qwen3.5-397B-A17B-TEE",
        fast_vision_model: str = "google/gemma-4-31B-turbo-TEE",
        reasoning_model: str = "moonshotai/Kimi-K2.6-TEE",
        timeout: float = 90.0,
    ):
        self.api_key = api_key or os.environ.get("CHUTES_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.vision_model = vision_model
        self.fast_vision_model = fast_vision_model
        self.reasoning_model = reasoning_model
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        """Check if an API key has been supplied."""
        return bool(self.api_key and self.api_key != "YOUR_CHUTES_API_KEY_HERE")

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def check_connection(self) -> bool:
        """Verify Chutes AI connectivity and authentication."""
        if not self.is_configured:
            return False
        try:
            resp = requests.get(
                f"{self.base_url}/models",
                headers=self._get_headers(),
                timeout=5.0
            )
            return resp.status_code == 200
        except Exception as e:
            logger.debug("Chutes connection check failed: %s", e)
            return False

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model_override: Optional[str] = None,
    ) -> ModelResponse:
        """Send chat completion request to Chutes AI."""
        if not self.is_configured:
            return ModelResponse(
                content="Chutes API key not configured. Please set chutes.api_key in config.yaml or CHUTES_API_KEY env var.",
                model=model_override or self.reasoning_model,
                raw={"error": "missing_api_key"}
            )

        model_name = model_override or self.reasoning_model
        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self._get_headers(),
                json=payload,
                timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()
            return self._parse_openai_response(data, model_name)
        except Exception as e:
            logger.error("Chutes chat error with model %s: %s", model_name, e)
            return ModelResponse(
                content=f"Error communicating with Chutes AI: {str(e)}",
                model=model_name,
                raw={"error": str(e)}
            )

    def vision_chat(
        self,
        prompt: str,
        image_base64: str,
        system_prompt: Optional[str] = None,
        model_override: Optional[str] = None,
        fast_mode: bool = False,
    ) -> ModelResponse:
        """Send multimodal visual query to Chutes AI."""
        if not self.is_configured:
            return ModelResponse(
                content="Chutes API key not configured.",
                model=model_override or (self.fast_vision_model if fast_mode else self.vision_model),
                raw={"error": "missing_api_key"}
            )

        model_name = model_override or (self.fast_vision_model if fast_mode else self.vision_model)

        # Build OpenAI multimodal content
        image_url = (
            image_base64
            if image_base64.startswith("data:")
            else f"data:image/jpeg;base64,{image_base64}"
        )

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        user_content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]
        messages.append({"role": "user", "content": user_content})

        payload = {
            "model": model_name,
            "messages": messages,
            "stream": False,
        }

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self._get_headers(),
                json=payload,
                timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()
            return self._parse_openai_response(data, model_name)
        except Exception as e:
            logger.error("Chutes vision error with model %s: %s", model_name, e)
            return ModelResponse(
                content=f"Error communicating with Chutes AI vision model: {str(e)}",
                model=model_name,
                raw={"error": str(e)}
            )

    def _parse_openai_response(self, data: Dict[str, Any], default_model: str) -> ModelResponse:
        """Parse standard OpenAI API response."""
        choices = data.get("choices", [])
        if not choices:
            return ModelResponse(content="", model=default_model, raw=data)

        choice = choices[0]
        msg = choice.get("message", {})
        content = msg.get("content", "") or ""
        model_used = data.get("model", default_model)

        thinking = ""
        # Check for reasoning_content or thinking tags
        if "reasoning_content" in msg and msg["reasoning_content"]:
            thinking = msg["reasoning_content"]
        else:
            thought_match = re.search(r"<(?:thought|thinking)>(.*?)</(?:thought|thinking)>", content, re.DOTALL)
            if thought_match:
                thinking = thought_match.group(1).strip()
                content = re.sub(r"<(?:thought|thinking)>.*?</(?:thought|thinking)>", "", content, flags=re.DOTALL).strip()

        tool_calls: List[ToolCall] = []
        raw_tools = msg.get("tool_calls", [])
        if raw_tools and isinstance(raw_tools, list):
            for t in raw_tools:
                fn = t.get("function", {})
                name = fn.get("name", "")
                args_str = fn.get("arguments", "{}")
                if isinstance(args_str, str):
                    try:
                        args = json.loads(args_str)
                    except Exception:
                        args = {"raw_input": args_str}
                elif isinstance(args_str, dict):
                    args = args_str
                else:
                    args = {}

                if name:
                    tool_calls.append(ToolCall(name=name, arguments=args, id=t.get("id")))

        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            thinking=thinking,
            model=model_used,
            raw=data
        )
