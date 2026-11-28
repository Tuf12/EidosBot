# Specification: Subagents

**Document:** `docs/specs/SPEC_SUBAGENTS.md`  
**Status:** Draft / Specification  
**Primary modules:** `eidos/subagents/` (new), `eidos/core/loop.py`, `eidos/core/tool_registry.py`, `eidos/models/router.py`, `eidos/integrations/mcp_client.py`, `eidos/ui/settings_dialog.py`, `eidos/ui/pill.py`, `eidos/gateway/server.py`

---

## 1. Executive Summary

### 1.1. Problem

Eidos has one foreground agent loop. The batch runner can process a checkpointed queue on a single background thread, one item at a time, and each item is one fresh model call rather than an agent. Eidos cannot hand an open-ended piece of a task to another worker, keep talking to the user, and pick up the result later.

A second local Ollama call is not available for this. The GPU runs one heavy model at a time (`SPEC_COMPUTE_RESOURCE_POLICY.md`).

### 1.2. Decision

A subagent is a background run of the same harness the foreground agent uses: the tool loop, the safety gate, the sandbox, and the existing MCP client. It is not a second copy of the conversation. Each run starts with an empty message list and a system prompt Eidos writes for that task. Model calls go to the configured cloud provider only.

Eidos creates, revises, and summons these workers while it is doing a task. A settings tab lists them and can retire one. Retirement is how the user withdraws a worker. It is not a step Eidos waits on before the first summon.

### 1.3. Solution

1. A folder on disk for each subagent: role, manifest, run history, working memory, and an optional Python hook module.
2. A harness that runs that folder on a background thread, with a small concurrency cap.
3. Foreground tools so Eidos can create a worker, summon it, check it, and stop it.
4. A contract with Live Conversation: a running subagent does not hold the microphone, does not speak, and does not take the local inference slot.

---

## 2. What a Subagent Is

| | Foreground Eidos | Subagent |
|---|---|---|
| Loop, tools, safety gate, sandbox, MCP client | Owns them | Uses the same implementations |
| Message list | The active conversation | Empty at the start of every run |
| System prompt | Kernel prompt, job, skill, profile, episodic memory | Harness preamble plus the prompt for this run |
| Model | Local-first, per `models.provider_preference` | Cloud only |
| Voice | Speaks when a turn or Live Conversation asks it to | Never speaks |
| Busy flag the microphone watches | Sets it for the duration of `run_task` | Does not set it |
| Can summon a subagent | Yes | No |

The shared part is the hands. The prompt, the transcript, and the memory are the worker's own.

Skills stay procedures. Jobs stay the project folder and the brief. The batch queue stays the checkpointed list of similar items. A subagent is the worker Eidos sends after an open piece of work: research this, draft this, operate this integration, then come back with a summary.

---

## 3. On-Disk Package

Root: `subagents.dir` in config, default `~/EidosWorkspace/subagents/<name>/`.

```text
~/EidosWorkspace/subagents/<name>/
  AGENT.md          # standing role and procedure
  manifest.yaml     # tool ceiling, MCP servers, step cap
  memory.md         # short working memory injected into the next run
  history.jsonl     # one JSON object per line, one line per finished run
  hooks.py          # optional
  runs/<run_id>.jsonl   # step trace for that run
```

`<name>` is a slug: lowercase letters, digits, and hyphens. The harness creates the directory. Eidos writes `AGENT.md`, `manifest.yaml`, and `hooks.py` through tools. The harness is the only writer of `history.jsonl`, `memory.md`, and `runs/`.

### 3.1. `AGENT.md`

Markdown. No required frontmatter. This is the standing system prompt for the worker: objective, rules, steps, and the shape of the result.

A summon may pass a `system_prompt` that replaces this body for that run only. The file changes only when Eidos calls `update_subagent` or passes `save_prompt: true` on summon.

### 3.2. `manifest.yaml`

```yaml
name: prior-art
description: Search prior art and write a short brief.
tools:
  - execute_shell
  - load_skill
  - close_skill
  - list_skills
  - finish_task
mcp_servers: []
allow_desktop_input: false
max_steps: 40
```

| Field | Meaning |
|---|---|
| `name` | Slug. Matches the folder name. |
| `description` | One line Eidos sees in `list_subagents`. |
| `tools` | Tool names the worker may be given. Intersected with tools that exist on the foreground registry. |
| `mcp_servers` | Configured MCP server names whose tools are attached for the run. |
| `allow_desktop_input` | When false, click, type, and hotkey tools are omitted even if listed. Default false. |
| `max_steps` | Step fuse for one run. Capped by `agent.safety_step_fuse`. |

