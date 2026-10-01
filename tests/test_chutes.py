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


def test_chutes_list_models():
    from unittest.mock import MagicMock, patch

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "object": "list",
        "data": [
            {"id": "google/gemma-4-31B-turbo-TEE"},
            {"id": "moonshotai/Kimi-K2.6-TEE"},
            {"id": "Qwen/Qwen3.5-397B-A17B-TEE"},
        ]
    }

    client = ChutesClient(api_key="test_key")
    with patch("requests.get", return_value=mock_resp):
        models = client.list_models()

    assert models == [
        "Qwen/Qwen3.5-397B-A17B-TEE",
        "google/gemma-4-31B-turbo-TEE",
        "moonshotai/Kimi-K2.6-TEE",
    ]


def test_chutes_stream_openai_chat():
    import json
    from unittest.mock import MagicMock, patch

    mock_resp = MagicMock()
    mock_resp.iter_lines.return_value = [
        b'data: {"choices":[{"delta":{"content":"Hello "}}]}',
        b'data: {"choices":[{"delta":{"content":"world!"}}]}',
        b'data: [DONE]',
    ]

    client = ChutesClient(api_key="test_key", stream=True)
    with patch("requests.post", return_value=mock_resp):
        resp = client.chat([{"role": "user", "content": "hi"}])

    assert resp.content == "Hello world!"


def test_chutes_with_recall_interceptor():
    from unittest.mock import MagicMock

    mock_interceptor = MagicMock()
    mock_interceptor.execute_openai_chat_with_recall.return_value = ModelResponse(
        content="Recalled content from Chutes",
        thinking="Thoughts",
        tool_calls=[],
        model="google/gemma-4-31B-turbo-TEE",
    )

    client = ChutesClient(api_key="test_key", recall_interceptor=mock_interceptor)
    resp = client.chat([{"role": "user", "content": "adjust screen"}])

    assert mock_interceptor.execute_openai_chat_with_recall.called
    assert resp.content == "Recalled content from Chutes"
    assert resp.thinking == "Thoughts"


def test_model_router_propagates_recall_interceptor():
    from unittest.mock import MagicMock

    mock_interceptor = MagicMock()
    client_ollama = MagicMock()
    client_chutes = MagicMock()

    router = ModelRouter(ollama_client=client_ollama, chutes_client=client_chutes)
    router.recall_interceptor = mock_interceptor

    assert client_ollama.recall_interceptor == mock_interceptor
    assert client_chutes.recall_interceptor == mock_interceptor

