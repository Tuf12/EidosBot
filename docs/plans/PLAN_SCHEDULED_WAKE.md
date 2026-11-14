# Implementation Plan: Scheduled Wake, Autonomous Timers & Proactive Notifications

**Specification:** [`docs/specs/SPEC_SCHEDULED_WAKE.md`](../specs/SPEC_SCHEDULED_WAKE.md)  
**Feature overview:** [`docs/SCHEDULED_WAKE_AND_TIMERS.md`](../SCHEDULED_WAKE_AND_TIMERS.md)  
**Target modules:** `eidos/scheduling/*`, `eidos/core/loop.py`, `eidos/core/tool_registry.py`, `eidos/gateway/server.py`, `eidos/gateway/telegram_bot.py`, `eidos/ui/settings_dialog.py`, `eidos/main.py`, `deploy/systemd/`  
**Status:** Ready for Implementation  
**Estimated complexity:** Medium (SQLite + daemon threading + dual-process lease semantics + systemd packaging)

---

## 1. Objectives & Guardrails

### Primary goal

Allow EidosBot to **create, persist, and execute** its own timers while the app is running, and allow a **lightweight system-level scheduler** to fire those same timers when the PyQt HUD is closed.

### Non-negotiable guardrails

1. **No duplicate delivery:** lease-based claims; one winner per `timer_id`.
2. **Foreground first:** never block user chat; defer `agent_turn` fires while `agent_loop` is foreground-busy (same semantics as consolidation idle checks).
3. **Compute policy:** fired agent turns are background-only per [`SPEC_COMPUTE_RESOURCE_POLICY.md`](../specs/SPEC_COMPUTE_RESOURCE_POLICY.md).
4. **Bounded autonomy:** caps on pending count, horizon, delay, and `max_steps` on fire.
5. **Test without full app:** store/scheduler/executor unit tests only; no back-to-back GUI launches in CI.
6. **Linux Mint first:** user `systemd` units; document `loginctl enable-linger` for logged-out delivery.

---

## 2. File inventory

| File | Change | Responsibility |
|---|---|---|
| `eidos/scheduling/__init__.py` | **New** | Public exports |
| `eidos/scheduling/types.py` | **New** | `ScheduledTimer`, `TimerKind`, `TimerChannel`, payloads |
| `eidos/scheduling/store.py` | **New** | SQLite schema, CRUD, claim/complete |
| `eidos/scheduling/scheduler.py` | **New** | Polling daemon thread |
| `eidos/scheduling/executor.py` | **New** | Channel delivery + agent dispatch |
| `eidos/scheduler_main.py` | **New** | Headless entry for systemd |
| `eidos/core/tool_registry.py` | Mod | Tool schemas `schedule_timer`, `list_scheduled_timers`, `cancel_scheduled_timer` |
| `eidos/core/loop.py` | Mod | Tool dispatch; start/stop scheduler; inject store |
| `eidos/gateway/server.py` | Mod | WS `scheduled_notification`; REST list/cancel |
| `eidos/gateway/telegram_bot.py` | Mod | `/timers`, `/cancel_timer`; optional notify on fire |
| `eidos/ui/settings_dialog.py` | Mod | Scheduling toggles + pending timer table (Phase 2 UI) |
| `eidos/ui/app.py` | Mod | WS handler → pill chat inject |
| `eidos/main.py` | Mod | `--scheduler-only` flag; wire GUI scheduler |
| `config.example.yaml` | Mod | `scheduling:` block |
| `deploy/systemd/eidos-scheduler.service` | **New** | User unit template |
| `deploy/systemd/README.md` | **New** | Install, linger, troubleshooting |
| `tests/test_scheduling.py` | **New** | Store, claim, executor mocks |
| `docs/SCHEDULED_WAKE_AND_TIMERS.md` | Done | Feature doc |
| `docs/specs/SPEC_SCHEDULED_WAKE.md` | Done | Engineering spec |

---

## 3. Phase 1 — Persistence & in-process scheduler (MVP)

**Goal:** Timers created by tools fire **`notify`** payloads while GUI or CLI+gateway is running.

### 3.1. Types & store

1. Add `ScheduledTimer` pydantic/dataclass models in `types.py`.
2. In `ScheduledTimerStore.__init__`, accept `db_path` from `AgentLoop` config (`conversations_db_path`).
3. Run migration in store constructor:

```sql
CREATE TABLE IF NOT EXISTS scheduled_timers (
  timer_id TEXT PRIMARY KEY,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  fire_at REAL NOT NULL,
  timezone TEXT,
  kind TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  channels_json TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  conversation_id TEXT,
  created_by TEXT NOT NULL,
  fired_at REAL,
  last_error TEXT,
  lease_owner TEXT,
  lease_until REAL
);
CREATE INDEX IF NOT EXISTS idx_scheduled_timers_due ON scheduled_timers(status, fire_at);
```

4. Implement validation: `min_delay_seconds`, `max_horizon_days`, `max_pending_timers`.

### 3.2. Scheduler thread

1. `ReminderScheduler(store, executor, interval, owner_id=get_owner_id())`.
2. Wire in `AgentLoop.__init__` after gateway/telegram references exist; `start()` at end of startup, `stop()` on shutdown.
3. Use `owner_id = f"gui:{os.getpid()}"` in GUI mode, `f"cli:{os.getpid()}"` in CLI.

### 3.3. Executor (notify only)

1. Inject weak refs to `telegram_gw`, `gateway`.
2. Implement channel matrix for `kind=notify` only.
3. Log audit line to `~/.eidos/audit.log` on fire.

### 3.4. Tools

1. Register tools in `tool_registry.py`.
2. In `loop._dispatch_tool`, route to `store.create_timer` / `list` / `cancel`.
3. Default `conversation_id` to `active_conversation_id` when omitted.

### 3.5. Config

Add to `config.example.yaml`:

```yaml
scheduling:
  enabled: true
  check_interval_seconds: 15.0
  lease_ttl_seconds: 45.0
  max_pending_timers: 64
  min_delay_seconds: 60.0
  max_horizon_days: 30
  default_channels: ["telegram", "desktop_chat"]
  missed_policy: fire_once
  agent_turn_on_fire:
    enabled: true
    max_steps: 15
    require_confirmation: false
    defer_while_foreground_busy: true
    defer_seconds: 120.0
    max_defer_attempts: 10
  system_service:
    enabled: false
    gateway: true
    telegram: true
    start_full_gui_on_agent_turn: false
```

Load `scheduling` dict in `AgentLoop` / scheduler entrypoint (mirror `memory.consolidation` pattern).

### 3.6. Tests

- `tests/test_scheduling.py`: in-memory or tempfile SQLite; CRUD + due query + single-thread claim.

**Phase 1 exit criteria:** From chat, agent schedules 90s `notify`; Telegram and desktop receive message without user input.

---

## 4. Phase 2 — Agent-turn timers & gateway surface

**Goal:** Fired timers can run a **bounded** `run_task` in the background and surface results.

### 4.1. Executor `agent_turn`

1. Check kill switch; if tripped → `mark_failed`.
2. If `defer_while_foreground_busy` and busy → bump `fire_at`, increment defer counter in `payload_json` metadata or separate column `defer_count` (migration).
3. Call `run_task(prompt, max_steps=capped, speak_summary=False)` on worker thread **or** delegate to `gateway.dispatch_task` when only gateway is up.
4. Reuse `on_task_finish` for Telegram when `on_task_completed` notification enabled.

### 4.2. Gateway

1. `broadcast_event("scheduled_notification", {...})` for notify path (already in Phase 1).
2. Add `GET /api/scheduling/timers`, `POST /api/scheduling/timers/{id}/cancel`.
3. PWA: handle event in `useEidosWS.ts` (toast + optional conversation inject).

### 4.3. Desktop UI

1. Settings → Scheduling: enable/disable, show pending table, cancel button.
2. Pill: wire WS to `add_message` for notify.

### 4.4. Optional confirmation flow

If `require_confirmation: true`, create timers in `status=awaiting_approval`; HUD/Telegram card approves → `pending`.

**Phase 2 exit criteria:** Agent schedules disk-check in 2 minutes; at fire, background turn runs and user gets summary on Telegram.

---

## 5. Phase 3 — System scheduler service (wake when GUI closed)

**Goal:** Same SQLite file; **headless** process fires timers when HUD is not running.

### 5.1. Headless entrypoint

1. Add `python -m eidos.scheduler` or `eidos --scheduler-only --config config.yaml`.
2. No `QApplication`; optional `--gateway` / `--telegram` from `scheduling.system_service`.
3. Construct `AgentLoop` with minimal UI stubs (existing patterns in CLI mode).