An empty `tools` list means the default set in section 6.2. A summon cannot add a tool or an MCP server that the manifest does not already allow. Widening access is an `update_subagent` on the manifest, which is a foreground action and is written to the audit log.

### 3.3. `memory.md` and `history.jsonl`

`memory.md` is what the next run sees. The harness rewrites it at the end of a successful or failed run with one cloud call: the previous memory plus the run summary, capped at about 1,500 words. If that call fails, the previous `memory.md` is kept and the run is still recorded.

`history.jsonl` is the log the worker can use as a memory base. One line:

```json
{
  "run_id": "run_20261124_150201_ab12",
  "started_at": "2026-11-24T15:02:01Z",
  "finished_at": "2026-11-24T15:06:40Z",
  "status": "succeeded",
  "task": "Find prior art for the clip-on filter.",
  "summary": "Three relevant filings. Notes are in the job folder.",
  "artifacts": ["~/EidosWorkspace/jobs/filter/notes/prior-art.md"],
  "steps": 11,
  "error": null
}
```

`status` is `succeeded`, `failed`, or `cancelled`. The step trace lives in `runs/<run_id>.jsonl` and is not copied into the prompt. The worker reads a past run on purpose with the history tool the harness gives it (section 6.2).

---

## 4. Harness

New package `eidos/subagents/`. `AgentLoop` owns one `SubagentHarness` the way it owns `BatchJobRunner`.

### 4.1. Prompt for one run

The message list is exactly:

1. **System.** A fixed preamble, then the run prompt, then `memory.md` when the file has content.
2. **User.** The task string.

The preamble is short and always present. It tells the worker that it is a background subagent, that it must use `finish_task` with a summary, that it must not address the user as if it were the foreground chat, and that it must not try to summon other workers. It names the run id and, when the foreground passed one, the job directory the worker may write into.

The run prompt is `system_prompt` from the summon when that argument is non-empty. Otherwise it is the body of `AGENT.md`.

These are not copied into the run: the foreground conversation, the kernel prompt from `eidos/core/context.py`, the active skill body, the user profile, episodic recall, and the Live Conversation voice instruction.

### 4.2. Loop

The harness runs the same step cycle as `AgentLoop.run_task`: call the model with the assembled tools, dispatch the tool, append the result, repeat until `finish_task`, a stall, `max_steps`, cancel, or the kill switch.

Each model call uses `ModelRouter.chat(..., priority="background")` and then refuses the result if `last_provider_used` is `ollama`. When the cloud client is missing or the cloud call fails, the run ends as `failed` with the error recorded. It does not fall through to the local model. Section 5 is the rule.

Tool dispatch goes through the foreground safety gate, allowlist, audit log, and Bubblewrap shell. A level-3 confirmation pauses that subagent and raises the existing approval card on the pill, the web app, and Telegram. It does not cancel the foreground turn, and it does not move Live Conversation out of `listening`. Silence is not approval.

`hooks.py`, when present, is loaded from that subagent folder. The harness calls only these names, and only when they are defined:

| Hook | When | Contract |
|---|---|---|
| `prepare_task(task, memory, manifest) -> str` | Before the user message is sent | Returns the task text. |
| `on_step(step) -> None` | After a tool result is appended | Observes. The return value is ignored. |
| `finish(transcript, status) -> str` | After the loop stops | Returns the summary stored in history and handed back to the foreground. |

A hook exception fails the run. A hook does not receive the model client, the MCP client, or a way to mark a tool call as already approved. Task code that is a plain script still goes through `execute_shell` and the sandbox.

### 4.3. Threads and the cap

Each run is a daemon thread named `Subagent-<name>-<run_id>`. `subagents.max_concurrent` (default 2) is the number of runs whose model loop may be active. A summon past the cap is accepted, stored as `queued`, and started when a slot frees. `queue_when_busy: false` makes that summon return `busy` instead of queueing.

The foreground `run_task` returns as soon as the summon tool returns the run id. The parent turn does not block on the worker.

### 4.4. When a run ends

1. Append the `history.jsonl` line and close the step trace.
2. Rewrite `memory.md` as in section 3.3.
3. Clear the run off the active set.
4. Emit `on_background_activity` so the pill can show the remaining workers, and broadcast `subagent_run` (section 8).
5. Store the summary where the next foreground turn can see it (section 7).

The harness does not call `run_task`, does not append an assistant chat line by itself, and does not call Kokoro.

---

## 5. Models

Subagent calls follow `SPEC_COMPUTE_RESOURCE_POLICY.md` with a stricter route than the reflector:

