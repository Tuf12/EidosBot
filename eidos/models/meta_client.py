"""Meta Model API client for Muse Spark and Muse Wonder.

Chat Completions at https://api.meta.ai/v1 with a Model API key
(config ``models.meta.api_key`` or the ``MODEL_API_KEY`` environment variable).
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

from eidos.models.base import ModelResponse
from eidos.models.chutes_client import ChutesClient

META_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_META_MODEL = "muse-spark-1.3"
DEFAULT_REASONING_EFFORT = "high"

# Agentic text models. Image, speech, and segmentation models are separate APIs.
META_CHAT_MODELS = [
    "muse-spark-1.3",
    "muse-wonder-1.0",
    "muse-spark-1.2",
    "muse-spark-1.1",
    "muse-spark-1.3-contributor",
    "muse-spark-1.2-contributor",
]

META_REASONING_EFFORTS = ["minimal", "low", "medium", "high", "xhigh", "max"]

# ``max`` is rejected on older Spark versions and on Contributor-mode IDs.
META_MAX_EFFORT_MODELS = {"muse-wonder-1.0", "muse-spark-1.3"}


def prepare_meta_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Copy chat history into the shape Meta Chat Completions accepts.

    Tool-call arguments must be JSON strings, and each tool result needs the
    matching ``tool_call_id``. The live agent history is left unchanged.
    """
    prepared: List[Dict[str, Any]] = []
    pending_ids: List[tuple] = []

    for msg in messages:
        if not isinstance(msg, dict):
            prepared.append(msg)
            pending_ids = []
            continue

        cloned = dict(msg)
        role = cloned.get("role")
        if role == "assistant" and isinstance(cloned.get("tool_calls"), list):
            calls: List[Any] = []
            pending_ids = []
            for index, tc in enumerate(cloned["tool_calls"]):
                if not isinstance(tc, dict):
                    calls.append(tc)
                    continue
                tc_copy = dict(tc)
                fn = dict(tc_copy.get("function") or {})
                args = fn.get("arguments", "{}")
                if not isinstance(args, str):
                    fn["arguments"] = json.dumps(args, default=str)
                tc_copy["function"] = fn
                tc_copy.setdefault("type", "function")
                call_id = tc_copy.get("id") or f"call_{index}"
                tc_copy["id"] = call_id
                pending_ids.append((call_id, fn.get("name")))
                calls.append(tc_copy)
            cloned["tool_calls"] = calls
            if cloned.get("content") is None:
                cloned["content"] = ""
        elif role == "tool":
            content = cloned.get("content")
            if not isinstance(content, str):
                cloned["content"] = json.dumps(content, default=str)
            if not cloned.get("tool_call_id") and pending_ids:
                name = cloned.get("name")
                match_idx = 0
                for index, (_call_id, call_name) in enumerate(pending_ids):
                    if name and call_name == name:
                        match_idx = index
                        break
                call_id, _call_name = pending_ids.pop(match_idx)
                cloned["tool_call_id"] = call_id
        else:
            pending_ids = []
        prepared.append(cloned)
    return prepared


class MetaClient(ChutesClient):
    """
    Client for Meta Model API (https://api.meta.ai/v1).

    Muse Spark and Muse Wonder accept text, images, video, audio, and PDF,
    return text, and support OpenAI-style tool calling. Both reason on every
    request; ``reasoning_effort`` controls how long they think.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = META_BASE_URL,
        vision_model: str = DEFAULT_META_MODEL,
        reasoning_model: str = DEFAULT_META_MODEL,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
        timeout: float = 300.0,
        stream: bool = True,
        recall_interceptor: Optional[Any] = None,
        on_recall_callback: Optional[Callable[[str, str], None]] = None,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url or META_BASE_URL,
            vision_model=vision_model or DEFAULT_META_MODEL,
            reasoning_model=reasoning_model or DEFAULT_META_MODEL,
            timeout=timeout,
            stream=stream,
            recall_interceptor=recall_interceptor,
            on_recall_callback=on_recall_callback,
            api_key_env="MODEL_API_KEY",
        )
        self.service_name = "Meta Model API"
        self.key_config_hint = "models.meta.api_key in config.yaml or MODEL_API_KEY"
        self.invalid_api_keys = {"", "YOUR_MODEL_API_KEY_HERE", "YOUR_CHUTES_API_KEY_HERE"}
        self.reasoning_effort = (reasoning_effort or DEFAULT_REASONING_EFFORT).strip().lower()

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model_override: Optional[str] = None,
    ) -> ModelResponse:
        return super().chat(
            messages=prepare_meta_messages(messages),
            tools=tools,
            model_override=model_override,
        )

    def _request_extras(self, model_name: str) -> Dict[str, Any]:
        effort = (self.reasoning_effort or "").strip().lower()
        if effort in ("", "none"):
            return {}
        if effort == "max" and model_name not in META_MAX_EFFORT_MODELS:
            effort = "xhigh"
        return {"reasoning_effort": effort}
