# Implementation Plan: Conversation Recall

**Specification:** [`docs/specs/SPEC_CONVERSATION_RECALL.md`](../specs/SPEC_CONVERSATION_RECALL.md)  
**Related Systems:** [`docs/PROJECT_JOBS.md`](../PROJECT_JOBS.md), [`docs/specs/SPEC_EPISODIC_DYNAMIC_SCOPING.md`](../specs/SPEC_EPISODIC_DYNAMIC_SCOPING.md)  
**Target Modules:** `eidos/memory/conversation_index.py`, `eidos/memory/conversation_store.py`, `eidos/memory/recall_stream.py`, `eidos/core/loop.py`, `eidos/memory/__init__.py`  
**Status:** Not started  
**Estimated Complexity:** Medium (a small vector index, a shorter prompt tail, residue on chat boundaries, and one more block in `<recall>`)

---

## 1. Objectives & Architectural Guardrails

### Primary Goal
Replace the flat replay of the last 16 chat messages with a short verbatim tail plus a scoped search of older exchanges. A new chat or a cleared chat keeps a residue that a later task can retrieve. Episodes, the job brief, and the user profile stay as they are.

### Non-Negotiable Guardrails
1. **No second corpus.** Index completed chat exchanges only. Do not embed tool results, metadata thoughts, job notes, workspace files, or OptimalX notes.
2. **Do not grow the raw transcript.** The verbatim tail is the last three user messages and the last three assistant messages, in their original order. The old `conversation_history[-16:]` slice is removed. Retrieved hits are at most three, and a hit already in the tail is dropped. An empty search adds no "nothing found" block.
3. **Job boundary.** A job-scoped conversation searches turns saved with that same `job_id`. An explicitly unscoped conversation searches other explicitly unscoped turns. A conversation that has never been bound searches only its own turns. A different job never matches. The entity graph is not consulted.
4. **Embed on CPU, and survive a miss.** Use the existing `OllamaEmbeddingClient` (`nomic-embed-text`, `num_gpu` from `memory.embedding_num_gpu`). If the embed call fails, skip retrieval for that request and still send the verbatim tail.
5. **Chat switching stays local.** Residue text is built from the stored user and assistant messages. It does not call Meta, Chutes, or the local chat model. Starting or clearing a chat must not wait on a generation.
6. **Tests stay off the app.** Unit tests use a temp SQLite file and a fake embedder that returns a fixed vector per string. Do not boot `eidos.main`, the gateway, or Ollama.
7. **Thread safety.** Index writes take the index lock. They happen after `ConversationStore` has committed the message, not inside its lock.

---

## 2. File Modification & Creation Inventory

| File | Change Type | Responsibility |
|---|---|---|
| `eidos/memory/conversation_index.py` | **New File** | SQLite index of exchanges. Upsert, delete, scoped search, `<conversation_context>` and `<recalled_conversation>` rendering. sqlite-vec when it loads, numpy cosine otherwise, same pattern as `EpisodicStore`. |
| `eidos/memory/conversation_store.py` | Modification | `residue` column on `Conversation`. Read and write it. List exchanges (user message paired with the following assistant message) for indexing and backfill. |
| `eidos/memory/types.py` | Modification | `ConversationHit` result: `message_id`, `conversation_id`, `conversation_title`, `job_id`, `user_text`, `assistant_text`, `similarity`, `is_residue`. |
| `eidos/core/loop.py` | Modification | Build the index next to the episodic store. Index on `_remember_turn`. Tail + retrieval in `_run_task_body`. Residue in `start_new_conversation` and `clear_history`. Drop index rows in `delete_conversation`. |
| `eidos/memory/recall_stream.py` | Modification | Search the conversation index inside `lookup` / `format_recalled_memory` and append `<recalled_conversation>` when there is a hit. |
| `eidos/memory/__init__.py` | Modification | Export `ConversationIndex` and `ConversationHit`. |
| `tests/test_conversation_recall.py` | **New File** | Index, scope, tail, residue, and recall XML. No app process. |

---

## 3. Detailed Phase-by-Phase Implementation Plan

### Phase 1: Index schema and search (`conversation_index.py`, `types.py`)

The database file lives next to the episodic store, default `~/.eidos/memory/vector_store/conversation_index.db`. Pass that path from the same memory config the episodic store already uses (`vector_store_path` / `episodic_db_path` parent directory). Tests pass a temp file.

