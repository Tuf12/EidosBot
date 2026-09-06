"""Tests for EidosBot Model Client."""

from eidos.models.ollama_client import OllamaClient


def test_ollama_connection():
    client = OllamaClient()
    # Check if local Ollama server is responding
    assert client.check_connection() is True
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