### 5.2. Lease handoff

1. On GUI start: scheduler uses `lease_ttl_seconds` when claiming.
2. On GUI stop: stop in-process scheduler thread; systemd instance claims after TTL.
3. Unit test: simulate two owners with sequential claims.

### 5.3. systemd packaging

`deploy/systemd/eidos-scheduler.service`:

```ini
[Unit]
Description=EidosBot timer scheduler (headless)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/CursorProjects/EidosBot
ExecStart=%h/CursorProjects/EidosBot/.venv/bin/python -m eidos.scheduler --config %h/.eidos/config.yaml
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
```

`deploy/systemd/README.md`:

- `systemctl --user daemon-reload`
- `systemctl --user enable --now eidos-scheduler.service`
- `loginctl enable-linger $USER` for timers when logged out
- Set `scheduling.system_service.enabled: true`

### 5.4. Install hook (optional)

Settings dialog button: “Install user scheduler” copies unit to `~/.config/systemd/user/` and runs `daemon-reload` (shell via gated Level 2 confirm).

**Phase 3 exit criteria:** Close GUI; at `fire_at`, Telegram receives notify from systemd service only.

---

## 6. Phase 4 — Catch-up, Telegram UX, hardening

### 6.1. Missed timer policy

On scheduler boot:

- `drop`: mark overdue `cancelled` with reason `missed`.
- `fire_once`: fire at most `max_catchup_per_boot` (default 5).
- `fire_immediately_on_startup`: fire all due pending.

### 6.2. Telegram commands

- `/timers` — paginated pending list with short ids.
- `/cancel_timer <id_prefix>` — cancel if unique prefix match.

### 6.3. Observability

- Log fire metrics: `logger.info("scheduled_timer_fired", extra={...})`.
- Gateway log stream entry.

### 6.4. Tailscale health (PWA channel)

When a fired timer includes channel `pwa` and `scheduling.warn_if_tailscale_down: true` (default):

- If `tailscale status --json` reports not `Running`, log warning; still fire Telegram if configured; record hint in audit log.
- Later: scheduler startup may call host CLI wrappers from [`TAILSCALE_AND_EIDOS.md`](../TAILSCALE_AND_EIDOS.md).

### 6.5. Recurrence (optional stretch)

- Add `recurrence_rule` column (iCalendar RRULE string); **not** required for v1 sign-off.

---

## 7. Phase 5 — Advanced system wake (optional / future)

Document only unless explicitly prioritized:

| Item | Approach |
|---|---|
| Resume from suspend | `sleep.target` / custom `systemd-sleep` script invoking `eidos.scheduler --catch-up` |
| RTC wake | Gatekeeper-approved `schedule_timer` with `wake_host: true` → writes transient `rtcwake` helper (Level 2) |
| Start full GUI on agent turn | `systemd-run` desktop file `eidosbot.desktop` with prompt env |

---

## 8. Integration checklist

- [ ] `AgentLoop` shuts down scheduler in GUI close path (`ui/app.py` cleanup).
- [ ] CLI with `--telegram` runs scheduler for one-shot sessions.
- [ ] Batch runner does not disable scheduling.
- [ ] Kill switch stops **new** agent_turn fires; in-flight turn respects existing abort.
- [ ] `docs/FEATURES.md` §12 linked.
- [ ] `docs/ROADMAP.md` Phase 15 checklist.

---

## 9. Verification plan (manual)

1. **In-app notify:** Schedule 2-minute reminder; confirm pill + Telegram.
2. **Lease:** Run GUI + systemd; confirm single delivery.
3. **GUI closed:** Stop GUI only; systemd fires Telegram notify.
4. **Agent turn:** Schedule check with `max_steps: 5`; verify no foreground stall during user chat (defer).
5. **Cancel:** `/cancel_timer` and settings cancel before fire.

---

## 10. Risk register

| Risk | Mitigation |
|---|---|
| Double fire GUI + systemd | Lease TTL + atomic claim SQL |
| Runaway agent self-scheduling | `max_pending_timers`, rate limit per conversation per hour |
| LLM cost on fire | Default `notify`; agent_turn opt-in; low `max_steps` |
| systemd path drift | README uses `%h` placeholders; user edits WorkingDirectory |
| PC off | Document limitation; Phase 5 RTC optional |