```sql
CREATE TABLE IF NOT EXISTS conversation_turns (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    job_id TEXT NOT NULL DEFAULT '',
    scope_bound INTEGER NOT NULL DEFAULT 0,
    conversation_title TEXT NOT NULL DEFAULT '',
    user_text TEXT NOT NULL,
    assistant_text TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    is_residue INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_conv_turns_scope
    ON conversation_turns(job_id, scope_bound, conversation_id);
```

Store the embedding in a `vec0` table when sqlite-vec loads, and in a `embedding BLOB` column otherwise. `content_hash` is the hash of `user_text + assistant_text + job_id`. Upsert skips the embed call when the hash is unchanged.

`scope_bound` records whether the conversation had been explicitly scoped at save time (`Conversation.jobId` or `integrationFocus` was not NULL). `job_id` is the stripped job id, or `''` when there is none.

Search API:

```python
def search(
    self,
    query: str,
    *,
    conversation_id: str,
    job_id: Optional[str],
    scope_bound: bool,
    top_k: int = 3,
    min_similarity: float = 0.20,
    exclude_message_ids: Optional[Set[str]] = None,
) -> List[ConversationHit]:
```

Filter before ranking:

| `scope_bound` | `job_id` | Rows kept |
|---|---|---|
| `True` | non-empty | `job_id` equals that id |
| `True` | empty | `scope_bound = 1` and `job_id = ''` |
| `False` | any | `conversation_id` equals the active conversation |

Then drop `exclude_message_ids`, rank by cosine similarity, drop scores under `min_similarity`, and return at most `top_k`.

`render_context(residue, hits) -> str` builds the `<conversation_context>` block from the spec. It returns `""` when there is no residue and no hits.

`render_recall(hit) -> str` builds one `<recalled_conversation>` block.

### Phase 2: Pairing and residue column (`conversation_store.py`)

Add `residue TEXT` on `Conversation` with the same `PRAGMA table_info` migration `_ensure_scope_columns` already uses.

Add:

- `get_residue(conversation_id) -> str`
- `set_residue(conversation_id, text)`
- `iter_exchanges(conversation_id=None) -> List[Exchange]`

An exchange is a user `Message` and the next assistant `Message` in that conversation, ordered by `createdAt`. The assistant message id is the index key. Skip roles other than user and assistant. `iter_exchanges(None)` walks every conversation for backfill.

`get_conversation_scope` already reports `bound`, `job_id`, and `integration_focus`. The index uses that at save time. Later `set_conversation_scope` calls do not rewrite old rows.

### Phase 3: Save, delete, and backfill (`loop.py`)

Construct `ConversationIndex` in `AgentLoop.__init__` only when memory is enabled, sharing the episodic store's embedding client so a second Ollama client is not created.

On `_remember_turn`, after `add_message` returns the assistant id:

1. Read scope with `get_conversation_scope`.
2. Find the user message this assistant message answers (the last user row in that conversation before this id).
3. Upsert one turn. Assistant text is the `content` argument, which is already the headline or the capped attempt detail.

On `delete_conversation`, delete index rows for that `conversation_id` before the store delete. Do not write a residue. The conversation is gone.

Backfill once, after init, on a daemon thread:

- Select exchanges that have no index row.
- Cap the pass at 200 exchanges, newest first.
- Stop the pass on the first embed exception.
- Skip the pass when memory is disabled.
- Do not start a second pass later in the same process.

### Phase 4: Prompt tail and turn-start retrieval (`loop.py`)

In `_run_task_body`, where the last 16 messages are copied into `messages`:

1. Build the tail from `conversation_history`: walk from the end, keep up to three user and three assistant messages, then emit them in original order. This is the only raw history appended.
2. Embed `user_goal` and search the index with the active conversation's scope. `exclude_message_ids` is the set of assistant message ids in the tail, plus the active conversation's residue row id (`residue:<conversation_id>`).
3. If the active conversation has a residue, pass it to `render_context`.
4. When the rendered block is non-empty, append it to the system prompt after `<episodic_context>` (the system prompt is `messages[0]`). Do not put retrieved chat inside `<episodic_context>`.

Leave `_compact_context_if_needed(..., max_chars=48000)` on the live tool transcript alone.

