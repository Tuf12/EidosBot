"""Tests for Chutes AI client and ModelRouter."""

import pytest
from eidos.models.base import BaseModelClient, ModelResponse, ToolCall
from eidos.models.chutes_client import ChutesClient
from eidos.models.router import ModelRouter, ProviderPreference


class MockClient(BaseModelClient):
    """Mock client for router testing."""

    def __init__(self, name="mock", fail=False):
        self.name = name
        self.fail = fail

    def chat(self, messages, tools=None, model_override=None):
        if self.fail:
            return ModelResponse(content="Error communicating with Ollama", model=self.name)
        return ModelResponse(content=f"Response from {self.name}", model=self.name)

    def vision_chat(self, prompt, image_base64, system_prompt=None, model_override=None, dense_grounding=False):
        if self.fail:
            return ModelResponse(content="Error communicating with Ollama", model=self.name)
        return ModelResponse(content=f"Vision response from {self.name}", model=self.name)


def test_chutes_unconfigured():
    client = ChutesClient(api_key="")
    assert not client.is_configured

    resp = client.chat([{"role": "user", "content": "hello"}])
    assert "not configured" in resp.content.lower()

    resp_vis = client.vision_chat("look", "base64img")
    assert "not configured" in resp_vis.content.lower()


def test_chutes_parse_response():
    client = ChutesClient(api_key="test_key")
    raw_response = {
        "id": "chatcmpl-123",
        "model": "Qwen/Qwen3.5-397B-A17B-TEE",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "I am executing a tool.",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "click_element",
                                "arguments": '{"target_description": "Submit"}'
                            }
                        }
                    ]
                }
            }
        ]
    }

    parsed = client._parse_openai_response(raw_response, "fallback-model")
    assert parsed.has_tool_calls
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "click_element"
    assert parsed.tool_calls[0].arguments == {"target_description": "Submit"}
    assert parsed.model == "Qwen/Qwen3.5-397B-A17B-TEE"


def test_model_router_local_preference():
    mock_local = MockClient("local_ollama")
    mock_cloud = MockClient("chutes_cloud")
    router = ModelRouter(ollama_client=mock_local, chutes_client=mock_cloud, preference="local")

    resp = router.chat([{"role": "user", "content": "hi"}])
    assert resp.model == "local_ollama"
    assert router.last_provider_used == "ollama"


def test_model_router_cloud_preference():
    mock_local = MockClient("local_ollama")
    mock_cloud = MockClient("chutes_cloud")
    mock_cloud.is_configured = True
    router = ModelRouter(ollama_client=mock_local, chutes_client=mock_cloud, preference="cloud")

    resp = router.chat([{"role": "user", "content": "hi"}])
    assert resp.model == "chutes_cloud"
    assert router.last_provider_used == "chutes"


def test_model_router_auto_escalation_on_error():
    mock_local_fail = MockClient("local_ollama", fail=True)
    mock_cloud = MockClient("chutes_cloud")
    mock_cloud.is_configured = True
    router = ModelRouter(ollama_client=mock_local_fail, chutes_client=mock_cloud, preference="auto")

    resp = router.chat([{"role": "user", "content": "hi"}])
    # Router should catch local failure and escalate to cloud
    assert resp.model == "chutes_cloud"
    assert router.last_provider_used == "chutes"
