# Scheduled Wake, Autonomous Timers & Proactive Notifications

**Specification:** [`docs/specs/SPEC_SCHEDULED_WAKE.md`](specs/SPEC_SCHEDULED_WAKE.md)  
**Implementation plan:** [`docs/plans/PLAN_SCHEDULED_WAKE.md`](plans/PLAN_SCHEDULED_WAKE.md)  
**Status:** Draft (specified, not yet implemented)  
**Related:** [`TELEGRAM_GATEWAY.md`](TELEGRAM_GATEWAY.md), [`MOBILE_PWA_GATEWAY.md`](MOBILE_PWA_GATEWAY.md), [`TAILSCALE_AND_EIDOS.md`](TAILSCALE_AND_EIDOS.md), [`docs/specs/SPEC_COMPUTE_RESOURCE_POLICY.md`](specs/SPEC_COMPUTE_RESOURCE_POLICY.md)

---

## 1. Vision

EidosBot should be able to **set its own reminders and timers** during normal work—then **act on them later** without waiting for the user to open chat again.

Two cooperating layers deliver that:

| Layer | When Eidos GUI/CLI is running | When Eidos is **closed** |
|---|---|---|
| **In-process scheduler** | Daemon thread polls persisted timers and delivers notifications or kicks off agent turns | Store on disk; nothing fires until something is running |
| **System wake service** | Same store; optional handoff to a lightweight always-on user service | User `systemd` unit (or headless daemon) loads the store and fires due items—optionally **starting** a full Eidos session for agent-turn timers |

The user (and the model) get one mental model: **“schedule something”** → it is **durable** → it **fires on time** through the channels you configured (Telegram, desktop pill, PWA, TTS when appropriate).

---

## 2. Problem Statement

Today, Eidos can push messages **reactively** in a few hard-coded cases (batch milestones, risk approvals, task-finished summaries via Telegram). There is no way for the agent to:

- Set an arbitrary “ping me in 20 minutes about X” or “check this again at 9am.”
- Persist that intent across a GUI restart.
- Wake the machine’s **Eidos stack** when the floating pill is closed.

That limits unattended companionship: monitoring long jobs, follow-ups, periodic checks, and “I’ll remind you when…” promises in chat.

---

## 3. User & Agent Experience

### 3.1. Agent-created timers (tools)

While a task or chat turn is active, the model can call tools such as:

- **`schedule_timer`** — create a durable timer (relative delay or absolute `fire_at`).
- **`list_scheduled_timers`** — list pending / recently fired timers (scoped to conversation or global per policy).
- **`cancel_scheduled_timer`** — cancel by id before fire.

Each timer carries:

- **Payload kind**
  - **`notify`** — deliver a fixed message (no LLM turn).
  - **`agent_turn`** — run a bounded autonomous turn with an internal prompt (e.g. “Re-check disk usage and summarize for the user”).
- **Delivery channels** (subset): `telegram`, `desktop_chat`, `pwa`, `speak` (speak only when host UI is active and voice is enabled).
- **Optional** `conversation_id` so replies land in the right thread.

Example user request:

> “Remind me in 45 minutes if the render job hasn’t finished, and message me on Telegram.”

The agent schedules one or two timers (`notify` and/or `agent_turn` that inspects the job) with `channels: ["telegram"]`.

### 3.2. User visibility

- **Settings → Scheduled timers** (planned): view, cancel, and toggle whether agent-created timers require confirmation.
- **Telegram:** `/timers` lists pending timers; `/cancel_timer <id>` cancels (authorized users only).
- **PWA:** read-only list via gateway API (planned).

### 3.3. What “wake” means

| Action | `notify` timer | `agent_turn` timer |
|---|---|---|
| Eidos **running** | Push message + optional chat bubble | Background `run_task` / chat dispatch with `priority=background` |
| Eidos **closed**, scheduler service **running** | Telegram (and PWA if gateway up) | Start **headless worker** or **oneshot** `eidos` run (configurable cap on steps) |
| PC **suspended/off** | Best-effort; see §5.3 | Same; may require RTC wake (optional, advanced) |

---

## 4. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         PERSISTENT TIMER STORE                          │
│              ~/.eidos/eidos.db  table: scheduled_timers                 │
│   (shared by GUI, CLI, gateway, and system scheduler service)           │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
┌───────────────┐     ┌─────────────────┐     ┌─────────────────────────┐
│ AgentLoop     │     │ ReminderScheduler│     │ eidos-scheduler.service │
│ schedule_*    │     │ (in-process      │     │ (systemd user unit,     │
│ tools         │     │  daemon thread)  │     │  headless, no PyQt)     │
└───────────────┘     └────────┬─────────┘     └────────────┬────────────┘
                               │                            │
                               └────────────┬───────────────┘
                                            ▼
                               ┌────────────────────────────┐
                               │ ReminderExecutor           │
                               │ • notify → broadcast       │
                               │ • agent_turn → dispatch    │
                               └────────────┬───────────────┘
                                            │
                    ┌───────────────────────┼───────────────────────┐
                    ▼                       ▼                       ▼
           TelegramGateway          GatewayServer WS          Desktop pill / TTS
           broadcast_message        scheduled_notification    chat inject + speak
