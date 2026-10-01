# EidosBot Telegram Gateway & Remote Interaction Specification

This document defines the architecture, message routing, remote approval mechanics, and security controls for the **EidosBot Telegram Gateway**.

---

## 1. System Vision & Purpose

The Telegram Gateway turns EidosBot into a **continuous companion** when you are away from your PC:
1. **Remote Chat & Delegation:** Launch tasks, check computer status, or kick off batch jobs from your phone.
2. **Interactive Remote Approvals:** When a command or destructive action requires authorization, EidosBot delivers an interactive Telegram card with inline approval buttons (`[Approve]`, `[Reject]`).
3. **Human-in-the-Loop Batch Support:** When running unattended batch jobs, EidosBot pushes milestone summaries and asks clarifying questions on ambiguous items.
4. **Remote Emergency Stop:** Send `/kill` or `/stop` to instantly invoke the kernel kill switch and stop active tasks.

---

## 2. Gateway Architecture & Control Flow

```
┌────────────────────────────────────────────────────────────────────────┐
│                        EIDOS TELEGRAM ARCHITECTURE                     │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│   📱 TELEGRAM APP (Phone / Remote Device)                              │
│        │  • User sends prompt or taps inline [Approve] buttons         │
│        │  • Encrypted TLS over Telegram Bot API                        │
│        ▼                                                               │
│   🌉 EIDOS TELEGRAM GATEWAY (`eidos/gateway/telegram_bot.py`)          │
│        │  • Authenticates user against allowed_user_ids                │
│        │  • Forwards user message to Eidos Agent Loop                  │
│        │  • Resolves approval futures for Safety Gatekeeper            │
│        ▼                                                               │
│   ⚙️ EIDOS CORE AGENT LOOP (`eidos/core/loop.py`)                      │
│        │                                                               │
│        ├──▶ 🛡️ SAFETY GATEKEEPER                                       │
│        │     • Requests user decision when host confirmation is needed │
│        │                                                               │
│        └──▶ 🛠️ ACTION & TOOL RUNNERS                                   │
│              • Sandboxed shell, Playwright browser, or batch runner    │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

The Telegram Gateway functions as an unprivileged remote control surface for the agent loop:
- Messages from authorized users are queued directly into the agent's active task context.
- When the agent finishes a turn or encounters a milestone, output summaries are sent back to the chat.
- When an action triggers confirmation, the gateway suspends execution and displays an interactive approval card.

---

## 3. Remote Approval & Human-in-the-Loop Flows

### 3.1. Interactive Command Confirmation
When EidosBot encounters a command that cannot run in the sandbox and requires user approval (Level 2 Host or Level 3 Destructive):

1. The `Gatekeeper` detects that the user is away or remote mode is active.
2. The Telegram Gateway posts an interactive message card with inline keyboard buttons:

```text
⚠️ EidosBot Command Approval Required
━━━━━━━━━━━━━━━━━━━━━━
Command:   npm run deploy:staging
Directory: ~/Projects/EidosBot
Risk:      Level 2 (Host Shell Execution)
Reason:    Deploying updated code to server

[ ✅ Approve Once ]      [ ❌ Reject ]
[ 💾 Always Allow ]      [ 🛑 Emergency Stop ]
```

3. When you tap **[ ✅ Approve Once ]** on your phone:
   - Telegram fires a callback query with the decision payload.
   - The Gateway unblocks the Gatekeeper's waiting thread (`threading.Event`).
   - The command executes, and output is routed back to your Telegram chat.

### 3.2. Batch Job Clarifications & Question Handling
While processing a batch job queue, EidosBot might encounter an item requiring user direction:

```text
❓ Item #48 Clarification Needed
━━━━━━━━━━━━━━━━━━━━━━
Concept: "Ultrasonic Eyeglass Cleaner Clip"
Issue:   Found existing patents for benchtop ultrasonic baths, but none for a clip-on battery form factor.

How should I position the primary market research?
[ 👓 Consumer EDC ]      [ 🔬 Optometry Clinics ]
```

Tapping a button or typing a quick voice/text reply immediately unpauses the job runner with your guidance.

---

## 4. Key Telegram Commands

| Command | Action |
|---|---|
| `/status` | Returns system health, current desktop active window, GPU/RAM usage, and internal clock. |
| `/jobs` | Lists active batch jobs, item completion counts, percentage, and current item being processed. |
| `/pause` | Safely pauses the currently active batch job or task after the current step. |
| `/resume` | Resumes a paused batch job from its last checkpoint in `queue.json`. |
| `/kill` | **Global Remote Kill Switch:** Instantly terminates active child processes, stops the loop, and halts all tasks. |
| `/clear` | Clears active conversation memory context for a fresh session. |

---

## 5. Security & Authentication Safeguards

Because Telegram allows public bots, EidosBot employs strict security boundaries:

1. **User ID Whitelisting (`allowed_user_ids`):**
   - The bot checks `message.from_user.id` against a hardcoded list in `config.yaml`.
   - Any message from an unauthorized Telegram account is silently dropped and logged to `~/.eidos/audit.log`.
2. **Local Token Storage:**
   - The bot token is stored locally in `~/.eidos/telegram.token` or via environment variable `EIDOS_TELEGRAM_BOT_TOKEN`, with permissions set to `chmod 600`.
3. **No Root Privilege Over Telegram:**
   - Even with Telegram approval, commands running as `sudo` or modifying critical system files (`Level 3`) can be configured to require physical presence at the desktop keyboard if preferred.

---

## 6. Configuration Specification (`config.yaml`)

```yaml
gateway:
  telegram:
    enabled: true
    bot_token_env: "EIDOS_TELEGRAM_BOT_TOKEN"   # Or stored in ~/.eidos/telegram.token
    allowed_user_ids:
      - 123456789                              # Your specific numeric Telegram User ID
    notifications:
      on_job_milestone_interval: 10            # Notify every 10 completed items
      on_approval_required: true               # Send interactive inline button for approvals
      on_task_finish: true                     # Push final summary when task completes
    remote_kill_switch_enabled: true           # Allow /kill command to abort operations
```

---

## 7. Python Implementation Design (`eidos/gateway/telegram_bot.py`)

Using the lightweight `python-telegram-bot` (or `aiogram`) library with long-polling (no public domain or open ports required):

```python
import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

logger = logging.getLogger(__name__)

class TelegramGateway:
    def __init__(self, token: str, allowed_users: list[int], agent_loop):
        self.token = token
        self.allowed_users = set(allowed_users)
        self.agent_loop = agent_loop
        self.app = None

    def _is_authorized(self, update: Update) -> bool:
        user_id = update.effective_user.id if update.effective_user else None
        return user_id in self.allowed_users

    async def start(self):
        self.app = ApplicationBuilder().token(self.token).build()
        self.app.add_handler(CommandHandler("status", self._handle_status))
        self.app.add_handler(CommandHandler("kill", self._handle_kill))
        self.app.add_handler(CallbackQueryHandler(self._handle_approval_callback))
        self.app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), self._handle_message))
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()

    async def send_approval_request(self, command: str, reason: str, callback_future):
        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data="approve"),
                InlineKeyboardButton("❌ Reject", callback_data="reject")
            ],
            [InlineKeyboardButton("🛑 Kill Switch", callback_data="kill")]
        ]
        # Stores future to resolve when user clicks button
        ...
```
