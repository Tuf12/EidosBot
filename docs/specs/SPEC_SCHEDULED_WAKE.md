# Specification: Scheduled Wake, Autonomous Timers & Proactive Notifications

**Document:** `docs/specs/SPEC_SCHEDULED_WAKE.md`  
**Feature overview:** [`docs/SCHEDULED_WAKE_AND_TIMERS.md`](../SCHEDULED_WAKE_AND_TIMERS.md)  
**Status:** Draft / Specification  
**Primary modules:** `eidos/scheduling/` (new), `eidos/core/loop.py`, `eidos/core/tool_registry.py`, `eidos/gateway/server.py`, `eidos/gateway/telegram_bot.py`, `eidos/main.py`

---

## 1. Executive Summary

### 1.1. Problem

EidosBot cannot persist agent intent to **act later**: arbitrary reminders, follow-up checks, or proactive summaries. Existing Telegram pushes are tied to batch jobs, risk approvals, and task completion—not to model-authored schedules.

### 1.2. Solution

Introduce a **durable scheduled timer subsystem** with:

1. **SQLite-backed `scheduled_timers`** store (shared across processes).
2. **`ReminderScheduler`** polling loop inside any running Eidos process.
3. **`ReminderExecutor`** that delivers **`notify`** payloads or dispatches **`agent_turn`** work.
4. **`eidos-scheduler` user systemd service** for fires when the PyQt HUD is closed.
5. **Agent tools** to create, list, and cancel timers.

---

## 2. Data Model

### 2.1. Table `scheduled_timers`

Location: same database as conversations (`config.memory.conversations_db_path`, default `~/.eidos/eidos.db`).

| Column | Type | Description |
|---|---|---|
| `timer_id` | TEXT PK | UUID v4 |
| `created_at` | REAL | `time.time()` UTC epoch |
| `updated_at` | REAL | Last mutation |
| `fire_at` | REAL | UTC epoch when timer becomes due |
| `timezone` | TEXT | IANA name for display / absolute parsing (optional) |
| `kind` | TEXT | `notify` \| `agent_turn` |
| `status` | TEXT | `pending` \| `fired` \| `cancelled` \| `failed` |
| `channels_json` | TEXT | JSON array: `telegram`, `desktop_chat`, `pwa`, `speak` |
| `payload_json` | TEXT | See §2.2 |
| `conversation_id` | TEXT | Nullable FK to conversations |
| `created_by` | TEXT | `agent` \| `user` \| `system` |
| `fired_at` | REAL | Nullable; set on successful fire |
| `last_error` | TEXT | Nullable failure reason |
| `lease_owner` | TEXT | Nullable process id holding fire rights |
| `lease_until` | REAL | Nullable epoch |

Indexes:

- `idx_scheduled_timers_due` on `(status, fire_at)` where `status = 'pending'`.
- `idx_scheduled_timers_conversation` on `(conversation_id, status)`.

### 2.2. Payload schemas (`payload_json`)

**`notify`:**

```json
{
  "title": "Render check",
  "body": "The batch job has been running for 45 minutes.",
  "parse_mode": "Markdown"
}
```

**`agent_turn`:**

```json
{
  "prompt": "Check whether job 'renders-q3' completed. If not, summarize progress from queue.json. If done, congratulate briefly.",
  "max_steps": 12,
  "speak_summary": false
}
```

---

## 3. Component Responsibilities

### 3.1. `ScheduledTimerStore` (`eidos/scheduling/store.py`)

- Schema migration on open (idempotent `CREATE TABLE IF NOT EXISTS`).
- CRUD: `create_timer`, `cancel_timer`, `list_timers`, `get_due_timers(now, limit)`.
- **Claim for execution:** `try_claim_timer(timer_id, owner, lease_ttl)` — atomic update `lease_owner`, `lease_until` where `status=pending` and lease expired.
- **Complete:** `mark_fired`, `mark_failed`.
- Thread-safe (`threading.Lock`).

### 3.2. `ReminderScheduler` (`eidos/scheduling/scheduler.py`)

- Daemon thread; interval from `scheduling.check_interval_seconds`.
- On each tick:
  1. If this process is **GUI primary**, refresh lease on pending claims (optional optimization).
  2. Query due timers with `fire_at <= now`.
  3. For each: `try_claim` → hand to `ReminderExecutor` on worker thread (single-flight or small pool).
- **`start()` / `stop()`** tied to `AgentLoop` lifecycle and headless scheduler entrypoint.
- Respects `scheduling.enabled`.

### 3.3. `ReminderExecutor` (`eidos/scheduling/executor.py`)

Dependencies injected: `telegram_gw`, `gateway`, `agent_loop`, `config`.

| `kind` | Behavior |
|---|---|
| `notify` | For each channel: `telegram` → `broadcast_message`; `pwa`/`desktop_chat` → gateway `broadcast_event("scheduled_notification", {...})`; `speak` → `voice.speak` if UI process and `voice.auto_speak_responses` |
| `agent_turn` | If `defer_while_foreground_busy` and foreground busy → requeue `fire_at = now + defer_seconds` (max N retries). Else `agent_loop.run_task(prompt, max_steps=..., speak_summary=...)` or gateway `dispatch_task` equivalent with `speak_on_host=false` |

