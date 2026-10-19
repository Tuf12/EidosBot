"""Ollama client for local multi-model execution with tool calling."""

import json
import logging
import re
import threading
from typing import Any, Callable, Dict, List, Optional

import requests

from eidos.models.base import BaseModelClient, ModelResponse, ToolCall
from eidos.models.inflight import InflightRequest
from eidos.models.ollama_options import chat_generation_options

logger = logging.getLogger(__name__)

# One GPU-resident generator at a time for every client in this process.
_GPU_LOCK = threading.Lock()

_VISION_NAME_MARKERS = (
    "vision",
    "llava",
    "bakllava",
    "moondream",
    "minicpm-v",
    "pixtral",
    "internvl",
    "cogvlm",
    "gemma3",
    "gemma4",
    "qwen2-vl",
    "qwen2.5-vl",
    "qwen2.5vl",
    "qwen3-vl",
    "qwen3vl",
)


def _model_identity(name: str) -> str:
    """Normalize an Ollama model name so tag aliases compare equal."""
    text = (name or "").strip().lower()
    if text.endswith(":latest"):
        text = text[: -len(":latest")]
    return text


def _names_match(left: str, right: str) -> bool:
    a = _model_identity(left)
    b = _model_identity(right)
    return bool(a) and a == b


def _name_suggests_vision(name: str) -> bool:
    text = (name or "").lower()
    if any(marker in text for marker in _VISION_NAME_MARKERS):
        return True
    tokens = re.split(r"[^a-z0-9]+", text)
    return "vl" in tokens


