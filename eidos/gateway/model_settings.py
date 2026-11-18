"""Read and update the active AI model from the remote web app.

API keys and base URLs stay on the PC. Callers only see which provider and
model ids are selected, plus the model names the phone can choose from.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Dict, Iterable, List, Optional

import yaml

from eidos.models.meta_client import (
    DEFAULT_META_MODEL,
    DEFAULT_REASONING_EFFORT,
    META_CHAT_MODELS,
    META_REASONING_EFFORTS,
)

# Same starter ids as the desktop Models tab. Live catalogs are merged in on refresh.
CHUTES_REASONING_PRESETS = [
    "moonshotai/Kimi-K2.6-TEE",
    "google/gemma-4-31B-turbo-TEE",
    "deepseek-ai/DeepSeek-V3.2-TEE",
    "Qwen/Qwen3-32B-TEE",
    "zai-org/GLM-5.1-TEE",
    "moonshotai/Kimi-K3-TEE",
]
CHUTES_VISION_PRESETS = [
    "Qwen/Qwen3.5-397B-A17B-TEE",
    "google/gemma-4-31B-turbo-TEE",
    "moonshotai/Kimi-K2.6-TEE",
    "Qwen/Qwen3.6-27B-TEE",
]

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\-]{0,199}$")
_PREFERENCES = ("local", "auto", "cloud")
_CLOUD_PROVIDERS = ("chutes", "meta")

_PATCH_KEYS = {
    "preference",
    "cloud_provider",
    "local_reasoning_model",
    "local_vision_model",
    "chutes_reasoning_model",
    "chutes_vision_model",
    "meta_reasoning_model",
    "meta_vision_model",
    "meta_reasoning_effort",
    "reasoning_model",
    "vision_model",
}


def describe_model_settings(agent: Any, *, refresh: bool = False) -> Dict[str, Any]:
    """Return the current model selection and the ids the phone may pick."""
    with _LOCK:
        return _describe(agent, refresh=refresh, list_local=True)


def apply_model_settings(
    agent: Any,
    payload: Dict[str, Any],
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Switch the live model and write the same choice back to config.yaml."""
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object")
    unknown = sorted(set(payload) - _PATCH_KEYS)
    if unknown:
        raise ValueError("Only model selection can be changed from the phone")
    if not any(key in payload for key in _PATCH_KEYS):
        raise ValueError("Choose a mode or model to update")

    cfg = getattr(agent, "config", None)
    if not isinstance(cfg, dict):
        raise ValueError("The agent has no configuration loaded")

    with _LOCK:
        _apply_patch(cfg, payload)
        updater = getattr(agent, "update_config", None)
        if callable(updater):
            updater(cfg)
        persisted = True
        if config_path:
            try:
                _persist(config_path, cfg)
            except OSError as exc:
                persisted = False
                logger.warning("Model change is live but was not saved to disk: %s", exc)
        snapshot = _describe(agent, refresh=False, list_local=False)
        snapshot["persisted"] = persisted
        return snapshot


def _models_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    models = cfg.get("models")
    if not isinstance(models, dict):
        models = {}
        cfg["models"] = models
    for key in ("local", "cloud", "meta"):
        section = models.get(key)
        if not isinstance(section, dict):
            models[key] = {}
    return models


def _apply_patch(cfg: Dict[str, Any], payload: Dict[str, Any]) -> None:
    models = _models_cfg(cfg)
    local = models["local"]
    cloud = models["cloud"]
    meta = models["meta"]

    if "preference" in payload:
        preference = str(payload.get("preference") or "").strip().lower()
        if preference not in _PREFERENCES:
            raise ValueError("Mode must be local, auto, or cloud")
        models["provider_preference"] = preference

    if "cloud_provider" in payload:
        provider = str(payload.get("cloud_provider") or "").strip().lower()
        if provider not in _CLOUD_PROVIDERS:
            raise ValueError("Cloud provider must be chutes or meta")
        cloud["provider"] = provider

    _set_model(local, "reasoning_model", payload, "local_reasoning_model", "Local chat model")
    _set_model(local, "vision_model", payload, "local_vision_model", "Local vision model")
    _set_model(cloud, "reasoning_model", payload, "chutes_reasoning_model", "Chutes chat model")
    _set_model(cloud, "vision_model", payload, "chutes_vision_model", "Chutes vision model")
    _set_model(meta, "reasoning_model", payload, "meta_reasoning_model", "Meta chat model")
    _set_model(meta, "vision_model", payload, "meta_vision_model", "Meta vision model")

    if "meta_reasoning_effort" in payload:
        effort = str(payload.get("meta_reasoning_effort") or "").strip().lower()
        if effort not in META_REASONING_EFFORTS:
            allowed = ", ".join(META_REASONING_EFFORTS)
            raise ValueError(f"Thinking effort must be one of: {allowed}")
        meta["reasoning_effort"] = effort

    preference = str(models.get("provider_preference") or "local").strip().lower()
    provider = str(cloud.get("provider") or "chutes").strip().lower()
    backend = _active_backend(preference, provider)
    target = {"local": local, "chutes": cloud, "meta": meta}[backend]
    if "reasoning_model" in payload:
        target["reasoning_model"] = _model_id(payload.get("reasoning_model"), "Chat model")
    if "vision_model" in payload:
        target["vision_model"] = _model_id(payload.get("vision_model"), "Vision model")


