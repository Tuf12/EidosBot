# Specification: Conversation Recall

**Document:** `docs/specs/SPEC_CONVERSATION_RECALL.md`  
**Plan:** [`docs/plans/PLAN_CONVERSATION_RECALL.md`](../plans/PLAN_CONVERSATION_RECALL.md)  
**Status:** Draft / Specification  
**Primary modules:** `eidos/memory/conversation_store.py`, `eidos/memory/conversation_index.py` (new), `eidos/core/loop.py`, `eidos/memory/recall_stream.py`

**Related:** [SPEC_EPISODIC_DYNAMIC_SCOPING.md](SPEC_EPISODIC_DYNAMIC_SCOPING.md) named `<recalled_conversation>` and did not specify it. Episodic retrieval stays in the episodic specs. Job scope on a conversation stays as [PROJECT_JOBS.md](../PROJECT_JOBS.md) describes it.

---

## 1. Executive Summary

### 1.1. Problem

Each task replays the last 16 user and assistant messages from the active conversation. A finished turn is stored as the `finish_task` line. A stopped or stalled turn is stored as a trace of up to about 8,000 characters. Inside one task, tool results accumulate until the prompt reaches 48,000 characters, and then older tool output is cut down.

That slice is a lot of turns and a small amount of durable content. A new conversation drops the slice. Facts that never became a job decision, a profile entry, or an episode are gone. Compacting the chat has the same hole: there is no residue to search later.

Meta and Chutes are not given a context cap. The 64k `num_ctx` applies only to local Ollama. A longer raw transcript makes Muse lazier because the current goal sits under a trail of earlier answers that already look finished. Raising 16 toward the full history feeds that. The model cannot search for the one relevant turn. It can only reread what the prompt contains.

### 1.2. Decision

Treat past chat the way episodes are already treated. Keep a short verbatim tail. Embed older turns as the user goal plus the assistant summary. At the start of a task, retrieve a few scoped hits and put them in a small block beside `<episodic_context>`. When a conversation is cut, write a short residue onto that conversation so a later chat can still find it.

Tool observations stay out of this index. Episodes already keep those.

### 1.3. Solution

1. A conversation-turn index beside `eidos.db`, embedded with the same local `nomic-embed-text` path the episodic store uses.
2. Turn-start retrieval of one to three past turns, job-scoped, above a similarity floor, excluding anything already in the verbatim tail.
3. A verbatim tail of the last three user and assistant messages, replacing the flat last-16 replay.
4. A residue summary stored on the conversation when the user starts a new chat or clears the active one.
5. Mid-thought `<recall>` can search the same index and splice `<recalled_conversation>`.

---

## 2. What Gets Indexed

Index one record per completed exchange: the user message paired with the assistant message that followed it.

| Field | Source |
|---|---|
| `conversation_id` | `Conversation.id` |
| `message_id` | Assistant `Message.id` |
| `job_id` | `Conversation.jobId` at the time the turn was saved. Empty string when the chat is unscoped. |
| `text` | User message, then the assistant content that `_remember_turn` stored. |
| `created_at` | Assistant message timestamp |

Successful turns are already a short headline. Stalled and stopped turns are the attempt detail, capped as they are today. Do not embed tool results, thoughts stored only in message metadata, or the live step transcript.

Re-embed a turn when its stored assistant content changes. Delete index rows when the conversation or those messages are deleted.

---

## 3. What the Prompt Receives

On each task, after the system prompt is assembled:

1. **Verbatim tail.** The last three user and assistant messages of the active conversation, in order. This replaces `conversation_history[-16:]`.
2. **Retrieved turns.** Up to three older exchanges whose embedding is close to the new user goal. Render them as `<conversation_context>`. Each hit names the conversation title, the job id when it has one, and the user and assistant text. Skip a hit whose message is already in the tail.
3. **Residue.** If this conversation has a residue summary, include it once, above the retrieved turns.

`<episodic_context>` is unchanged and stays a separate block. A retrieved chat turn is not a proven tool pattern.

The live task still appends tool results and still compacts them at 48,000 characters. This spec does not raise that budget.

### 3.1. Rendered shape

```xml
<conversation_context>
Residue:
- Left the brightness task unfinished. HDMI-0 was the connected output.

Earlier exchange (Coil lantern, conversation "Shade notes"):
- User: the shade is too warm at night
- Assistant: Set the night preset to 2700K and left the day preset alone.

Earlier exchange (this conversation):
- User: where did we put the OpenSCAD source
- Assistant: artifacts/lantern.scad in the Coil lantern job.
</conversation_context>
```

Empty retrieval omits the block. A miss is not written into the prompt as "nothing found."

---

## 4. Scope

Use the job already stored on the conversation (`Conversation.jobId`).

| Active conversation | Which past turns are eligible |
|---|---|
| Bound to a job | Turns from any conversation with that same `job_id` |
| Explicitly unscoped | Turns from other explicitly unscoped conversations |
| Not yet bound | Turns from this conversation only |

A turn from a different job is dropped. Entity-graph overlap does not pull chat across jobs. Chat is not an episode.

Changing a conversation's job does not rewrite old index rows. Those rows keep the `job_id` they were saved with. New turns use the new job.

---

## 5. Residue

Starting a new conversation, or clearing the active one, writes a residue onto the conversation being left.

The residue is a few sentences: decisions made, files named, and threads left open. It is produced from the verbatim messages of that conversation, not from tool logs. It is stored on the conversation record and included in that conversation's index so a later goal can retrieve it.

The job brief is not edited by this step. Decisions that belong in the brief still go through `update_job_brief`.

A conversation with no messages gets no residue.

---

## 6. Active Recall

`<recall>` keeps searching episodes and OptimalX notes. It also searches this index with the same scope rules and the same similarity floor.

A chat hit is spliced as its own block, after `<recalled_memory>` and `<recalled_notes>` when those exist:

```xml
<recalled_conversation conversation="Shade notes" job_id="coil-lantern">
- User: the shade is too warm at night
- Assistant: Set the night preset to 2700K and left the day preset alone.
</recalled_conversation>
```

No chat hit omits the block. The episodic "nothing found" line is unchanged when the episode search misses.

---

## 7. Similarity and Budgets

- Embed with the existing local embedding model. A failed embed skips retrieval for that request and leaves the verbatim tail in place.
- Minimum similarity matches episodic recall (`recall_min_similarity`, default 0.20) unless a later setting splits them.
- At most three retrieved exchanges, and at most three tail messages on each side of the role pair (three user, three assistant, in order).
- Retrieved text uses the stored assistant content. Do not expand metadata thoughts back into the prompt.

---

## 8. Out of Scope

- Indexing job notes, workspace documents, or OptimalX as a general corpus.
- Sending the raw 16-message window in addition to the retrieved block.
- Writing into a provider's private reasoning channel.
- Cross-job chat retrieval through the entity graph.
- Automatic edits to `job.yaml`.

---

## 9. Tests

- A finished exchange is indexed as user text plus assistant headline. A tool observation from the same task is not a row.
- With six older exchanges in one job and a new goal that matches one of them, the prompt contains that exchange inside `<conversation_context>` and only the last three tail messages verbatim.
- A matching exchange from another job is absent.
- An unscoped conversation does not retrieve a job-scoped exchange.
- The tail exchange is not repeated in the retrieved block.
- Clearing a conversation stores a residue and a later goal in a new unscoped chat can retrieve it.
- `<recall>` with a chat match emits `<recalled_conversation>` and still emits `<recalled_memory>` when an episode also matches.