class OllamaClient(BaseModelClient):
    """Client for local Ollama instances supporting tool calling and vision.

    When the primary reasoning model can already see images, vision requests
    stay on that model. The designated vision model is not loaded, so two
    vision models never share the GPU.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        reasoning_model: str = "batiai/gemma4-26b:iq4",
        vision_model: str = "batiai/gemma4-12b:q6",
        chat_model: str = "gpt-oss:20b",
        timeout: float = 120.0,
        num_ctx: Optional[int] = None,
        recall_interceptor: Optional[Any] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.reasoning_model = reasoning_model
        self.vision_model = vision_model
        self.chat_model = chat_model
        self.timeout = timeout
        self.num_ctx = num_ctx
        self.recall_interceptor = recall_interceptor
        self.on_recall_callback: Optional[Callable[[str, str], None]] = None
        self._inflight = InflightRequest()
        self._vision_capability_cache: Dict[str, bool] = {}
        self._logged_shared_vision = False
        self._logged_ps_failure = False

    def abort(self) -> None:
        """Close the model request that is currently generating."""
        self._inflight.abort()

    def reset_abort(self) -> None:
        """Clear a previous stop so the next task can call the model."""
        self._inflight.reset()

    def _cancelled_response(self, model_name: str) -> ModelResponse:
        return ModelResponse(content="", model=model_name, raw={"cancelled": True})

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

    def clear_vision_capability_cache(self) -> None:
        """Drop cached vision probes after the configured models change."""
        self._vision_capability_cache.clear()
        self._logged_shared_vision = False
        self._logged_ps_failure = False

    def model_supports_vision(self, model_name: str) -> bool:
        """Return whether an installed Ollama model accepts images.

        `/api/show` reads the manifest and does not load weights onto the GPU.
        A listed `capabilities` array is authoritative. Name hints are used
        only when Ollama does not report capabilities.
        """
        key = _model_identity(model_name)
        if not key:
            return False
        cached = self._vision_capability_cache.get(key)
        if cached is not None:
            return cached

        supports = self._probe_vision_capability(model_name)
        self._vision_capability_cache[key] = supports
        return supports

    def resolve_vision_model(self, model_override: Optional[str] = None) -> str:
        """Pick the local model that will serve a vision request.

        A vision-capable primary replaces the designated vision model so
        Ollama does not load a second set of weights.
        """
        primary = (self.reasoning_model or "").strip()
        if primary and self.model_supports_vision(primary):
            requested = (model_override or self.vision_model or "").strip()
            if requested and not _names_match(requested, primary) and not self._logged_shared_vision:
                logger.info(
                    "Primary Ollama model %s supports vision. "
                    "Vision requests will use it, and %s will not be loaded.",
                    primary,
                    requested,
                )
                self._logged_shared_vision = True
            return primary
        return (model_override or self.vision_model or primary).strip()

    def generation_options(self) -> Dict[str, Any]:
        """Ollama ``options`` for chat (shared by streaming recall and direct chat)."""
        return chat_generation_options(self.num_ctx)

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model_override: Optional[str] = None
    ) -> ModelResponse:
        """Send chat completion request with optional tool definitions."""
        with _GPU_LOCK:
            self._release_extra_vision_model()
            return self._chat_unlocked(messages, tools, model_override)

    def _chat_unlocked(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model_override: Optional[str] = None
    ) -> ModelResponse:
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
                on_response=self._inflight.bind,
                should_stop=self._inflight.cancelled,
                request_options=self.generation_options(),
            )

        payload = {
            "model": model_name,
            "messages": messages,
            "stream": False,
            "options": self.generation_options(),
        }
        if tools:
            payload["tools"] = tools

        return self._post_chat(model_name, payload)

    def _post_chat(
        self,
        model_name: str,
        payload: Dict[str, Any],
        error_label: str = "Ollama",
    ) -> ModelResponse:
        """POST a non-streaming chat call that Stop can close."""
        session = requests.Session()
        self._inflight.arm(session)
        try:
            if self._inflight.cancelled():
                return self._cancelled_response(model_name)
            resp = session.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
            )
            if self._inflight.cancelled():
                return self._cancelled_response(model_name)
            resp.raise_for_status()
            data = resp.json()
            return self._parse_chat_response(data, model_name)
        except Exception as e:
            if self._inflight.cancelled():
                logger.info("Ollama chat cancelled for model %s", model_name)
                return self._cancelled_response(model_name)
            logger.error("Ollama chat error with model %s: %s", model_name, e)
            return ModelResponse(
                content=f"Error communicating with {error_label}: {str(e)}",
                model=model_name,
                raw={"error": str(e)}
            )
        finally:
            self._inflight.release(session=session)
            try:
                session.close()
            except Exception:
                pass

    def vision_chat(
        self,
        prompt: str,
        image_base64: str,
        system_prompt: Optional[str] = None,
        model_override: Optional[str] = None
    ) -> ModelResponse:
        """Send multimodal vision query to Ollama."""
        with _GPU_LOCK:
            model_name = self.resolve_vision_model(model_override)
            self._release_extra_vision_model()
            return self._vision_chat_unlocked(prompt, image_base64, system_prompt, model_name)

    def _vision_chat_unlocked(
        self,
        prompt: str,
        image_base64: str,
        system_prompt: Optional[str],
        model_name: str,
    ) -> ModelResponse:
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

        return self._post_chat(model_name, payload, error_label="Ollama vision model")

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
        elif "<thought>" in content or "<thinking>" in content:
            unclosed_match = re.search(r"<(?:thought|thinking)>(.*)", content, re.DOTALL)
            if unclosed_match:
                thinking = (thinking + "\n" + unclosed_match.group(1).strip()).strip()
                content = re.sub(r"<(?:thought|thinking)>.*", "", content, flags=re.DOTALL).strip()

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

    def _probe_vision_capability(self, model_name: str) -> bool:
        try:
            resp = requests.post(
                f"{self.base_url}/api/show",
                json={"model": model_name},
                timeout=5.0,
            )
            if resp.status_code == 200:
                known = self._vision_from_show_payload(resp.json())
                if known is not None:
                    return known
        except Exception as e:
            logger.debug("Could not read Ollama model capabilities for %s: %s", model_name, e)
        return _name_suggests_vision(model_name)

    @staticmethod
    def _vision_from_show_payload(data: Dict[str, Any]) -> Optional[bool]:
        """Interpret `/api/show`. None means the payload has no capability signal."""
        if not isinstance(data, dict):
            return None

        caps = data.get("capabilities")
        if isinstance(caps, list) and caps:
            return any(str(item).lower() == "vision" for item in caps)

        info = data.get("model_info") or {}
        if isinstance(info, dict):
            for key in info:
                lowered = str(key).lower()
                if any(token in lowered for token in ("vision", "clip", "mmproj", "projector")):
                    return True

        details = data.get("details") or {}
        families: List[str] = []
        if isinstance(details, dict):
            raw_families = details.get("families") or []
            if isinstance(raw_families, list):
                families.extend(str(item) for item in raw_families)
            if details.get("family"):
                families.append(str(details["family"]))
        blob = " ".join(families).lower()
        if any(token in blob for token in ("clip", "vision", "llava", "gemma3", "gemma4")):
            return True
        if "vl" in re.split(r"[^a-z0-9]+", blob):
            return True
        return None

    def _release_extra_vision_model(self) -> None:
        """Unload the designated vision model when the primary can see images."""
        primary = (self.reasoning_model or "").strip()
        extra = (self.vision_model or "").strip()
        if not primary or not extra or _names_match(primary, extra):
            return
        if not self.model_supports_vision(primary):
            return

        loaded_name = self._loaded_name(extra)
        if loaded_name is None:
            if not self._logged_ps_failure:
                logger.warning(
                    "Could not list loaded Ollama models. If %s is still in memory with %s, "
                    "unload it with `ollama stop %s` before continuing.",
                    extra,
                    primary,
                    extra,
                )
                self._logged_ps_failure = True
            return
        if not loaded_name:
            return

        logger.info(
            "Unloading Ollama vision model %s so it does not share the GPU with %s.",
            loaded_name,
            primary,
        )
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={"model": loaded_name, "keep_alive": 0, "stream": False},
                timeout=30.0,
            )
            if resp.status_code >= 400:
                logger.warning(
                    "Ollama refused to unload %s (HTTP %s).",
                    loaded_name,
                    resp.status_code,
                )
        except Exception as e:
            logger.warning("Failed to unload Ollama model %s: %s", loaded_name, e)

    def _loaded_name(self, model_name: str) -> Optional[str]:
        """Return the resident model name.

        Empty string means the model is not loaded. None means `/api/ps` could
        not be read, so the caller must not assume the GPU is clear.
        """
        try:
            resp = requests.get(f"{self.base_url}/api/ps", timeout=3.0)
            if resp.status_code != 200:
                return None
            data = resp.json()
        except Exception as e:
            logger.debug("Could not list loaded Ollama models: %s", e)
            return None

        if not isinstance(data, dict):
            return None

        for item in data.get("models") or []:
            if not isinstance(item, dict):
                continue
            resident = item.get("name") or item.get("model") or ""
            if _names_match(str(resident), model_name):
                return str(resident)
        return ""
