"""Unit tests for Active Recall & Mid-Stream Injection (RecallStreamInterceptor)."""

import json
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from eidos.core.context import build_system_prompt
from eidos.memory.episodic_store import EpisodicStore
from eidos.memory.recall_stream import RecallStreamInterceptor
from eidos.memory.types import (
    EpisodeMetadata,
    EpisodeRecord,
    EpisodeReflection,
    EpisodicSearchResult,
    StepRecord,
)
from eidos.models.base import ModelResponse, ToolCall
from eidos.models.ollama_client import OllamaClient


def test_extract_recall_query():
    interceptor = RecallStreamInterceptor()

    # Standard tag
    text1 = "I need to check sink inputs. <recall>pactl list sink inputs</recall> Continuing plan..."
    assert interceptor.extract_recall_query(text1) == "pactl list sink inputs"

    # Multiline query and whitespace
    text2 = "Thinking...\n<recall>\n  find ~/Downloads -name '*.png'  \n</recall>\nDone."
    assert interceptor.extract_recall_query(text2) == "find ~/Downloads -name '*.png'"

    # Case insensitive
    text3 = "<RECALL>audio volume pactl</RECALL>"
    assert interceptor.extract_recall_query(text3) == "audio volume pactl"

    # Incomplete tag returns None
    text4 = "No complete tag <recall>missing closing tag"
    assert interceptor.extract_recall_query(text4) is None

    # No tag
    text5 = "Just regular reasoning without any recall tags."
    assert interceptor.extract_recall_query(text5) is None


def test_format_recalled_memory():
    interceptor = RecallStreamInterceptor()

    # Format with match
    result = EpisodicSearchResult(
        episode_id="ep_test_audio_123",
        task_goal="List PulseAudio sinks",
        outcome="success",
        similarity=0.88,
        summary="Used pactl list sink-inputs",
        lessons_learned="Must run in user desktop session",
        key_actions=["execute_shell(pactl list sink-inputs)"],
    )
    formatted = interceptor.format_recalled_memory(result, "pactl sink inputs")
    assert '<recalled_memory episode="ep_test_audio_123">' in formatted
    assert "- Past Task: List PulseAudio sinks" in formatted
    assert "- Outcome: success" in formatted
    assert "- Resolution Summary: Used pactl list sink-inputs" in formatted
    assert "- Proven Tool Pattern: execute_shell(pactl list sink-inputs)" in formatted
    assert "- Lessons / Gotchas: Must run in user desktop session" in formatted
    assert "Continuing plan:" in formatted

    # Format without match
    formatted_none = interceptor.format_recalled_memory(None, "unknown hardware query")
    assert "<recalled_memory>" in formatted_none
    assert 'No past experience found matching query: "unknown hardware query"' in formatted_none
    assert "Proceed with deterministic command discovery." in formatted_none
    assert "Continuing plan:" in formatted_none


def test_execute_chat_with_no_recall_tags():
    """Verify regular streaming execution without recall tags functions normally."""
    mock_resp = MagicMock()
    mock_resp.iter_lines.return_value = [
        json.dumps({"message": {"role": "assistant", "content": "Hello "}, "done": False}).encode(),
        json.dumps({"message": {"role": "assistant", "content": "world!"}, "done": True}).encode(),
    ]

    with patch("requests.post", return_value=mock_resp):
        interceptor = RecallStreamInterceptor()
        response = interceptor.execute_chat_with_recall(
            base_url="http://localhost:11434",
            model_name="test-model",
            messages=[{"role": "user", "content": "Say hello"}],
        )

    assert response.content == "Hello world!"
    assert response.model == "test-model"
    assert not response.has_tool_calls