After success: `mark_fired`. On exception: `mark_failed` with truncated error.

### 3.4. Agent tools (`eidos/core/tool_registry.py` + dispatch in `loop.py`)

| Tool | Parameters (summary) |
|---|---|
| `schedule_timer` | `delay_seconds` **xor** `fire_at_iso`, `kind`, `channels`, `title`/`body` **or** `agent_prompt`, optional `conversation_id` |
| `list_scheduled_timers` | `status` filter, `limit` |
| `cancel_scheduled_timer` | `timer_id` |

Validation in store layer:

- Enforce `min_delay_seconds`, `max_horizon_days`, `max_pending_timers`.
- Reject past `fire_at` beyond small clock skew window (e.g. 5s).

### 3.5. System service (`eidos/scheduler_main.py` or `python -m eidos.scheduler`)

- Headless entry: load config, **no PyQt**.
- Start `KillSwitch` (optional hotkey may be noop without display).
- Start Telegram and/or gateway per `scheduling.system_service.*`.
- Construct minimal `AgentLoop` **only if** `agent_turn_on_fire.enabled`.
- Run `ReminderScheduler` as primary loop; SIGTERM → graceful `stop()`.

**Lease rule:** Process sets `owner = "systemd:<pid>"`. GUI sets `owner = "gui:<pid>"`. If GUI holds active lease, systemd skips claims until `lease_until` elapsed.

### 3.6. systemd integration (`deploy/systemd/`)

- `eidos-scheduler.service` — `Type=simple`, `Restart=on-failure`, `Environment=DISPLAY=` unset.
- `eidos-scheduler.timer` — **not** used for per-timer wake; optional **heartbeat** only if we later add watchdog.
- Install docs in plan: `systemctl --user enable --now eidos-scheduler.service`.

---

## 4. Gateway & UI Contracts

### 4.1. WebSocket event `scheduled_notification`

```json
{
  "timer_id": "…",
  "kind": "notify",
  "title": "…",
  "body": "…",
  "conversation_id": "…",
  "fired_at": 1734567890.0
}
```

PWA appends an assistant-style system notice or injects into active conversation if `conversation_id` matches.

### 4.2. REST (optional v1)

- `GET /api/scheduling/timers?status=pending`
- `POST /api/scheduling/timers/{id}/cancel`

Auth: same bearer token as gateway.

### 4.3. Desktop pill

- Subscribe to `scheduled_notification`; call `chat_history.add_message("assistant", body, ...)`.
- Settings panel: pending timers table with Cancel button.

### 4.4. Telegram

- `broadcast_message` for `notify`.
- For `agent_turn` completion, reuse existing `on_task_finish` notification path when enabled.
- Commands: `/timers`, `/cancel_timer <short_id>`.

---

## 5. Concurrency & Idempotence

- **Exactly-once user perception:** duplicate claim prevented by SQL `UPDATE ... WHERE status='pending' AND (lease_until IS NULL OR lease_until < ?)`.
- **Clock skew:** scheduler uses monotonic time only for intervals; due comparison uses UTC wall clock from `time.time()`.
- **Startup catch-up:** on scheduler start, fire all `pending` with `fire_at < now` subject to `missed_policy` and `max_catchup_per_boot`.

---

## 6. Security

- Only **authorized Telegram users** can list/cancel via bot commands.
- Gateway API requires auth token.
- Agent cannot cancel another conversation’s timers unless `scheduling.cross_conversation_cancel: false` (default).
- **No secrets** in `payload_json`; audit log entry on create/cancel/fire.

---

## 7. Compute & Resource Policy

Fired **`agent_turn`** timers:

- Must use `priority="background"` on model calls.
- Must not call `run_task` if kill switch active; mark `failed` with reason.
- Honor `scheduling.agent_turn_on_fire.max_steps` cap even if user `--steps` default is higher.

Reference: [`SPEC_COMPUTE_RESOURCE_POLICY.md`](SPEC_COMPUTE_RESOURCE_POLICY.md).

---

## 8. Configuration Keys

All under `scheduling:` in `config.yaml` (see feature doc §7). Defaults documented in `config.example.yaml` when implemented.

---

## 9. Testing Requirements

| Test | Intent |
|---|---|
| `test_timer_store_crud` | Create/list/cancel; enforce limits |
| `test_due_selection` | Correct ordering and status filters |
| `test_claim_idempotent` | Two threads; one wins |
| `test_lease_handoff` | GUI lease blocks systemd claim until TTL |
| `test_executor_notify_channels` | Mock telegram/gateway; channel matrix |
| `test_missed_policy` | Overdue timers on startup |

No full PyQt or back-to-back `run_gui_mode` in CI.

---

## 10. Related Documents

- [`docs/SCHEDULED_WAKE_AND_TIMERS.md`](../SCHEDULED_WAKE_AND_TIMERS.md)
- [`docs/plans/PLAN_SCHEDULED_WAKE.md`](../plans/PLAN_SCHEDULED_WAKE.md)
- [`docs/TELEGRAM_GATEWAY.md`](../TELEGRAM_GATEWAY.md)
