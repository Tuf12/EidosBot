"""Tests for EidosBot Model Client."""

import json

import pytest

from eidos.models.ollama_client import OllamaClient, _names_match


def test_ollama_connection():
    client = OllamaClient()
    # Check if local Ollama server is responding
    if not client.check_connection():
        pytest.skip("Local Ollama daemon is not running")
    models = client.list_models()
    assert len(models) > 0


def test_parse_native_tool_calls():
    client = OllamaClient()
    mock_data = {
        "message": {
            "role": "assistant",
            "content": "I will execute the command.",
            "tool_calls": [
                {
                    "function": {
                        "name": "execute_shell",
                        "arguments": {"command": "ls -la"}
                    }
                }
            ]
        }
    }
    resp = client._parse_chat_response(mock_data, "test-model")
    assert resp.has_tool_calls
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "execute_shell"
    assert resp.tool_calls[0].arguments == {"command": "ls -la"}


def test_parse_fallback_json_tool_calls():
    client = OllamaClient()
    mock_data = {
        "message": {
            "role": "assistant",
            "content": "Let me run this:\n```json\n{\"name\": \"click\", \"arguments\": {\"x\": 150, \"y\": 300}}\n```"
        }
    }
    resp = client._parse_chat_response(mock_data, "test-model")
    assert resp.has_tool_calls
    assert resp.tool_calls[0].name == "click"
    assert resp.tool_calls[0].arguments == {"x": 150, "y": 300}


def test_parse_thinking_tokens():
    client = OllamaClient()
    mock_data = {
        "message": {
            "role": "assistant",
            "content": "<thought>Analyzing the active window</thought>Done analyzing."
        }
    }
    resp = client._parse_chat_response(mock_data, "test-model")
    assert resp.thinking == "Analyzing the active window"
    assert resp.content == "Done analyzing."


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _install_ollama_http(monkeypatch, show_payload, loaded_models=None):
    """Stub Ollama HTTP so vision routing tests never touch a live GPU."""
    calls = []
    loaded_models = list(loaded_models or [])

    def fake_post(url, json=None, timeout=None, **kwargs):
        calls.append((url, json))
        if str(url).endswith("/api/show"):
            return _FakeResponse(show_payload)
        if str(url).endswith("/api/generate"):
            return _FakeResponse({"model": (json or {}).get("model"), "done": True})
        if str(url).endswith("/api/chat"):
            model = (json or {}).get("model")
            return _FakeResponse({"message": {"role": "assistant", "content": "ok"}, "model": model})
        raise AssertionError(f"unexpected POST {url}")

    def fake_get(url, timeout=None, **kwargs):
        calls.append((url, None))
        if str(url).endswith("/api/ps"):
            return _FakeResponse({"models": [{"name": name} for name in loaded_models]})
        raise AssertionError(f"unexpected GET {url}")

    def fake_session_post(self, url, json=None, timeout=None, **kwargs):
        return fake_post(url, json=json, timeout=timeout, **kwargs)

    monkeypatch.setattr("eidos.models.ollama_client.requests.post", fake_post)
    monkeypatch.setattr("eidos.models.ollama_client.requests.Session.post", fake_session_post)
    monkeypatch.setattr("eidos.models.ollama_client.requests.get", fake_get)
    return calls


def test_names_match_ignores_latest_alias():
    assert _names_match("gemma4:12b", "gemma4:12b:latest")
    assert not _names_match("gemma4:12b", "gemma4:26b")


def test_primary_with_vision_is_used_for_vision_requests(monkeypatch):
    calls = _install_ollama_http(
        monkeypatch,
        {"capabilities": ["completion", "vision", "tools"]},
    )
    client = OllamaClient(
        reasoning_model="primary-vl",
        vision_model="designated-vision",
    )

    resp = client.vision_chat("where is the button", "aW1hZ2U=")

    chat_calls = [payload for url, payload in calls if str(url).endswith("/api/chat")]
    assert len(chat_calls) == 1
    assert chat_calls[0]["model"] == "primary-vl"
    assert resp.model == "primary-vl"
    assert all(payload.get("model") != "designated-vision" for _, payload in calls if payload)


def test_text_only_primary_still_uses_designated_vision_model(monkeypatch):
    calls = _install_ollama_http(
        monkeypatch,
        {"capabilities": ["completion", "tools"]},
    )
    client = OllamaClient(
        reasoning_model="text-only",
        vision_model="designated-vision",
    )

    resp = client.vision_chat("where is the button", "aW1hZ2U=")

    chat_calls = [payload for url, payload in calls if str(url).endswith("/api/chat")]
    assert chat_calls[0]["model"] == "designated-vision"
    assert resp.model == "designated-vision"


def test_primary_with_vision_unloads_resident_vision_model(monkeypatch):
    calls = _install_ollama_http(
        monkeypatch,
        {"capabilities": ["completion", "vision"]},
        loaded_models=["designated-vision:latest"],
    )
    client = OllamaClient(
        reasoning_model="primary-vl",
        vision_model="designated-vision",
    )

    client.chat(messages=[{"role": "user", "content": "hello"}])

    unload_calls = [payload for url, payload in calls if str(url).endswith("/api/generate")]
    chat_calls = [payload for url, payload in calls if str(url).endswith("/api/chat")]
    assert unload_calls == [{"model": "designated-vision:latest", "keep_alive": 0, "stream": False}]
    assert chat_calls[0]["model"] == "primary-vl"


def test_explicit_vision_override_cannot_load_second_model(monkeypatch):
    calls = _install_ollama_http(
        monkeypatch,
        {"capabilities": ["completion", "vision"]},
    )
    client = OllamaClient(
        reasoning_model="primary-vl",
        vision_model="designated-vision",
    )

    client.vision_chat("look", "aW1hZ2U=", model_override="designated-vision")

    chat_calls = [payload for url, payload in calls if str(url).endswith("/api/chat")]
    assert chat_calls[0]["model"] == "primary-vl"


def test_missing_capability_metadata_uses_vision_name_hint(monkeypatch):
    calls = _install_ollama_http(monkeypatch, {})
    client = OllamaClient(
        reasoning_model="batiai/gemma4-26b:iq4",
        vision_model="batiai/gemma4-12b:q6",
    )

    client.vision_chat("look", "aW1hZ2U=")

    chat_calls = [payload for url, payload in calls if str(url).endswith("/api/chat")]
    assert chat_calls[0]["model"] == "batiai/gemma4-26b:iq4"


def test_api_capability_overrides_vision_looking_name(monkeypatch):
    calls = _install_ollama_http(
        monkeypatch,
        {"capabilities": ["completion"]},
    )
    client = OllamaClient(
        reasoning_model="custom-gemma4-text",
        vision_model="designated-vision",
    )

    client.vision_chat("look", "aW1hZ2U=")

    chat_calls = [payload for url, payload in calls if str(url).endswith("/api/chat")]
    assert chat_calls[0]["model"] == "designated-vision"