def test_execute_chat_stops_when_requested():
    """Stop closes the stream and does not return the rest of the generation."""
    mock_resp = MagicMock()
    mock_resp.iter_lines.return_value = [
        json.dumps({"message": {"role": "assistant", "content": "Keep going"}}).encode(),
    ]
    checks = {"n": 0}

    def should_stop():
        checks["n"] += 1
        return checks["n"] >= 2

    seen = []
    with patch("requests.post", return_value=mock_resp):
        interceptor = RecallStreamInterceptor()
        response = interceptor.execute_chat_with_recall(
            base_url="http://localhost:11434",
            model_name="test-model",
            messages=[{"role": "user", "content": "Say hello"}],
            on_response=seen.append,
            should_stop=should_stop,
        )

    assert response.raw.get("cancelled") is True
    assert response.content == ""
    assert seen == [mock_resp]
    mock_resp.close.assert_called()


def test_execute_chat_with_active_recall_interception():
    """Verify stream is intercepted upon </recall>, vector query is made, and continuation resumes."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_episodic.db"

        # Seed episodic memory with deterministic mock embedder
        mock_emb = MagicMock()
        mock_emb.embed_text.return_value = [0.1] * 768
        mock_emb.dimension = 768
        store = EpisodicStore(db_path=db_path, embedding_client=mock_emb)

        rec = EpisodeRecord(
            episode_id="ep_pactl_001",
            task_goal="Query pulse audio sink inputs",
            metadata=EpisodeMetadata(outcome="success", critic_score=1.0),
            trajectory=[
                StepRecord(
                    step=1,
                    tool_calls=[{"name": "execute_shell", "arguments": {"command": "pactl list sink-inputs"}}],
                )
            ],
            reflection=EpisodeReflection(
                summary="Filtered by application name",
                lessons_learned="Always run under desktop session",
            ),
        )
        store.index_episode(rec)

        interceptor = RecallStreamInterceptor(episodic_store=store, min_similarity=0.1)

        # Stream 1: Model emits thought with <recall> tag
        resp1 = MagicMock()
        resp1.iter_lines.return_value = [
            json.dumps({"message": {"role": "assistant", "thinking": "I need to check pactl.\n<recall>pactl list sink inputs</recall>"}}).encode(),
        ]

        # Stream 2: Continuation after memory splice
        resp2 = MagicMock()
        resp2.iter_lines.return_value = [
            json.dumps({
                "message": {
                    "role": "assistant",
                    "content": "I will execute pactl list sink-inputs.",
                    "tool_calls": [
                        {"function": {"name": "execute_shell", "arguments": {"command": "pactl list sink-inputs"}}}
                    ],
                },
                "done": True,
            }).encode()
        ]

        callback_calls = []

        def on_recall(query, mem_text):
            callback_calls.append((query, mem_text))

        chat_calls = []

        def mock_post(url, *args, **kwargs):
            if "/api/chat" in str(url):
                chat_calls.append((url, kwargs))
                return resp1 if len(chat_calls) == 1 else resp2
            # Embedding requests
            emb_resp = MagicMock()
            emb_resp.status_code = 200
            emb_resp.json.return_value = {"embeddings": [[0.1] * 768], "embedding": [0.1] * 768}
            return emb_resp

        with patch("requests.post", side_effect=mock_post):
            response = interceptor.execute_chat_with_recall(
                base_url="http://localhost:11434",
                model_name="test-model",
                messages=[{"role": "user", "content": "Check pactl sink inputs"}],
                on_recall_callback=on_recall,
            )

        # 1. Verify two chat requests were made (original stream + continuation after splice)
        assert len(chat_calls) == 2

        # 2. Verify callback received recall query and memory
        assert len(callback_calls) == 1
        query, text = callback_calls[0]
        assert query == "pactl list sink inputs"
        assert "<recalled_memory" in text

        # 3. Verify continuation message had spliced memory
        continuation_payload = chat_calls[1][1]["json"]
        cont_messages = continuation_payload["messages"]
        assert len(cont_messages) == 2
        assistant_turn = cont_messages[1]
        assert "<recall>pactl list sink inputs</recall>" in assistant_turn["content"]
        assert "<recalled_memory episode=\"ep_pactl_001\">" in assistant_turn["content"]

        # 4. Verify final model response has tool call and combined thinking
        assert response.has_tool_calls
        assert response.tool_calls[0].name == "execute_shell"
        assert response.tool_calls[0].arguments["command"] == "pactl list sink-inputs"
        assert "pactl" in response.thinking


def test_chunk_split_recall_tokens():
    """Verify that <recall> tags split across arbitrary token chunks are correctly reconstructed."""
    interceptor = RecallStreamInterceptor()

    resp1 = MagicMock()
    # Tag split across 4 small chunks
    resp1.iter_lines.return_value = [
        json.dumps({"message": {"content": "Let me look up <rec"}}).encode(),
        json.dumps({"message": {"content": "all>pactl "}}).encode(),
        json.dumps({"message": {"content": "sinks</rec"}}).encode(),
        json.dumps({"message": {"content": "all> and then plan"}}).encode(),
    ]

    resp2 = MagicMock()
    resp2.iter_lines.return_value = [
        json.dumps({"message": {"content": "I have the plan now."}, "done": True}).encode()
    ]

    captured_recalls = []

    def on_recall(q, _):
        captured_recalls.append(q)

    with patch("requests.post", side_effect=[resp1, resp2]):
        response = interceptor.execute_chat_with_recall(
            base_url="http://localhost:11434",
            model_name="test-model",
            messages=[{"role": "user", "content": "Audio command"}],
            on_recall_callback=on_recall,
        )

    assert len(captured_recalls) == 1
    assert captured_recalls[0] == "pactl sinks"
    assert "I have the plan now." in response.content


def test_max_recalls_per_turn_safeguard():
    """Ensure interceptor halts recall loop after max_recalls_per_turn to prevent infinite loops."""
    interceptor = RecallStreamInterceptor(max_recalls_per_turn=2)

    # Every response emits a new <recall> tag
    def make_loop_resp(tag_num):
        m = MagicMock()
        m.iter_lines.return_value = [
            json.dumps({"message": {"content": f"Loop {tag_num} <recall>query {tag_num}</recall>"}}).encode()
        ]
        return m

    loop_responses = [make_loop_resp(1), make_loop_resp(2), make_loop_resp(3)]

    with patch("requests.post", side_effect=loop_responses) as mock_post:
        response = interceptor.execute_chat_with_recall(
            base_url="http://localhost:11434",
            model_name="test-model",
            messages=[{"role": "user", "content": "Infinite recall loop test"}],
        )

    # Should only execute 2 recall loops, then break
    assert mock_post.call_count == 2


def test_ollama_client_integration_with_recall_interceptor():
    """Verify OllamaClient routes chat calls through recall_interceptor when configured."""
    mock_interceptor = MagicMock(spec=RecallStreamInterceptor)
    mock_interceptor.execute_chat_with_recall.return_value = ModelResponse(
        content="Recalled response",
        thinking="Thoughts with recall",
        tool_calls=[],
        model="batiai/gemma4-26b:iq4",
    )

    client = OllamaClient(recall_interceptor=mock_interceptor)
    client.model_supports_vision = lambda _name: False
    resp = client.chat(messages=[{"role": "user", "content": "Test"}])

    assert mock_interceptor.execute_chat_with_recall.called
    assert resp.content == "Recalled response"
    assert resp.thinking == "Thoughts with recall"


def test_build_system_prompt_includes_conscious_recall_principle():
    prompt = build_system_prompt(
        active_window_info={},
        current_dir="/tmp",
    )
    assert "CONSCIOUS ACTIVE RECALL:" in prompt
    assert "<recall>search keywords</recall>" in prompt
    assert "<recalled_memory>" in prompt


def test_agent_loop_active_recall_callback_and_interception():
    """Verify AgentLoop wires on_recall callback and active recall events fire during execution."""
    from eidos.core.loop import AgentLoop
    from eidos.memory.trajectory_logger import TrajectoryLogger
    from eidos.memory.user_store import UserStore

    with tempfile.TemporaryDirectory() as td:
        prof_file = Path(td) / "user_profile.yaml"
        traj_file = Path(td) / "episodes.jsonl"
        vector_db = Path(td) / "episodic.db"

        user_store = UserStore(storage_path=prof_file)
        traj_logger = TrajectoryLogger(storage_path=traj_file)

        mock_emb = MagicMock()
        mock_emb.embed_text.return_value = [0.1] * 768
        mock_emb.dimension = 768
        episodic_store = EpisodicStore(db_path=vector_db, embedding_client=mock_emb)

        # Seed past episode
        past_rec = EpisodeRecord(
            episode_id="ep_saved_command",
            task_goal="Inspect active pactl sink inputs",
            metadata=EpisodeMetadata(outcome="success", critic_score=1.0),
            trajectory=[
                StepRecord(
                    step=1,
                    tool_calls=[{"name": "execute_shell", "arguments": {"command": "pactl list sink-inputs"}}],
                )
            ],
            reflection=EpisodeReflection(
                summary="Used pactl sink-inputs command",
                lessons_learned="Worked perfectly",
            ),
        )
        episodic_store.index_episode(past_rec)

        # Create interceptor and OllamaClient
        interceptor = RecallStreamInterceptor(episodic_store=episodic_store, min_similarity=0.1)
        ollama = OllamaClient(recall_interceptor=interceptor)
        ollama.model_supports_vision = lambda _name: False

        mock_gk = MagicMock()
        mock_gk.evaluate.return_value = (True, "Allowed")

        loop = AgentLoop(
            config={"memory": {"enabled": True}},
            model_client=ollama,
            gatekeeper=mock_gk,
            user_store=user_store,
            trajectory_logger=traj_logger,
            episodic_store=episodic_store,
        )

        recalled_events = []
        loop.register_callback("on_recall", lambda q, text: recalled_events.append((q, text)))

        # Mock Ollama streaming
        resp1 = MagicMock()
        resp1.iter_lines.return_value = [
            json.dumps({"message": {"content": "Thinking...\n<recall>pactl sink inputs</recall>"}}).encode(),
        ]

        resp2 = MagicMock()
        resp2.iter_lines.return_value = [
            json.dumps({
                "message": {
                    "content": "Executing command now.",
                    "tool_calls": [
                        {"function": {"name": "finish_task", "arguments": {"summary": "Done using pactl."}}}
                    ],
                },
                "done": True,
            }).encode()
        ]

        chat_calls = []

        def mock_post(url, *args, **kwargs):
            if "/api/chat" in str(url):
                chat_calls.append((url, kwargs))
                return resp1 if len(chat_calls) == 1 else resp2
            emb_resp = MagicMock()
            emb_resp.status_code = 200
            emb_resp.json.return_value = {"embeddings": [[0.1] * 768]}
            return emb_resp

        with patch("requests.post", side_effect=mock_post):
            result = loop.run_task("Check audio sink")

        assert result["success"] is True
        assert len(recalled_events) == 1
        q, mem = recalled_events[0]
        assert q == "pactl sink inputs"
        assert "ep_saved_command" in mem


def test_execute_openai_chat_with_active_recall_interception():
    """Verify OpenAI SSE stream is intercepted upon </recall>, vector query is made, and continuation resumes."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_episodic_openai.db"

        mock_emb = MagicMock()
        mock_emb.embed_text.return_value = [0.1] * 768
        mock_emb.dimension = 768
        store = EpisodicStore(db_path=db_path, embedding_client=mock_emb)

        rec = EpisodeRecord(
            episode_id="ep_xrandr_001",
            task_goal="Set screen brightness with xrandr",
            metadata=EpisodeMetadata(outcome="success", critic_score=1.0),
            trajectory=[
                StepRecord(
                    step=1,
                    tool_calls=[{"name": "execute_shell", "arguments": {"command": "xrandr --output HDMI-0 --brightness 0.5"}}],
                )
            ],
            reflection=EpisodeReflection(
                summary="Active display is HDMI-0",
                lessons_learned="Use xrandr --output HDMI-0 --brightness",
            ),
        )
        store.index_episode(rec)

        interceptor = RecallStreamInterceptor(episodic_store=store, min_similarity=0.1)

        # Stream 1: Model emits reasoning with <recall> tag
        resp1 = MagicMock()
        resp1.iter_lines.return_value = [
            b'data: {"choices":[{"delta":{"reasoning_content":"Thinking about display... <recall>screen brightness</recall>"}}]}',
        ]

        # Stream 2: Continuation after memory splice
        resp2 = MagicMock()
        resp2.iter_lines.return_value = [
            b'data: {"choices":[{"delta":{"content":"I will set brightness now."}}]}',
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"execute_shell","arguments":"{\\"command\\": \\"xrandr --output HDMI-0 --brightness 0.5\\"}"}}]}}]}',
            b'data: [DONE]',
        ]

        callback_calls = []

        def on_recall(query, mem_text):
            callback_calls.append((query, mem_text))

        chat_calls = []

        def mock_post(url, *args, **kwargs):
            if "/chat/completions" in str(url):
                chat_calls.append((url, kwargs))
                return resp1 if len(chat_calls) == 1 else resp2
            emb_resp = MagicMock()
            emb_resp.status_code = 200
            emb_resp.json.return_value = {"embeddings": [[0.1] * 768], "embedding": [0.1] * 768}
            return emb_resp

        with patch("requests.post", side_effect=mock_post):
            response = interceptor.execute_openai_chat_with_recall(
                base_url="https://llm.chutes.ai/v1",
                api_key="test_key",
                model_name="google/gemma-4-31B-turbo-TEE",
                messages=[{"role": "user", "content": "Adjust screen brightness"}],
                on_recall_callback=on_recall,
            )

        assert len(chat_calls) == 2
        assert len(callback_calls) == 1
        assert callback_calls[0][0] == "screen brightness"
        assert "ep_xrandr_001" in callback_calls[0][1]

        assert response.has_tool_calls
        assert response.tool_calls[0].name == "execute_shell"
        assert response.tool_calls[0].arguments["command"] == "xrandr --output HDMI-0 --brightness 0.5"
        assert "screen brightness" in response.thinking