- The call is `priority="background"`.
- The provider must be the configured cloud (`models.cloud` or `models.meta`). Local Ollama is a failed run, not a fallback.
- The call does not acquire the local inference slot and does not change `is_foreground_busy()`.
- `model` on the summon, when set, is a cloud model id passed as `model_override`. An Ollama model name is rejected.

`subagents.max_concurrent` is the cap on simultaneous cloud chats from this harness. The reflector and the consolidator keep their own path and are not counted against it.

---

## 6. Tools

### 6.1. Foreground tools

Added to the base kernel, next to the job tools. Schemas stay short.

| Tool | Behavior |
|---|---|
| `list_subagents` | Name, description, retired flag, active run id if any, last status. Retired workers are omitted unless `include_retired` is true. |
| `create_subagent` | Writes the folder, `AGENT.md`, and `manifest.yaml`. The new worker is summonable immediately. |
| `update_subagent` | Replaces `AGENT.md`, manifest fields, or `hooks.py`. Manifest changes are audited. |
| `summon_subagent` | Starts or queues a run. Arguments: `name`, `task`, optional `system_prompt`, optional `save_prompt`, optional `job_id`, optional `model`. Returns `run_id` and `status` (`running` or `queued`). |
| `get_subagent_run` | Status, summary when finished, error when failed. |
| `read_subagent_history` | Recent `history.jsonl` lines for one name. Optional `run_id` returns that run's step trace. |
| `stop_subagent` | Cancels one run or every run for that name. |
| `retire_subagent` | Marks the manifest retired. `list_subagents` hides it and `summon_subagent` refuses it. The folder is kept. |

`summon_subagent` on an unknown name, a retired name, or a name whose `mcp_servers` are not configured returns an error and does not start a thread. A missing cloud key returns an error.

### 6.2. Tools inside a run

The worker receives the intersection of the manifest `tools` list and the registry. When `tools` is empty, the default set is:

`execute_shell`, `load_skill`, `close_skill`, `list_skills`, `get_current_time`, `finish_task`, `share_file`, `read_job_document`, `list_jobs`, `read_subagent_history`.

`read_subagent_history` is bound to that worker's own folder.

These names are never given to a worker, even if the manifest lists them:

`create_subagent`, `update_subagent`, `summon_subagent`, `retire_subagent`, `stop_subagent`, `create_job`, `focus_job`, `close_job`, `focus_integration`, `add_mcp_server`, `remove_mcp_server`, `set_secret`.

`focus_job` and `focus_integration` stay with the foreground so a worker cannot change the conversation's scope. The worker writes files through the shell and `share_file`. When `job_id` was passed, the preamble names that job directory and the sandboxed shell's writable roots include it for the run.

Desktop input tools (`click`, `click_element`, `type_text`, hotkeys, and the rest of the `desktop-gui` input set) require `allow_desktop_input: true`. While the foreground `run_task` is active, or Live Conversation is in any state other than `off`, a desktop-input call returns a tool error `desktop_busy` and the worker continues. Screenshots and window title stay available. One pointer at a time.

### 6.3. MCP

The worker uses the process `MCPClientManager` already owned by `AgentLoop`. It does not spawn a second session for a server the foreground has open. `call_tool` on that manager is already safe to call from another thread.

For each name in `mcp_servers`, the harness attaches the same scoped schemas `get_scoped_tool_schemas` would attach for that server, for the length of the run. A server with many tools uses `call_mcp_tool`, matching the foreground. Those schemas are removed from the worker when the run ends. The foreground's integration focus is left as it was.

---

## 7. Foreground Chat and Live Conversation

Live Conversation is specified in `SPEC_LIVE_CONVERSATION.md` and is not implemented yet. The harness obeys the contract now so the voice mode can be added without retuning workers.

- A subagent run must not set `_is_busy`, `_task_active`, or `ComputePolicy`'s foreground-busy bit.
- Live Conversation re-enters `listening` from the foreground turn and playback, as that spec already says. Subagent activity is not "the agent" in that sentence.
- The pill shows workers through the existing background-activity signal (`notify_background_activity`): the count and the active names.
- Completion stores a pending result keyed by `run_id`. At the start of the next foreground `run_task`, unread results are inserted as a short system block ("Background results") and then marked read. The user sees them in the thought path the UI already uses for system context. They are not a new spoken turn.
- Scheduled `speak` notifications keep the rule in `SPEC_SCHEDULED_WAKE.md`. A subagent completion is not one of those notifications.

The kill switch (`Ctrl+Alt+Esc`, Telegram `/kill`) cancels every running and queued subagent, aborts their cloud calls, and leaves their history line as `cancelled`. `AgentLoop.cancel_task` does the same for subagents as it already does for the batch worker.

---

## 8. Gateway