def _set_model(
    section: Dict[str, Any],
    dest: str,
    payload: Dict[str, Any],
    source: str,
    label: str,
) -> None:
    if source not in payload:
        return
    section[dest] = _model_id(payload.get(source), label)


def _model_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not _MODEL_ID.match(text):
        raise ValueError(f"{label} must be a model id such as muse-spark-1.3")
    return text


def _active_backend(preference: str, cloud_provider: str) -> str:
    if preference == "local":
        return "local"
    if cloud_provider == "meta":
        return "meta"
    return "chutes"


def _describe(agent: Any, *, refresh: bool, list_local: bool) -> Dict[str, Any]:
    cfg = getattr(agent, "config", None)
    models = cfg.get("models", {}) if isinstance(cfg, dict) else {}
    if not isinstance(models, dict):
        models = {}
    local = models.get("local") if isinstance(models.get("local"), dict) else {}
    cloud = models.get("cloud") if isinstance(models.get("cloud"), dict) else {}
    meta = models.get("meta") if isinstance(models.get("meta"), dict) else {}

    preference = str(models.get("provider_preference") or "local").strip().lower()
    if preference not in _PREFERENCES:
        preference = "local"
    provider = str(cloud.get("provider") or "chutes").strip().lower()
    if provider not in _CLOUD_PROVIDERS:
        provider = "chutes"

    local_reasoning = str(local.get("reasoning_model") or "")
    local_vision = str(local.get("vision_model") or "")
    chutes_reasoning = str(cloud.get("reasoning_model") or "")
    chutes_vision = str(cloud.get("vision_model") or "")
    meta_reasoning = str(meta.get("reasoning_model") or DEFAULT_META_MODEL)
    meta_vision = str(meta.get("vision_model") or DEFAULT_META_MODEL)
    effort = str(meta.get("reasoning_effort") or DEFAULT_REASONING_EFFORT).strip().lower()
    if effort not in META_REASONING_EFFORTS:
        effort = DEFAULT_REASONING_EFFORT

    backend = _active_backend(preference, provider)
    if backend == "local":
        active_reasoning, active_vision = local_reasoning, local_vision
    elif backend == "meta":
        active_reasoning, active_vision = meta_reasoning, meta_vision
    else:
        active_reasoning, active_vision = chutes_reasoning, chutes_vision

    local_names: List[str] = []
    local_reachable = False
    if list_local:
        local_names = _safe_list(getattr(agent, "ollama", None))
        local_reachable = bool(local_names) or _client_reachable(getattr(agent, "ollama", None))

    chutes_live: List[str] = []
    meta_live: List[str] = []
    if refresh:
        chutes_live = _safe_list(getattr(agent, "chutes", None))
        meta_live = _safe_list(getattr(agent, "meta", None))

    summary = _summary(preference, backend, active_reasoning)
    return {
        "preference": preference,
        "cloud_provider": provider,
        "active_backend": backend,
        "active_reasoning_model": active_reasoning,
        "active_vision_model": active_vision,
        "summary": summary,
        "local": {
            "reasoning_model": local_reasoning,
            "vision_model": local_vision,
            "reachable": local_reachable if list_local else None,
            "models": _merge([local_reasoning, local_vision], local_names),
        },
        "chutes": {
            "configured": _is_configured(getattr(agent, "chutes", None), cloud.get("api_key")),
            "reasoning_model": chutes_reasoning,
            "vision_model": chutes_vision,
            "models": _merge(
                [chutes_reasoning, chutes_vision],
                CHUTES_REASONING_PRESETS,
                CHUTES_VISION_PRESETS,
                chutes_live,
            ),
        },
        "meta": {
            "configured": _is_configured(getattr(agent, "meta", None), meta.get("api_key")),
            "reasoning_model": meta_reasoning,
            "vision_model": meta_vision,
            "reasoning_effort": effort,
            "efforts": list(META_REASONING_EFFORTS),
            "models": _merge([meta_reasoning, meta_vision], META_CHAT_MODELS, meta_live),
        },
    }


def _summary(preference: str, backend: str, reasoning: str) -> str:
    model = reasoning or "default"
    if preference == "local":
        return f"Local · {model}"
    mode = "Auto" if preference == "auto" else "Cloud"
    who = "Meta" if backend == "meta" else "Chutes"
    return f"{mode} · {who} · {model}"


def _merge(*groups: Iterable[Any]) -> List[str]:
    names: List[str] = []
    seen = set()
    for group in groups:
        for item in group:
            text = str(item or "").strip()
            if text and text not in seen:
                seen.add(text)
                names.append(text)
    return names


def _safe_list(client: Any) -> List[str]:
    if client is None or not hasattr(client, "list_models"):
        return []
    try:
        found = client.list_models() or []
    except Exception:
        logger.debug("Could not list models", exc_info=True)
        return []
    return [str(name) for name in found if name]


def _client_reachable(client: Any) -> bool:
    if client is None or not hasattr(client, "check_connection"):
        return False
    try:
        return bool(client.check_connection())
    except Exception:
        return False


def _is_configured(client: Any, stored_key: Any) -> bool:
    if client is not None and hasattr(client, "is_configured"):
        try:
            return bool(client.is_configured)
        except Exception:
            return False
    return bool(str(stored_key or "").strip())


def _persist(config_path: str, cfg: Dict[str, Any]) -> None:
    path = os.path.abspath(os.path.expanduser(config_path))
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        yaml.safe_dump(cfg, handle, default_flow_style=False, sort_keys=False)
    os.replace(tmp, path)