### Phase 5: Residue on new chat and clear (`loop.py`)

`_build_residue(conversation_id) -> str`:

- Load that conversation's exchanges, newest last, at most eight.
- Each exchange becomes two lines, clipped: `User:` at 200 characters, `Assistant:` at 300.
- Join them. Cap the whole string at 1,500 characters.
- No messages means `""`.

`start_new_conversation`:

1. Build the residue from the conversation being left.
2. If it is non-empty, `set_residue` and upsert an index row with `message_id = "residue:<id>"`, `is_residue = 1`, `user_text = "Conversation residue"`, and `assistant_text` the residue. Scope fields are the scope being persisted today by `_persist_scope_on_conversation`.
3. Then create the new conversation, as the method already does.

`clear_history`:

1. Build the residue from the current messages.
2. Save it on the conversation and upsert the residue row.
3. Delete index rows for that conversation's real message ids (keep the residue row).
4. Then call `clear_conversation_messages`, as today.

A second clear with no messages does not replace an existing residue with an empty string.

### Phase 6: Mid-thought recall (`recall_stream.py`)

`RecallStreamInterceptor` takes an optional `conversation_index`.

Where `lookup_memory` and `lookup_optimalx_notes` run, also call `conversation_index.search` with the interceptor's current conversation id and scope. The loop already calls `set_current_scope`. Add `set_conversation_ref(conversation_id, job_id, scope_bound)` from the same place in `run_task`, and again if a task's conversation changes.

`format_recalled_memory` appends `render_recall` for the top chat hit after the notes block. No chat hit adds nothing. The existing "No past experience found" branch still runs only when both the episode and the notes are empty. A chat-only hit still returns the conversation block and does not claim the episode search missed.

### Phase 7: Tests (`tests/test_conversation_recall.py`)

Use a fake embedder: the vector is a hash of the text spread into 8 floats, so identical strings score 1.0 and unrelated strings score low. No Ollama.

1. **Index shape.** Saving a user message and an assistant headline creates one row. A tool-role message does not.
2. **Tail and retrieval.** Six exchanges in one job. The prompt-building helper returns the last three exchanges as the tail and puts an older matching exchange in `<conversation_context>`, not in the tail.
3. **Job isolation.** A similar exchange with another `job_id` is absent.
4. **Unscoped isolation.** An explicitly unscoped search does not return a job-scoped row. A not-yet-bound search returns only that conversation's rows.
5. **Residue.** `clear_history` on a conversation with messages stores a residue. A later unscoped conversation's search for a phrase from that residue returns the residue row. Clearing an empty conversation leaves the stored residue in place.
6. **Recall XML.** A chat hit renders `<recalled_conversation conversation="..." job_id="...">`. An episode hit still renders `<recalled_memory>` in the same string.

Run `pytest tests/test_conversation_recall.py` only. Do not start the app.

---

## 4. Rollout Checklist

- [ ] **Step 1: Index and result type**
  - Add `ConversationHit` and `ConversationIndex` with sqlite-vec or numpy fallback.
- [ ] **Step 2: Store pairing and residue column**
  - Migrate `Conversation.residue`. Add `iter_exchanges`, `get_residue`, `set_residue`.
- [ ] **Step 3: Write path and backfill**
  - Upsert from `_remember_turn`. Delete rows from `delete_conversation`. Backfill at most 200 missing exchanges on a daemon thread.
- [ ] **Step 4: Prompt**
  - Swap the 16-message slice for the tail. Append `<conversation_context>` only when retrieval or the active residue has text.
- [ ] **Step 5: Residue boundaries**
  - Write residue in `start_new_conversation` and `clear_history` before messages are dropped.
- [ ] **Step 6: `<recall>`**
  - Search the index from `RecallStreamInterceptor` and append `<recalled_conversation>`.
- [ ] **Step 7: Tests**
  - `pytest tests/test_conversation_recall.py`.

---

## 5. Explicitly Not in This Plan

- Raising `num_ctx`, setting `max_tokens` on Meta or Chutes, or changing the 48,000-character tool compaction.
- A model-written summary of the chat. The residue is clipped transcript text so a new chat does not wait on a generation.
- Rewriting index rows when `set_conversation_scope` changes a conversation's job.
- Cross-job chat hits through the entity graph.
- Editing `job.yaml` from the residue step.