def test_execute_openai_chat_streaming_tool_call_chunks():
    """Verify tool calls split across multiple SSE deltas are properly merged."""
    interceptor = RecallStreamInterceptor()

    resp = MagicMock()
    resp.iter_lines.return_value = [
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_abc","type":"function","function":{"name":"execute_shell","arguments":""}}]}}]}',
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"command\\":"}}]}}]}',
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":" \\"ls\\"}"}}]}}]}',
        b'data: [DONE]',
    ]

    with patch("requests.post", return_value=resp):
        response = interceptor.execute_openai_chat_with_recall(
            base_url="https://llm.chutes.ai/v1",
            api_key="test_key",
            model_name="test-model",
            messages=[{"role": "user", "content": "list files"}],
        )

    assert response.has_tool_calls
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "call_abc"
    assert response.tool_calls[0].name == "execute_shell"
    assert response.tool_calls[0].arguments == {"command": "ls"}


def test_recall_stream_optimalx_notes_only():
    """Verify RecallStreamInterceptor correctly queries and splices OptimalX notes when no episodic match."""
    mock_optimalx = MagicMock()
    mock_optimalx.search_notes.return_value = [
        {
            "location": "Inventions > Smart Garden Solar Pump",
            "snippet": "12V DC brushless pump with 50W solar panel and 18650 battery buffer.",
        }
    ]

    interceptor = RecallStreamInterceptor(optimalx_bridge=mock_optimalx)

    notes = interceptor.lookup_optimalx_notes("solar pump", limit=2)
    assert len(notes) == 1
    assert "Smart Garden Solar Pump" in notes[0]["location"]

    formatted = interceptor.format_recalled_memory(result=None, query="solar pump", optimalx_notes=notes)
    assert "<recalled_notes>" in formatted
    assert "[OptimalX Note: Inventions > Smart Garden Solar Pump]" in formatted
    assert "12V DC brushless pump" in formatted
    assert "<recalled_memory>" not in formatted
    assert "Continuing plan:" in formatted


