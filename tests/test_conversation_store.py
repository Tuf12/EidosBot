"""Unit tests for SQLite ConversationStore persistence and retrieval."""

import tempfile
import threading
from pathlib import Path

from eidos.memory.conversation_store import (
    ConversationStore,
    _generate_title_from_prompt,
)


def test_generate_title_from_prompt():
    assert _generate_title_from_prompt("") == "New Chat"
    assert _generate_title_from_prompt("   ") == "New Chat"
    assert _generate_title_from_prompt("what is fine tuning?") == "What is fine tuning?"
    assert _generate_title_from_prompt("```bash\nls -la\n```") == "Ls -la"

    long_prompt = "Can you please help me find all the duplicate python files across my entire home directory and clean them up"
    title = _generate_title_from_prompt(long_prompt, max_chars=40)
    assert len(title) <= 45
    assert title.endswith("...")


def test_conversation_store_crud():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        store = ConversationStore(db_path=tmp.name)

        # 1. Create conversation
        conv_id = store.create_conversation(title="Test Topic", user_id="user_1")
        assert conv_id is not None

        # 2. Get conversation
        conv = store.get_conversation(conv_id)
        assert conv is not None
        assert conv["id"] == conv_id
        assert conv["title"] == "Test Topic"
        assert conv["userId"] == "user_1"
        assert conv["createdAt"] > 0
        assert conv["updatedAt"] >= conv["createdAt"]

        # 3. Update title
        store.update_conversation_title(conv_id, "Updated Topic")
        conv_updated = store.get_conversation(conv_id)
        assert conv_updated["title"] == "Updated Topic"

        # 4. Add messages
        m1 = store.add_message(conv_id, role="user", content="Hello, EidosBot!")
        m2 = store.add_message(conv_id, role="assistant", content="Hello! How can I assist you?", parent_id=m1)
        assert m1 is not None
        assert m2 is not None

        # 5. Fetch messages
        messages = store.get_messages(conv_id)
        assert len(messages) == 2
        assert messages[0]["role"] == "USER"
        assert messages[0]["content"] == "Hello, EidosBot!"
        assert messages[1]["role"] == "ASSISTANT"
        assert messages[1]["parentId"] == m1

        # 6. Clear messages
        store.clear_conversation_messages(conv_id)
        assert len(store.get_messages(conv_id)) == 0
        assert store.get_conversation(conv_id) is not None

        # 7. Delete conversation
        assert store.delete_conversation(conv_id) is True
        assert store.get_conversation(conv_id) is None
        assert store.delete_conversation(conv_id) is False

        store.close()


def test_conversation_store_auto_titling():
    store = ConversationStore(db_path=":memory:")

    # When created with default "New Chat", the first user prompt auto-derives a descriptive title
    conv_id = store.create_conversation(title="New Chat")
    store.add_message(conv_id, role="user", content="Explain quantum computing in simple terms")

    conv = store.get_conversation(conv_id)
    assert conv["title"] == "Explain quantum computing in simple terms"

    # Second user message does NOT overwrite the title
    store.add_message(conv_id, role="user", content="And what about quantum entanglement?")
    conv2 = store.get_conversation(conv_id)
    assert conv2["title"] == "Explain quantum computing in simple terms"

    store.close()


def test_conversation_store_most_recent_and_active():
    store = ConversationStore(db_path=":memory:")

    # Initially empty
    assert store.get_most_recent_conversation() is None

    # get_or_create_active_conversation_id should create one
    c1 = store.get_or_create_active_conversation_id()
    assert c1 is not None
    assert store.get_most_recent_conversation()["id"] == c1

    # Create a second conversation
    c2 = store.create_conversation(title="Second Chat")
    store.add_message(c2, role="user", content="Work on second task")

    # Most recent should now be c2
    recent = store.get_most_recent_conversation()
    assert recent["id"] == c2

    # Touch c1 to make c1 the most recent again
    store.touch_conversation(c1)
    recent_after = store.get_most_recent_conversation()
    assert recent_after["id"] == c1

    store.close()


def test_conversation_store_list_and_search():
    store = ConversationStore(db_path=":memory:")

    c1 = store.create_conversation(title="Docker Container Debugging")
    store.add_message(c1, role="user", content="Why is my docker container restarting?")
    store.add_message(c1, role="assistant", content="Check docker logs with `docker logs <id>`")

    c2 = store.create_conversation(title="Python FastAPI Development")
    store.add_message(c2, role="user", content="How do I add JWT authentication?")

    # List all
    all_convs = store.list_conversations()
    assert len(all_convs) == 2
    assert all_convs[0]["id"] == c2
    assert all_convs[0]["message_count"] == 1
    assert "JWT" in all_convs[0]["last_preview"]
    assert all_convs[1]["id"] == c1
    assert all_convs[1]["message_count"] == 2
    assert "docker logs" in all_convs[1]["last_preview"]

    # Search by title keyword
    results_docker = store.list_conversations(search_query="Docker")
    assert len(results_docker) == 1
    assert results_docker[0]["id"] == c1

    # Search by message content keyword
    results_jwt = store.list_conversations(search_query="authentication")
    assert len(results_jwt) == 1
    assert results_jwt[0]["id"] == c2

    # Search non-matching
    assert len(store.list_conversations(search_query="nonexistent_keyword_xyz")) == 0

    store.close()


def test_conversation_store_persistence_across_restarts():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db_path = tmp.name

        # Session 1: Create conversation and messages
        store1 = ConversationStore(db_path=db_path)
        c1 = store1.create_conversation(title="First Chat")
        store1.add_message(c1, role="user", content="Message 1")
        store1.add_message(c1, role="assistant", content="Reply 1")

        c2 = store1.create_conversation(title="Second Chat")
        store1.add_message(c2, role="user", content="Message 2")
        store1.add_message(c2, role="assistant", content="Reply 2")
        store1.close()

        # Session 2 (simulate app restart): Connect new store instance
        store2 = ConversationStore(db_path=db_path)
        recent = store2.get_most_recent_conversation()
        assert recent is not None
        assert recent["id"] == c2
        assert recent["title"] == "Second Chat"

        messages = store2.get_messages(recent["id"])
        assert len(messages) == 2
        assert messages[0]["content"] == "Message 2"
        assert messages[1]["content"] == "Reply 2"

        all_convs = store2.list_conversations()
        assert len(all_convs) == 2

        store2.close()


def test_conversation_store_concurrency():
    store = ConversationStore(db_path=":memory:")
    c_id = store.create_conversation(title="Concurrent Chat")

    def worker(worker_id: int):
        for i in range(10):
            store.add_message(c_id, role="user", content=f"Worker {worker_id} message {i}")

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    messages = store.get_messages(c_id)
    assert len(messages) == 50
    store.close()
