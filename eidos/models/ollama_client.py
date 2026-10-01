"""Ollama client for local multi-model execution with tool calling."""

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

import requests

from eidos.models.base import BaseModelClient, ModelResponse, ToolCall

logger = logging.getLogger(__name__)


class OllamaClient(BaseModelClient):
    """Client for local Ollama instances supporting tool calling and vision."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        reasoning_model: str = "batiai/gemma4-26b:iq4",
        vision_model: str = "batiai/gemma4-12b:q6",
        chat_model: str = "gpt-oss:20b",
        timeout: float = 60.0,
        recall_interceptor: Optional[Any] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.reasoning_model = reasoning_model
        self.vision_model = vision_model
        self.chat_model = chat_model
        self.timeout = timeout
        self.recall_interceptor = recall_interceptor
        self.on_recall_callback: Optional[Callable[[str, str], None]] = None

    def check_connection(self) -> bool:
        """Verify Ollama server is reachable."""
        try:
            resp = requests.get(f"{self.base_url}/", timeout=3.0)
            return resp.status_code == 200
        except Exception:
            return False

    def list_models(self) -> List[str]:
        """Return names of all installed models in Ollama."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                return [m.get("name") for m in data.get("models", [])]
        except Exception as e:
            logger.error("Failed to list Ollama models: %s", e)
        return []

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model_override: Optional[str] = None
    ) -> ModelResponse:
        """Send chat completion request with optional tool definitions."""
        model_name = model_override or self.reasoning_model

        # If Active Recall interceptor is configured, stream and intercept <recall> tags
        if self.recall_interceptor is not None:
            return self.recall_interceptor.execute_chat_with_recall(
                base_url=self.base_url,
                model_name=model_name,
                messages=messages,
                tools=tools,
                timeout=self.timeout,
                on_recall_callback=self.on_recall_callback,
            )

        payload = {
            "model": model_name,
            "messages": messages,
            "stream": False
        }
        if tools:
            payload["tools"] = tools

        try:
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()
            return self._parse_chat_response(data, model_name)
        except Exception as e:
            logger.error("Ollama chat error with model %s: %s", model_name, e)
            return ModelResponse(
                content=f"Error communicating with Ollama: {str(e)}",
                model=model_name,
                raw={"error": str(e)}
            )

    def vision_chat(
        self,
        prompt: str,
        image_base64: str,
        system_prompt: Optional[str] = None,
        model_override: Optional[str] = None
    ) -> ModelResponse:
        """Send multimodal vision query to Ollama."""
        model_name = model_override or self.vision_model
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append({
            "role": "user",
            "content": prompt,
            "images": [image_base64]
        })

        payload = {
            "model": model_name,
            "messages": messages,
            "stream": False
        }

        try:
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()
            return self._parse_chat_response(data, model_name)
        except Exception as e:
            logger.error("Ollama vision error with model %s: %s", model_name, e)
            return ModelResponse(
                content=f"Error communicating with Ollama vision model: {str(e)}",
                model=model_name,
                raw={"error": str(e)}
            )

    def _parse_chat_response(self, data: Dict[str, Any], model_name: str) -> ModelResponse:
        """Extract content, thinking tokens, and tool calls from Ollama output."""
        msg = data.get("message", {})
        content = msg.get("content", "") or ""
        thinking = msg.get("thinking", "") or ""

        # Extract <thought> or <thinking> tags if present inside content
        thought_match = re.search(r"<(?:thought|thinking)>(.*?)</(?:thought|thinking)>", content, re.DOTALL)
        if thought_match:
            thinking = thought_match.group(1).strip()
            content = re.sub(r"<(?:thought|thinking)>.*?</(?:thought|thinking)>", "", content, flags=re.DOTALL).strip()

        tool_calls: List[ToolCall] = []

        # 1. Parse native Ollama tool_calls
        raw_tools = msg.get("tool_calls", [])
        if raw_tools and isinstance(raw_tools, list):
            for t in raw_tools:
                fn = t.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {"raw_input": args}
                if name:
                    tool_calls.append(ToolCall(name=name, arguments=args, id=t.get("id")))

        # 2. Fallback: Parse tool calls formatted as JSON in content
        if not tool_calls:
            tool_calls = self._extract_json_tool_calls(content)

        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            thinking=thinking,
            model=model_name,
            raw=data
        )

    def _extract_json_tool_calls(self, text: str) -> List[ToolCall]:
        """Fallback tool extraction from markdown codeblocks or JSON snippets."""
        extracted: List[ToolCall] = []

        # Look for ```json { "name": ..., "arguments": ... } ```
        code_blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        for block in code_blocks:
            try:
                parsed = json.loads(block)
                if isinstance(parsed, dict) and "name" in parsed:
                    args = parsed.get("arguments", parsed.get("parameters", {}))
                    extracted.append(ToolCall(name=parsed["name"], arguments=args))
            except Exception:
                pass

        # Also look for <tool_call> tags
        tag_blocks = re.findall(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.DOTALL)
        for block in tag_blocks:
            try:
                parsed = json.loads(block)
                if isinstance(parsed, dict) and "name" in parsed:
                    args = parsed.get("arguments", {})
                    extracted.append(ToolCall(name=parsed["name"], arguments=args))
            except Exception:
                pass

        return extracted