def test_recall_stream_both_episodic_and_optimalx_notes():
    """Verify RecallStreamInterceptor splices both episodic memory and OptimalX notes together."""
    mock_optimalx = MagicMock()
    mock_optimalx.search_notes.return_value = [
        {
            "location": "Inventions > Linux Mint Bot",
            "snippet": "Use bubblewrap bwrap sandbox for zero-egress bash execution.",
        }
    ]

    episodic_res = EpisodicSearchResult(
        episode_id="ep_bwrap_999",
        task_goal="Run isolated command in sandbox",
        outcome="success",
        similarity=0.92,
        summary="Mounted root as ro and workspace as rw",
        lessons_learned="Requires /tmp and /dev/null bind mounts",
        key_actions=["bwrap --ro-bind / / ..."],
    )

    interceptor = RecallStreamInterceptor(optimalx_bridge=mock_optimalx)
    formatted = interceptor.format_recalled_memory(
        result=episodic_res,
        query="sandbox bwrap",
        optimalx_notes=mock_optimalx.search_notes.return_value,
    )

    assert '<recalled_memory episode="ep_bwrap_999">' in formatted
    assert "- Past Task: Run isolated command in sandbox" in formatted
    assert "<recalled_notes>" in formatted
    assert "[OptimalX Note: Inventions > Linux Mint Bot]" in formatted
    assert "bubblewrap bwrap sandbox" in formatted
    assert "Continuing plan:" in formatted