```

**Single source of truth:** SQLite rows written at schedule time. Whichever process is alive and owns the scheduler lease fires due items exactly once (idempotent `fired_at` + status).

---

## 5. System-Level Wake (Linux Mint / systemd)

### 5.1. Recommended default: user scheduler service

Install a **user** systemd unit `eidos-scheduler.service` that:

- Runs `python -m eidos.scheduler` (or `eidos --scheduler-only`) with **no PyQt**.
- Enables optional **gateway** and **Telegram** so `notify` timers reach the phone even when the HUD is closed.
- Uses **`loginctl enable-linger`** when the user wants timers while logged out (machine on).

On fire:

- **`notify`:** send Telegram / gateway events directly (no full agent).
- **`agent_turn`:** either invoke a bounded headless task via existing `AgentLoop` in the scheduler process, or **`systemd-run`** / `StartTransientUnit` a oneshot `eidos --cli "<prompt>" --steps N` with resource limits.

### 5.2. GUI coexistence

When the **full GUI** is running, the in-process `ReminderScheduler` holds a short-lived **lease** in the DB (or a file lock) so the systemd service **does not double-fire**. On GUI exit, the lease expires within `lease_ttl_seconds` and the system service resumes.

### 5.3. Suspend, hibernate, and powered-off (optional phases)

| Capability | Mechanism | Notes |
|---|---|---|
| Machine on, user logged out | `linger` + user service | Supported in v1 plan |
| Machine on, GUI closed | User scheduler service | Primary v1 goal |
| **Sleep/suspend** | `systemd-sleep` hook or inhibitor that resumes then runs catch-up | Phase 3; fires **missed** timers on wake with `missed_policy` |
| **RTC alarm wake** | `rtcwake` or BIOS alarm for critical timers | Phase 4; explicit user opt-in; gatekeeper Level 2+ |

Missed timers while offline use **`missed_policy`:** `drop`, `fire_once`, or `fire_immediately_on_startup` (configurable).

---

## 6. Safety, Policy & Compute

- **Gatekeeper:** Creating timers is **Level 0** (metadata only). **`agent_turn`** payloads that might run shell/browser tools remain subject to normal execution policy when they fire.
- **Confirmation (optional):** `scheduling.require_confirmation_for_agent_turn: true` stages agent-turn timers until the user approves in HUD or Telegram.
- **Limits:** `max_pending_timers`, `min_delay_seconds`, `max_horizon_days`, `max_agent_steps_on_fire` prevent runaway self-scheduling.
- **Compute:** Fired **`agent_turn`** timers use `ModelRouter.chat_background()` and [`SPEC_COMPUTE_RESOURCE_POLICY.md`](specs/SPEC_COMPUTE_RESOURCE_POLICY.md)—never preempt an active user turn.
- **Interrupt:** If the user starts a foreground task, a firing background agent turn **queues** or **defers** until idle (same pattern as episodic reflector).

---

## 7. Configuration (preview)

```yaml
scheduling:
  enabled: true
  check_interval_seconds: 15.0      # In-process and scheduler service poll cadence
  lease_ttl_seconds: 45.0           # GUI scheduler lease; systemd takes over after expiry
  max_pending_timers: 64
  min_delay_seconds: 60.0           # Agent cannot schedule < 1 minute without override
  max_horizon_days: 30
  default_channels: ["telegram", "desktop_chat"]
  missed_policy: fire_once          # drop | fire_once | fire_immediately_on_startup
  agent_turn_on_fire:
    enabled: true
    max_steps: 15
    require_confirmation: false
    defer_while_foreground_busy: true
  system_service:
    enabled: false                  # User enables after installing unit file
    start_full_gui_on_agent_turn: false
    gateway: true                   # Mini gateway for PWA notify when GUI down
    telegram: true
```

---

## 8. Success Criteria

1. Agent can schedule a **notify** timer in chat; user receives Telegram message at `fire_at` with GUI **closed**, scheduler service **running**.
2. Agent can schedule an **agent_turn** timer; at fire time a background turn runs and user gets a summary on configured channels.
3. Timers survive **GUI restart**; no duplicate delivery when GUI and systemd both run (lease semantics).
4. User can **list and cancel** pending timers from settings or Telegram.
5. Unit tests cover store CRUD, due selection, lease handoff, and idempotent fire—**without** back-to-back full app launches.

---

## 9. Out of Scope (initial release)

- Recurring cron-style timers (daily/weekly)—design hooks only.
- Cloud-hosted scheduler when the home PC is off (future: delegate to Telegram bot-only worker).
- Cross-device sync beyond this machine’s SQLite file.

See the implementation plan for phased delivery and file-level tasks.

---

## 10. Tailscale and remote delivery

**Telegram** and **desktop** channels do not depend on Tailscale. **PWA** and WebSocket delivery to a phone on cellular do: the host must be on your tailnet and reachable at `http://<100.x>:8420` (or MagicDNS).

Eidos does **not** route traffic through a special proxy—it can help **on the PC** with Tailscale join, health checks, and auth-key-assisted `tailscale up` after reboot. Phones still sign in via the Tailscale app (or machine share). MCP is optional; the default plan is **local CLI wrapper tools**, not a Tailscale Admin API server.

Full discussion: [`TAILSCALE_AND_EIDOS.md`](TAILSCALE_AND_EIDOS.md).
