"""Edit-and-retry rewinds stored turns before the prompt runs again."""

import time
from unittest.mock import MagicMock

from eidos.gateway.server import RemoteGatewayServer
from eidos.memory.conversation_store import ConversationStore


class FakeLoop:
    """Minimal agent loop with a real conversation store and no running task."""

    def __init__(self, store: ConversationStore, conversation_id: str):
        self.conversation_store = store
        self.active_conversation_id = conversation_id
        self.conversation_history = []
        self.calls = []
        self.history_at_call = None

    def reload_conversation_history(self) -> None:
        self.conversation_history.clear()
        for message in self.conversation_store.get_messages(self.active_conversation_id):
            role = (message.get("role") or "").lower()
            if role in ("user", "assistant"):
                self.conversation_history.append(
                    {"role": role, "content": message.get("content", "")}
                )

    def run_task(self, prompt: str, speak_summary: bool = True):
        self.calls.append(prompt)
        self.history_at_call = [dict(item) for item in self.conversation_history]
        return {"success": True, "summary": "ok", "detail": "ok"}


def _wait_for_call(loop: FakeLoop) -> None:
    deadline = time.time() + 2
    while not loop.calls and time.time() < deadline:
        time.sleep(0.02)
    assert loop.calls, "edit and retry did not dispatch a task"


def _seed(tmp_path):
    store = ConversationStore(db_path=tmp_path / "edit_retry.db")
    conversation_id = store.create_conversation("Edit Retry")
    first = store.add_message(conversation_id, role="user", content="first")
    store.add_message(conversation_id, role="assistant", content="reply one")
    second = store.add_message(conversation_id, role="user", content="second")
    store.add_message(conversation_id, role="assistant", content="reply two")
    loop = FakeLoop(store, conversation_id)
    loop.reload_conversation_history()
    gateway = RemoteGatewayServer(
        agent_loop=loop,
        host="127.0.0.1",
        port=0,
        auth_token="edit-retry-token",
        screen_capture=MagicMock(),
    )
    return store, loop, gateway, first, second


def test_edit_drops_later_turns_and_reruns_new_prompt(tmp_path):
    store, loop, gateway, _first, second = _seed(tmp_path)

    ok = gateway.edit_and_retry("second, but clearer", message_id=second)
    assert ok is True
    _wait_for_call(loop)

    assert loop.calls == ["second, but clearer"]
    assert [item["content"] for item in loop.history_at_call] == ["first", "reply one"]
    remaining = store.get_messages(loop.active_conversation_id)
    assert [item["content"] for item in remaining] == ["first", "reply one"]
    store.close()


def test_retry_from_assistant_uses_preceding_user_prompt(tmp_path):
    store, loop, gateway, _first, _second = _seed(tmp_path)

    ok = gateway.edit_and_retry("second", message_index=3)
    assert ok is True
    _wait_for_call(loop)

    assert loop.calls == ["second"]
    assert [item["content"] for item in loop.history_at_call] == ["first", "reply one"]
    store.close()


def test_client_message_id_falls_back_to_index(tmp_path):
    store, loop, gateway, _first, _second = _seed(tmp_path)

    ok = gateway.edit_and_retry(
        "first, rewritten",
        message_id="user_1710000000000",
        message_index=0,
    )
    assert ok is True
    _wait_for_call(loop)

    assert loop.calls == ["first, rewritten"]
    assert loop.history_at_call == []
    assert store.get_messages(loop.active_conversation_id) == []
    store.close()


def test_edit_retry_refuses_while_a_task_is_running(tmp_path):
    store, loop, gateway, _first, second = _seed(tmp_path)
    gateway.current_state = "thinking"

    ok = gateway.edit_and_retry("second", message_id=second)
    assert ok is False
    assert loop.calls == []
    assert len(store.get_messages(loop.active_conversation_id)) == 4
    store.close()