def test_execute_chat_with_optimalx_recall_streaming():
    """Verify streaming Ollama chat intercepts <recall> and splices OptimalX note context."""
    mock_optimalx = MagicMock()
    mock_optimalx.search_notes.return_value = [
        {
            "location": "Research > Kokoro TTS",
            "snippet": "Run Kokoro TTS via local ONNX model with voice bm_george.",
        }
    ]

    interceptor = RecallStreamInterceptor(optimalx_bridge=mock_optimalx)

    resp1 = MagicMock()
    resp1.iter_lines.return_value = [
        json.dumps({
            "message": {"role": "assistant", "thinking": "Let me check user preferences... <recall>kokoro voice settings</recall>"},
            "done": False,
        }).encode()
    ]

    resp2 = MagicMock()
    resp2.iter_lines.return_value = [
        json.dumps({
            "message": {"role": "assistant", "content": "I will configure Kokoro with bm_george voice."},
            "done": True,
        }).encode()
    ]

    callback_calls = []
    def on_recall(query, mem_text):
        callback_calls.append((query, mem_text))

    call_count = [0]
    def mock_post(url, *args, **kwargs):
        call_count[0] += 1
        return resp1 if call_count[0] == 1 else resp2

    with patch("requests.post", side_effect=mock_post):
        response = interceptor.execute_chat_with_recall(
            base_url="http://localhost:11434",
            model_name="test-model",
            messages=[{"role": "user", "content": "Configure voice"}],
            on_recall_callback=on_recall,
        )

    assert call_count[0] == 2
    assert len(callback_calls) == 1
    assert callback_calls[0][0] == "kokoro voice settings"
    assert "<recalled_notes>" in callback_calls[0][1]
    assert "bm_george" in callback_calls[0][1]
    assert "configure Kokoro with bm_george voice" in response.content