Broadcast on the existing gateway socket:

| Event | Payload | When |
|---|---|---|
| `subagent_run` | `{ "run_id", "name", "status", "summary" }` | Queued, started, succeeded, failed, cancelled |

`summary` is empty until the run has finished. The web app may show the line. It does not submit a follow-up task.

---

## 9. Settings

A **Subagents** tab on the existing settings dialog:

- Lists every folder: name, description, retired, last status, active run.
- **Stop** cancels the active run for that row.
- **Retire** and **Restore** call the same flag as `retire_subagent`. A restored worker can be summoned again.
- Edits `subagents.max_concurrent` and `subagents.queue_when_busy`.

The cloud account is the one already configured under Models. This tab does not collect a second API key.

Creating a worker does not require a visit to this tab. Eidos summons any worker that is not retired.

---

## 10. Configuration

Under `config.example.yaml`:

```yaml
subagents:
  dir: "~/EidosWorkspace/subagents"
  max_concurrent: 2
  max_steps: 40              # default when a manifest omits max_steps
  queue_when_busy: true
```

No cloud key and no `models.cloud` / `models.meta` block means `summon_subagent` returns an error that tells the foreground to configure a cloud provider.

---

## 11. Safety

- Worker tool calls use the same levels, allowlist, and `audit.log` as the foreground. The audit line includes `subagent` and `run_id`.
- Level 3 still always asks. The card names the worker. Declining fails that tool call and the worker sees the denial. The run continues unless the worker then finishes or stalls.
- `hooks.py` executes inside the Eidos process. It is trusted the way a skill Eidos writes is trusted, and it is limited to the three hooks in section 4.2.
- Nested summon is impossible because the summon tools are not in the worker's schema.
- Two workers may run at once, up to the cap. They share the MCP sessions and the sandbox. Desktop input stays single-owner (section 6.2).

---

## 12. Phases

Each phase is usable on its own.

1. **One worker.** Folder layout, create / list / summon / get / stop, cloud-only loop, default tool set, `history.jsonl`, pill background line, kill switch cancels the run. `max_concurrent` may be 1 in this phase.
2. **Several workers and reach.** The cap and the queue, manifest tool lists, MCP attachment on the shared client, `hooks.py`, `memory.md` rewrite, `read_subagent_history`, pending results on the next foreground turn, gateway event.
3. **Settings and the voice contract.** The settings tab, retire / restore, desktop-input lock while the foreground or Live Conversation is active. Phase 3 does not implement Live Conversation. It only keeps the flags that spec needs.

---

## 13. Acceptance

- `summon_subagent` returns a `run_id` while the worker is still running, and the foreground turn can `finish_task` before the worker does.
- A worker run with the local provider forced still does not call Ollama. The run status is `failed` when the cloud provider is missing.
- The worker's first model request contains the summon prompt or `AGENT.md`, and does not contain the foreground chat transcript.
- Two summons with `max_concurrent: 2` both reach `running`. A third is `queued` and starts after one finishes.
- A manifest that omits `add_mcp_server` does not put that tool in the worker schema. A listed MCP server is callable during the run and absent from the foreground schema afterward unless the foreground had already focused it.
- After a run, `history.jsonl` has one new line and the next summon's system content includes the updated `memory.md`.
- While a worker runs, `is_foreground_busy()` stays false unless the foreground itself is in a turn. The pill shows the worker as background activity.
- The kill switch stops the worker and writes `cancelled`.
- A retired worker is absent from `list_subagents` and `summon_subagent` returns an error.
- A desktop-input call from a worker while a foreground turn is active returns `desktop_busy` and does not move the pointer.

---

## 14. Out of Scope

- A local-model pool, a second Ollama process, or any subagent call that takes the GPU inference slot.
- Subagents that summon their own subagents.
- A worker with its own Live Conversation session, microphone, or Kokoro stream.
- Replacing the batch queue. Item lists stay on `BatchJobRunner`.
- A separate vector store per worker. Memory is `memory.md` plus `history.jsonl`.
- Editing another product's MCP server, or starting a server that is not already in the MCP config.

---

## 15. Related Specs

- [`SPEC_COMPUTE_RESOURCE_POLICY.md`](SPEC_COMPUTE_RESOURCE_POLICY.md) — one local heavy model; background work stays off that slot.
- [`SPEC_LIVE_CONVERSATION.md`](SPEC_LIVE_CONVERSATION.md) — the microphone follows the foreground turn. Subagent activity must not count as that turn.
- [`SPEC_SCHEDULED_WAKE.md`](SPEC_SCHEDULED_WAKE.md) — proactive speech stays on the timer path, not on worker completion.
