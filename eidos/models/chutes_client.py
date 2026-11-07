"""Chutes AI cloud client for accelerated vision grounding and complex planning."""

import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional

import requests

from eidos.models.base import BaseModelClient, ModelResponse, ToolCall
from eidos.models.inflight import InflightRequest

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
        reasoning_model: str = "moonshotai/Kimi-K2.6-TEE",
        timeout: float = 180.0,
        stream: bool = True,
        recall_interceptor: Optional[Any] = None,
        on_recall_callback: Optional[Callable[[str, str], None]] = None,
        api_key_env: str = "CHUTES_API_KEY",
    ):
        self.api_key_env = api_key_env
        self.api_key = api_key or os.environ.get(api_key_env, "")
        self.base_url = base_url.rstrip("/")
        self.vision_model = vision_model
        self.reasoning_model = reasoning_model
        self.timeout = timeout
        self.stream = stream
        self._inflight = InflightRequest()
        self.recall_interceptor = recall_interceptor
        self.on_recall_callback = on_recall_callback
        self.service_name = "Chutes AI"
        self.key_config_hint = "models.cloud.api_key in config.yaml or CHUTES_API_KEY"
        self.invalid_api_keys = {"", "YOUR_CHUTES_API_KEY_HERE"}

    @property
    def is_configured(self) -> bool:
        """Check if an API key has been supplied."""
        key = (self.api_key or "").strip()
        return bool(key) and key not in self.invalid_api_keys

    def _request_extras(self, model_name: str) -> Dict[str, Any]:
        """Extra JSON fields merged into each chat or vision request."""
        return {}

    def _missing_key_message(self) -> str:
        return (
            f"{self.service_name} API key not configured. "
            f"Please set {self.key_config_hint}."
        )

    def _communication_error(self, exc: Exception, *, vision: bool = False) -> str:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        target = f"{self.service_name} vision model" if vision else self.service_name
        if status_code == 429 or "429" in str(exc):
            return (
                f"Error communicating with {target}: 429 Client Error "
                "(Rate limit or concurrency quota exceeded). Please wait a moment "
                "or switch provider preference to 'local' in Settings."
            )
        return f"Error communicating with {target}: {exc}"

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
            logger.debug("%s connection check failed: %s", self.service_name, e)
            return False

    def list_models(self) -> List[str]:
        """Fetch available models from Chutes AI."""
        if not self.is_configured:
            return []
        try:
            resp = requests.get(
                f"{self.base_url}/models",
                headers=self._get_headers(),
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("id") for m in data.get("data", []) if m.get("id")]
                return sorted(models)
        except Exception as e:
            logger.error("Failed to list %s models: %s", self.service_name, e)
        return []

    def abort(self) -> None:
        """Close the model request that is currently generating."""
        self._inflight.abort()

    def reset_abort(self) -> None:
        """Clear a previous stop so the next task can call the model."""
        self._inflight.reset()

    def _cancelled_response(self, model_name: str) -> ModelResponse:
        return ModelResponse(content="", model=model_name, raw={"cancelled": True})

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model_override: Optional[str] = None,
    ) -> ModelResponse:
        """Send chat completion request to Chutes AI."""
        if not self.is_configured:
            return ModelResponse(
                content=self._missing_key_message(),
                model=model_override or self.reasoning_model,
                raw={"error": "missing_api_key"}
            )

        model_name = model_override or self.reasoning_model
        extras = self._request_extras(model_name)

        # If Active Recall interceptor is configured, stream SSE and intercept <recall> tags
        if self.recall_interceptor is not None:
            return self.recall_interceptor.execute_openai_chat_with_recall(
                base_url=self.base_url,
                api_key=self.api_key,
                model_name=model_name,
                messages=messages,
                tools=tools,
                timeout=self.timeout,
                on_recall_callback=self.on_recall_callback,
                on_response=self._inflight.bind,
                should_stop=self._inflight.cancelled,
                extra_payload=extras or None,
                service_name=self.service_name,
            )

        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": messages,
        }
        if extras:
            payload.update(extras)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            if self.stream:
                return self._stream_openai_chat(model_name, payload)
            else:
                payload["stream"] = False
                return self._post_openai_chat(model_name, payload)
        except Exception as e:
            if self._inflight.cancelled():
                logger.info("%s chat cancelled for model %s", self.service_name, model_name)
                return self._cancelled_response(model_name)
            logger.error("%s chat error with model %s: %s", self.service_name, model_name, e)
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            return ModelResponse(
                content=self._communication_error(e),
                model=model_name,
                raw={"error": str(e), "status_code": status_code}
            )

    def _post_openai_chat(self, model_name: str, payload: Dict[str, Any]) -> ModelResponse:
        """POST a non-streaming chat call that Stop can close."""
        session = requests.Session()
        self._inflight.arm(session)
        try:
            if self._inflight.cancelled():
                return self._cancelled_response(model_name)
            resp = session.post(
                f"{self.base_url}/chat/completions",
                headers=self._get_headers(),
                json=payload,
                timeout=self.timeout,
            )
            if self._inflight.cancelled():
                return self._cancelled_response(model_name)
            resp.raise_for_status()
            return self._parse_openai_response(resp.json(), model_name)
        finally:
            self._inflight.release(session=session)
            try:
                session.close()
            except Exception:
                pass

    def _stream_openai_chat(
        self,
        model_name: str,
        payload: Dict[str, Any],
    ) -> ModelResponse:
        """Stream chat completion via SSE, accumulating tokens, thoughts, and tool calls."""
        payload["stream"] = True
        if self._inflight.cancelled():
            return self._cancelled_response(model_name)
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers=self._get_headers(),
            json=payload,
            stream=True,
            timeout=self.timeout
        )
        self._inflight.bind(resp)
        total_content = ""
        total_thinking = ""
        tool_call_deltas: Dict[int, Dict[str, Any]] = {}
        raw_final_data: Dict[str, Any] = {}

        try:
            if self._inflight.cancelled():
                return self._cancelled_response(model_name)
            resp.raise_for_status()
            for line in resp.iter_lines():
                if self._inflight.cancelled():
                    return self._cancelled_response(model_name)
                if not line:
                    continue
                line_str = line.decode("utf-8") if isinstance(line, bytes) else line
                if not line_str.startswith("data:"):
                    continue
                body = line_str[5:].strip()
                if not body:
                    continue
                if body == "[DONE]":
                    break

                try:
                    chunk = json.loads(body)
                except Exception:
                    continue

                raw_final_data = chunk
                choices = chunk.get("choices", [])
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta", {})

                if delta.get("content"):
                    total_content += delta["content"]
                if delta.get("reasoning_content"):
                    total_thinking += delta["reasoning_content"]

                tc_deltas = delta.get("tool_calls", [])
                if tc_deltas and isinstance(tc_deltas, list):
                    for tc in tc_deltas:
                        idx = tc.get("index", 0)
                        if idx not in tool_call_deltas:
                            tool_call_deltas[idx] = {
                                "id": tc.get("id") or f"call_{idx}",
                                "name": "",
                                "arguments": "",
                            }
                        if tc.get("id"):
                            tool_call_deltas[idx]["id"] = tc["id"]
                        fn = tc.get("function", {})
                        if fn.get("name"):
                            tool_call_deltas[idx]["name"] = fn["name"]
                        if fn.get("arguments"):
                            tool_call_deltas[idx]["arguments"] += fn["arguments"]
        except Exception as exc:
            if self._inflight.cancelled():
                logger.info("%s stream cancelled for model %s", self.service_name, model_name)
                return self._cancelled_response(model_name)
            raise exc
        finally:
            self._inflight.release(response=resp)
            resp.close()

        accumulated_tool_calls: List[ToolCall] = []
        for idx in sorted(tool_call_deltas.keys()):
            tc_data = tool_call_deltas[idx]
            args_str = tc_data["arguments"]
            try:
                parsed_args = json.loads(args_str) if args_str else {}
            except Exception:
                parsed_args = {"raw_input": args_str}
            if tc_data["name"]:
                accumulated_tool_calls.append(
                    ToolCall(
                        name=tc_data["name"],
                        arguments=parsed_args,
                        id=tc_data["id"]
                    )
                )

        if not accumulated_tool_calls:
            accumulated_tool_calls = self._extract_json_tool_calls(total_content)

        thought_match = re.search(
            r"<(?:thought|thinking)>(.*?)</(?:thought|thinking)>", total_content, re.DOTALL
        )
        if thought_match:
            total_thinking = (total_thinking + "\n" + thought_match.group(1).strip()).strip()
            total_content = re.sub(
                r"<(?:thought|thinking)>.*?</(?:thought|thinking)>", "", total_content, flags=re.DOTALL
            ).strip()

        return ModelResponse(
            content=total_content,
            tool_calls=accumulated_tool_calls,
            thinking=total_thinking,
            model=model_name,
            raw=raw_final_data,
        )

    def _extract_json_tool_calls(self, text: str) -> List[ToolCall]:
        """Fallback tool extraction from markdown codeblocks or JSON snippets."""
        extracted: List[ToolCall] = []
        code_blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        for block in code_blocks:
            try:
                parsed = json.loads(block)
                if isinstance(parsed, dict) and "name" in parsed:
                    args = parsed.get("arguments", parsed.get("parameters", {}))
                    extracted.append(ToolCall(name=parsed["name"], arguments=args))
            except Exception:
                pass

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

    def vision_chat(
        self,
        prompt: str,
        image_base64: str,
        system_prompt: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> ModelResponse:
        """Send multimodal visual query to Chutes AI."""
        if not self.is_configured:
            return ModelResponse(
                content=self._missing_key_message(),
                model=model_override or self.vision_model,
                raw={"error": "missing_api_key"}
            )

        model_name = model_override or self.vision_model

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
        extras = self._request_extras(model_name)
        if extras:
            payload.update(extras)

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
            logger.error("%s vision error with model %s: %s", self.service_name, model_name, e)
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            return ModelResponse(
                content=self._communication_error(e, vision=True),
                model=model_name,
                raw={"error": str(e), "status_code": status_code}
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
