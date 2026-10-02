"""Telegram Gateway companion & remote human-in-the-loop approvals for EidosBot."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import uuid

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import (
        Application,
        ApplicationBuilder,
        CallbackQueryHandler,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    Application = None
    ApplicationBuilder = None
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None
    Update = None
    ContextTypes = None
    CommandHandler = None
    CallbackQueryHandler = None
    MessageHandler = None
    filters = None

from eidos.safety.risk_classifier import RiskLevel

logger = logging.getLogger(__name__)

# Suppress verbose third-party HTTP transport & polling logs from telegram / httpx
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)


def resolve_bot_token(
    token: Optional[str] = None,
    token_env_var: str = "EIDOS_TELEGRAM_BOT_TOKEN",
    token_file: str = "~/.eidos/telegram.token",
    explicit_token: Optional[str] = None,
    env_var: Optional[str] = None,
    file_path: Optional[str] = None,
) -> Optional[str]:
    """Discover bot token from explicit parameter, environment variable, or local token file."""
    # Resolve aliases
    actual_token = token or explicit_token
    actual_env = env_var or token_env_var
    actual_file = file_path or token_file

    if actual_token and str(actual_token).strip():
        return str(actual_token).strip()

    if actual_env:
        env_val = os.environ.get(actual_env, "").strip()
        if env_val:
            return env_val

    if actual_file:
        p = Path(os.path.abspath(os.path.expanduser(actual_file)))
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        return content
            except Exception as e:
                logger.debug("Could not read token file %s: %s", p, e)

    return None


class TelegramGateway:
    """Bi-directional Telegram bot for remote monitoring, delegation, and approvals."""

    def __init__(
        self,
        agent_loop: Any,
        token: Optional[str] = None,
        allowed_user_ids: Optional[List[Union[int, str]]] = None,
        token_env_var: str = "EIDOS_TELEGRAM_BOT_TOKEN",
        token_file: str = "~/.eidos/telegram.token",
        notifications_config: Optional[Dict[str, Any]] = None,
        remote_kill_switch_enabled: bool = True,
    ):
        self.agent_loop = agent_loop
        self.token = resolve_bot_token(token=token, token_env_var=token_env_var, token_file=token_file)

        # Normalize allowed user IDs as integers
        self.allowed_user_ids: Set[int] = set()
        if allowed_user_ids:
            for uid in allowed_user_ids:
                try:
                    self.allowed_user_ids.add(int(uid))
                except (ValueError, TypeError):
                    logger.warning("Ignoring invalid allowed Telegram user ID: %s", uid)

        self.notifications = notifications_config or {}
        self.remote_kill_switch_enabled = remote_kill_switch_enabled

        self.app: Optional[Application] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running: bool = False

        # Active chat IDs recorded from authorized users
        self._known_chat_ids: Set[int] = set()

        # Confirmation records: cid -> dict
        self._pending_confirmations: Dict[str, Dict[str, Any]] = {}
        self._confirm_lock = threading.Lock()

    @property
    def _pending_approvals(self) -> Dict[str, Dict[str, Any]]:
        """Alias for pending confirmations dictionary."""
        return self._pending_confirmations

    def is_authorized(self, user_id: Optional[int]) -> bool:
        """Check if numeric user ID is in the allowed whitelist."""
        if user_id is None:
            return False
        # If no allowed_user_ids configured, deny all by default for safety
        if not self.allowed_user_ids:
            return False
        return user_id in self.allowed_user_ids

    def _is_authorized(self, user_or_update: Any) -> bool:
        """Helper to check authorization directly or from an Update object."""
        if hasattr(user_or_update, "effective_user"):
            user = user_or_update.effective_user
            return self.is_authorized(user.id if user else None)
        return self.is_authorized(user_or_update)

    def is_running(self) -> bool:
        """Return True if background bot thread and application are active."""
        return self._running and self._thread is not None and self._thread.is_alive()

    # =========================================================================
    # Lifecycle Management
    # =========================================================================

    def start(self) -> bool:
        """Start long-polling Telegram bot in a dedicated background thread."""
        if not TELEGRAM_AVAILABLE:
            logger.warning("python-telegram-bot is not installed. Telegram gateway disabled.")
            return False

        if not self.token:
            logger.warning("No Telegram bot token found. Telegram gateway disabled.")
            return False

        if self.is_running():
            logger.debug("Telegram gateway is already running.")
            return True

        ready_event = threading.Event()
        start_error: List[Exception] = []

        def _thread_target():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop

            try:
                self.app = ApplicationBuilder().token(self.token).build()

                # Register command handlers
                self.app.add_handler(CommandHandler("start", self._handle_start))
                self.app.add_handler(CommandHandler("help", self._handle_help))
                self.app.add_handler(CommandHandler("status", self._handle_status))
                self.app.add_handler(CommandHandler("jobs", self._handle_jobs))
                self.app.add_handler(CommandHandler("run", self._handle_run))
                self.app.add_handler(CommandHandler("summary", self._handle_summary))
                self.app.add_handler(CommandHandler("pause", self._handle_pause))
                self.app.add_handler(CommandHandler("resume", self._handle_resume))
                self.app.add_handler(CommandHandler("kill", self._handle_kill))
                self.app.add_handler(CommandHandler("clear", self._handle_clear))

                # Register interactive callback query and text message handlers
                self.app.add_handler(CallbackQueryHandler(self._handle_callback_query))
                self.app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), self._handle_message))

                async def _init_and_run():
                    await self.app.initialize()
                    await self.app.start()
                    if self.app.updater:
                        await self.app.updater.start_polling()
                    self._running = True
                    ready_event.set()

                loop.run_until_complete(_init_and_run())
                loop.run_forever()

            except Exception as e:
                logger.error("Failed to start Telegram gateway: %s", e)
                start_error.append(e)
                self._running = False
                ready_event.set()

        self._thread = threading.Thread(target=_thread_target, name="EidosTelegramWorker", daemon=True)
        self._thread.start()

        ready_event.wait(timeout=10.0)
        if start_error:
            return False

        # Hook milestone listeners if BatchJobRunner exists on agent_loop
        if hasattr(self.agent_loop, "jobs") and hasattr(self.agent_loop.jobs, "add_milestone_listener"):
            self.agent_loop.jobs.add_milestone_listener(self.on_job_milestone)

        # Hook task finish listener if enabled
        if self.notifications.get("on_task_finish", True) and hasattr(self.agent_loop, "on_task_finish"):
            self.agent_loop.on_task_finish(self.on_task_finished)

        logger.info("EidosBot Telegram Gateway active (allowed users: %s)", list(self.allowed_user_ids))
        return True

    def stop(self):
        """Stop background bot polling and shut down application."""
        if not self.is_running() or not self._loop or not self.app:
            self._running = False
            return

        async def _shutdown():
            try:
                if self.app.updater and self.app.updater.running:
                    await self.app.updater.stop()
                if self.app.running:
                    await self.app.stop()
                await self.app.shutdown()
            except Exception as e:
                logger.debug("Error during Telegram gateway shutdown: %s", e)
            finally:
                self._loop.stop()

        asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

        self._running = False
        logger.info("EidosBot Telegram Gateway stopped.")

    # =========================================================================
    # Authorization & Communication Helpers
    # =========================================================================

    async def _verify_update_auth(self, update: Update) -> bool:
        """Verify update originated from an authorized user."""
        user = update.effective_user
        user_id = user.id if user else None

        if update.effective_chat:
            self._known_chat_ids.add(update.effective_chat.id)

        if not self.is_authorized(user_id):
            logger.warning("Rejected unauthorized Telegram access attempt from user_id: %s", user_id)
            if update.message:
                try:
                    await update.message.reply_text(
                        f"⛔ Unauthorized user (ID: `{user_id}`).\n"
                        "To grant access, add this user ID to `allowed_user_ids` in `config.yaml`.",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass
            elif update.callback_query:
                try:
                    await update.callback_query.answer("⛔ Unauthorized user ID", show_alert=True)
                except Exception:
                    pass
            return False

        return True

    def broadcast_message(self, text: str, parse_mode: Optional[str] = "Markdown"):
        """Send message to all authorized users and known chats (thread-safe)."""
        if not self._loop or not self.app or not self._running:
            return

        # Target set includes allowed user IDs and known authorized chat IDs
        targets = set(self.allowed_user_ids) | set(self._known_chat_ids)
        if not targets:
            return

        async def _send():
            bot = self.app.bot
            for chat_id in targets:
                try:
                    await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
                except Exception:
                    # Fallback to plain text if Markdown parsing fails
                    try:
                        await bot.send_message(chat_id=chat_id, text=text, parse_mode=None)
                    except Exception as e:
                        logger.debug("Could not send Telegram message to chat %s: %s", chat_id, e)

        asyncio.run_coroutine_threadsafe(_send(), self._loop)

    # =========================================================================
    # Command Handlers
    # =========================================================================

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._verify_update_auth(update):
            return

        welcome = (
            "🤖 *EidosBot Telegram Gateway Online*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Your 24/7 desktop co-pilot companion.\n\n"
            "*Commands:*\n"
            "• `/status` - Desktop status, active window & clock\n"
            "• `/jobs` - Batch queue progress & active items\n"
            "• `/run` - Start/resume active batch job in background\n"
            "• `/summary` - Generate/view leaderboard rankings summary\n"
            "• `/pause` - Pause active batch job\n"
            "• `/resume` - Resume paused batch job\n"
            "• `/kill` - Emergency stop active tasks\n"
            "• `/clear` - Start a fresh conversation context\n\n"
            "Send any prompt text to delegate a task directly!"
        )
        await update.message.reply_text(welcome, parse_mode="Markdown")

    async def _handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._handle_start(update, context)

    async def _handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._verify_update_auth(update):
            return

        # Gather system and agent status
        clock_str = "Unknown"
        if hasattr(self.agent_loop, "clock") and self.agent_loop.clock:
            clock_str = self.agent_loop.clock.format_for_prompt()

        win_info = {}
        if hasattr(self.agent_loop, "windows") and self.agent_loop.windows:
            try:
                win_info = self.agent_loop.windows.get_active_window_info()
            except Exception:
                pass
        win_title = win_info.get("title", "None")
        win_class = win_info.get("class_name", "None")

        kill_active = False
        if hasattr(self.agent_loop, "kill_switch") and self.agent_loop.kill_switch:
            kill_active = self.agent_loop.kill_switch.is_triggered()

        # Batch job status if available
        job_summary = "None"
        if hasattr(self.agent_loop, "jobs") and self.agent_loop.jobs and self.agent_loop.jobs.active_job:
            q = self.agent_loop.jobs.active_job
            job_summary = f"{q.job_name} ({q.status}, {q.completed_items}/{q.total_items})"

        status_text = (
            "📊 *EidosBot System Status*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• *Time:* `{clock_str}`\n"
            f"• *Active Window:* `{win_title}`\n"
            f"• *Window Class:* `{win_class}`\n"
            f"• *Kill Switch:* `{'🛑 TRIPPED' if kill_active else '✅ Ready'}`\n"
            f"• *Active Job:* `{job_summary}`\n"
        )
        await update.message.reply_text(status_text, parse_mode="Markdown")

    async def _handle_jobs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._verify_update_auth(update):
            return

        if not hasattr(self.agent_loop, "jobs") or not self.agent_loop.jobs:
            await update.message.reply_text("Batch Job Runner subsystem is not initialized.")
            return

        st = self.agent_loop.jobs.get_status()
        if "error" in st:
            await update.message.reply_text(f"ℹ️ {st['error']}")
            return

        active_item_text = "None"
        if st.get("active_item"):
            active_item_text = f"Item `{st['active_item'].get('id')}`"

        worker_text = "🟢 Running in background" if st.get("is_running_in_background") else "⏸️ Idle / Stopped"
        pct = st.get("progress_percent", 0.0)
        jobs_text = (
            f"📋 *Batch Job Queue:* `{st.get('job_name')}`\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• *Job ID:* `{st.get('job_id')}`\n"
            f"• *Skill:* `{st.get('skill_name')}`\n"
            f"• *Status:* `{st.get('status')}`\n"
            f"• *Worker:* {worker_text}\n"
            f"• *Progress:* {st.get('completed_items')}/{st.get('total_items')} ({pct}%)\n"
            f"• *Pending:* {st.get('pending_items')} | *Failed:* {st.get('failed_items')}\n"
            f"• *Active Item:* {active_item_text}\n"
        )
        await update.message.reply_text(jobs_text, parse_mode="Markdown")

    async def _handle_run(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._verify_update_auth(update):
            return

        if hasattr(self.agent_loop, "jobs") and self.agent_loop.jobs:
            args = context.args or []
            target = args[0] if args else None
            res = self.agent_loop.jobs.run_in_background(job_id_or_path=target)
            if res.get("success"):
                await update.message.reply_text(
                    f"🚀 *Started Background Batch Worker*\n"
                    f"• *Job:* `{res.get('job_name', 'Job')}` (`{res.get('job_id')}`)\n"
                    f"• *Progress:* {res.get('completed_items')}/{res.get('total_items')} ({res.get('progress_percent')}%)",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(f"⚠️ {res.get('error', 'Could not run batch job.')}")
        else:
            await update.message.reply_text("Batch Job Runner subsystem unavailable.")

    async def _handle_summary(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._verify_update_auth(update):
            return

        if hasattr(self.agent_loop, "jobs") and self.agent_loop.jobs:
            args = context.args or []
            target = args[0] if args else None
            res = self.agent_loop.jobs.generate_summary(job_id_or_path=target)
            if res.get("success"):
                md_path = res.get("markdown_path", "")
                total = res.get("total_items", 0)
                comp = res.get("completed_items", 0)
                avg = res.get("average_score", 0.0)
                t1 = res.get("tier_1_count", 0)
                t2 = res.get("tier_2_count", 0)
                t3 = res.get("tier_3_count", 0)
                needs_rev = res.get("needs_review_count", 0)

                msg = (
                    f"📊 *Batch Job Ranking Leaderboard*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"• *Progress:* {comp}/{total} items evaluated\n"
                    f"• *Average Score:* {avg}/100\n"
                    f"• *🏆 Tier 1 (80–100):* {t1}\n"
                    f"• *💡 Tier 2 (60–79):* {t2}\n"
                    f"• *⚠️ Tier 3 (<60):* {t3}\n"
                )
                if needs_rev > 0:
                    msg += f"• *⚠️ Needs Review:* {needs_rev}\n"
                msg += f"\n📁 *Report:* `{md_path}`"
                await update.message.reply_text(msg, parse_mode="Markdown")
            else:
                await update.message.reply_text(f"⚠️ {res.get('error', 'Could not generate ranking summary.')}")
        else:
            await update.message.reply_text("Batch Job Runner subsystem unavailable.")

    async def _handle_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._verify_update_auth(update):
            return

        if hasattr(self.agent_loop, "jobs") and self.agent_loop.jobs:
            res = self.agent_loop.jobs.pause()
            if res.get("success"):
                await update.message.reply_text(
                    f"⏸️ Batch job `{res.get('job_id')}` will pause safely after the active item.",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(f"⚠️ {res.get('error', 'Could not pause job.')}")
        else:
            await update.message.reply_text("Batch Job Runner subsystem unavailable.")

    async def _handle_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._verify_update_auth(update):
            return

        if hasattr(self.agent_loop, "jobs") and self.agent_loop.jobs:
            res = self.agent_loop.jobs.resume()
            if res.get("success"):
                await update.message.reply_text(
                    f"▶️ Resumed batch job `{res.get('job_id')}`.",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(f"⚠️ {res.get('error', 'Could not resume job.')}")
        else:
            await update.message.reply_text("Batch Job Runner subsystem unavailable.")

    async def _handle_kill(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._verify_update_auth(update):
            return

        if not self.remote_kill_switch_enabled:
            await update.message.reply_text("⚠️ Remote kill switch is disabled in configuration.")
            return

        if hasattr(self.agent_loop, "kill_switch") and self.agent_loop.kill_switch:
            self.agent_loop.kill_switch.trigger()
            await update.message.reply_text("🛑 *EMERGENCY STOP TRIPPED!* All tasks and child processes halted.", parse_mode="Markdown")
        else:
            await update.message.reply_text("Kill switch reference unavailable.")

    async def _handle_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._verify_update_auth(update):
            return

        cleared = False
        if hasattr(self.agent_loop, "conversation_store") and self.agent_loop.conversation_store:
            new_id = self.agent_loop.conversation_store.create_conversation("Telegram Remote Session")
            self.agent_loop.active_conversation_id = new_id
            cleared = True

        if hasattr(self.agent_loop, "context") and self.agent_loop.context:
            try:
                self.agent_loop.context.clear()
                cleared = True
            except Exception:
                pass

        if cleared:
            await update.message.reply_text("🧹 Active conversation reset. Fresh context initialized.")
        else:
            await update.message.reply_text("Conversation store not active.")

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive free-text prompt and dispatch to AgentLoop in background thread."""
        if not await self._verify_update_auth(update):
            return

        prompt = update.message.text.strip()
        if not prompt:
            return

        chat_id = update.effective_chat.id
        await update.message.reply_text(f"⏳ *Task received:* \"{prompt[:80]}\"...\nStarting execution...", parse_mode="Markdown")

        def _execute_task():
            try:
                res = self.agent_loop.run_task(prompt)
                summary = res.get("summary", "Task completed.")
                status_icon = "✅" if res.get("success") else "⚠️"
                reply_msg = f"{status_icon} *Task Finished*\n━━━━━━━━━━━━━━━━━━━━━━\n{summary}"
                self.broadcast_message(reply_msg)
            except Exception as e:
                logger.error("Error executing delegated Telegram task: %s", e)
                self.broadcast_message(f"❌ *Task Error:*\n{str(e)}")

        threading.Thread(target=_execute_task, daemon=True).start()

    # =========================================================================
    # Interactive Approvals & Gatekeeper Integration
    # =========================================================================

    def request_confirmation(
        self,
        action_type: str,
        details: Dict[str, Any],
        risk_level: int,
        reason: str,
        timeout_seconds: float = 120.0,
        cancel_event: Optional[threading.Event] = None,
    ) -> Optional[Tuple[bool, bool]]:
        """Deliver interactive confirmation card to Telegram and await user button tap."""
        if not self.is_running() or not self.allowed_user_ids:
            return None

        cid = uuid.uuid4().hex[:8]
        event = threading.Event()
        record: Dict[str, Any] = {
            "id": cid,
            "action_type": action_type,
            "details": details,
            "risk_level": risk_level,
            "reason": reason,
            "timestamp": time.time(),
            "event": event,
            "result": None,
            "sent_messages": [],  # List of (chat_id, message_id)
        }

        with self._confirm_lock:
            self._pending_confirmations[cid] = record

        # Build inline keyboard buttons
        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"confirm:{cid}:approve"),
                InlineKeyboardButton("❌ Reject", callback_data=f"confirm:{cid}:reject"),
            ]
        ]
        row_2 = []
        if risk_level == int(RiskLevel.LEVEL_2_SHELL):
            row_2.append(InlineKeyboardButton("💾 Always Allow", callback_data=f"confirm:{cid}:always"))
        row_2.append(InlineKeyboardButton("🛑 Kill Switch", callback_data=f"confirm:{cid}:kill"))
        keyboard.append(row_2)
        reply_markup = InlineKeyboardMarkup(keyboard)

        risk_label = "Destructive" if risk_level == 3 else ("Shell Execution" if risk_level == 2 else "UI Action")
        cmd_text = details.get("command") or details.get("rule") or str(details)
        card_text = (
            "⚠️ *EidosBot Safety Confirmation Needed*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• *Action:* `{action_type}`\n"
            f"• *Risk Level:* Level {risk_level} ({risk_label})\n"
            f"• *Reason:* {reason}\n"
            f"• *Target / Command:*\n`{cmd_text[:200]}`\n"
        )

        targets = set(self.allowed_user_ids) | set(self._known_chat_ids)

        async def _dispatch_card():
            bot = self.app.bot
            for chat_id in targets:
                try:
                    sent = await bot.send_message(
                        chat_id=chat_id,
                        text=card_text,
                        reply_markup=reply_markup,
                        parse_mode="Markdown",
                    )
                    with self._confirm_lock:
                        if cid in self._pending_confirmations:
                            self._pending_confirmations[cid]["sent_messages"].append((chat_id, sent.message_id))
                except Exception as e:
                    logger.debug("Failed sending approval card to %s: %s", chat_id, e)

        asyncio.run_coroutine_threadsafe(_dispatch_card(), self._loop)

        # Wait for user button tap or timeout
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            if event.is_set() or (cancel_event and cancel_event.is_set()):
                break
            time.sleep(0.05)

        with self._confirm_lock:
            res = record.get("result")
            self._pending_confirmations.pop(cid, None)

        return res if event.is_set() and res is not None else None

    def resolve_confirmation(
        self,
        request_id: str,
        approved: bool,
        allowlist: bool = False,
        actor_user_id: Optional[int] = None,
    ) -> bool:
        """Resolve pending confirmation request and update inline card on Telegram."""
        with self._confirm_lock:
            record = self._pending_confirmations.get(request_id)
            if not record:
                return False

            record["result"] = (approved, allowlist)
            record["event"].set()
            sent_msgs = list(record.get("sent_messages", []))

        # Update message card text
        if self._loop and self.app and sent_msgs:
            outcome_text = (
                "✅ *Approved by user*"
                if approved and not allowlist
                else ("💾 *Approved & Always Allowed*" if approved and allowlist else "❌ *Denied by user*")
            )
            card_update = (
                f"⚠️ *EidosBot Confirmation Resolved*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• *Action:* `{record['action_type']}`\n"
                f"• *Decision:* {outcome_text}\n"
            )

            async def _edit():
                bot = self.app.bot
                for chat_id, msg_id in sent_msgs:
                    try:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=msg_id,
                            text=card_update,
                            reply_markup=None,
                            parse_mode="Markdown",
                        )
                    except Exception:
                        pass

            asyncio.run_coroutine_threadsafe(_edit(), self._loop)

        return True

    async def _handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Parse button tap on approval cards."""
        query = update.callback_query
        if not query:
            return

        user_id = None
        if query.from_user and hasattr(query.from_user, "id"):
            user_id = query.from_user.id
        elif update.effective_user and hasattr(update.effective_user, "id"):
            user_id = update.effective_user.id

        if not self.is_authorized(user_id):
            await query.answer("⛔ Unauthorized user ID", show_alert=True)
            return

        data = query.data or ""
        # Expected format: confirm:{cid}:{action} or {action}:{cid}
        parts = data.split(":")
        if len(parts) == 3 and parts[0] == "confirm":
            _, cid, action = parts
        elif len(parts) == 2:
            action, cid = parts
        else:
            await query.answer()
            return

        feedback = "Response recorded."
        if action == "approve":
            feedback = "Command approved."
            self.resolve_confirmation(cid, approved=True, allowlist=False, actor_user_id=user_id)
        elif action == "reject":
            feedback = "Command rejected."
            self.resolve_confirmation(cid, approved=False, allowlist=False, actor_user_id=user_id)
        elif action == "always":
            feedback = "Command permanently added to allowlist."
            with self._confirm_lock:
                rec = self._pending_confirmations.get(cid, {})
                cmd = rec.get("command") or rec.get("details", {}).get("command")
            if cmd and hasattr(self.agent_loop, "gatekeeper") and self.agent_loop.gatekeeper:
                self.agent_loop.gatekeeper.add_to_allowlist(cmd)
            self.resolve_confirmation(cid, approved=True, allowlist=True, actor_user_id=user_id)
        elif action == "kill":
            feedback = "🚨 EMERGENCY KILL SWITCH TRIGGERED"
            if hasattr(self.agent_loop, "kill_switch") and self.agent_loop.kill_switch:
                self.agent_loop.kill_switch.trigger()
            self.resolve_confirmation(cid, approved=False, allowlist=False, actor_user_id=user_id)

        try:
            await query.answer(text=feedback)
        except Exception:
            pass

    # Alias for tests and legacy references
    _handle_approval_callback = _handle_callback_query

    def create_confirmation_callback(self) -> Optional[Callable[..., Optional[Tuple[bool, bool]]]]:
        """Create callback suitable for Gatekeeper.custom_prompt_callback."""
        if not self.is_running() or not self.allowed_user_ids:
            return None

        def telegram_callback(
            action_type: str, details: Dict[str, Any], risk_level: RiskLevel, reason: str
        ) -> Optional[Tuple[bool, bool]]:
            return self.request_confirmation(action_type, details, int(risk_level), reason)

        return telegram_callback

    @staticmethod
    def combine_callbacks(
        *callbacks: Optional[Callable[..., Optional[Tuple[bool, bool]]]],
        timeout_seconds: float = 120.0,
    ) -> Callable[..., Tuple[bool, bool]]:
        """Multi-channel combiner racing desktop GUI, web gateway, and Telegram.

        Whichever channel user interacts with first resolves the prompt.
        """
        valid_callbacks = [cb for cb in callbacks if cb is not None]
        if not valid_callbacks:
            return lambda act, det, risk, reason: (False, False)
        if len(valid_callbacks) == 1:
            def single_cb(act, det, risk, reason):
                res = valid_callbacks[0](act, det, risk, reason)
                return res if res is not None else (False, False)
            return single_cb

        def composite(
            action_type: str, details: Dict[str, Any], risk_level: RiskLevel, reason: str
        ) -> Tuple[bool, bool]:
            outcome_queue: List[Tuple[bool, bool]] = []
            done_event = threading.Event()

            def _worker(cb_func):
                try:
                    res = cb_func(action_type, details, risk_level, reason)
                    if not done_event.is_set() and res is not None:
                        outcome_queue.append(res)
                        done_event.set()
                except Exception as e:
                    logger.debug("Error in confirmation channel: %s", e)

            threads = [threading.Thread(target=_worker, args=(cb,), daemon=True) for cb in valid_callbacks]
            for t in threads:
                t.start()

            done_event.wait(timeout=timeout_seconds)
            done_event.set()
            return outcome_queue[0] if outcome_queue else (False, False)

        return composite

    # =========================================================================
    # Milestone & Task Progress Notifications
    # =========================================================================

    def on_job_milestone(self, queue: Any, item: Any):
        """Broadcast batch job progress updates at configured intervals."""
        if not self.is_running():
            return

        pct = queue.get_progress_percent() if hasattr(queue, "get_progress_percent") else 0.0
        status_icon = "🎉" if getattr(queue, "status", "") == "completed" else "📊"
        msg = (
            f"{status_icon} *Batch Job Milestone: {getattr(queue, 'job_name', 'Job')}*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• *Progress:* {getattr(queue, 'completed_items', 0)}/{getattr(queue, 'total_items', 0)} ({pct}%)\n"
            f"• *Status:* `{getattr(queue, 'status', 'in_progress')}`\n"
            f"• *Latest Item:* `{getattr(item, 'id', 'item')}`\n"
        )
        if hasattr(item, "result") and isinstance(item.result, dict):
            summary = item.result.get("summary")
            if summary:
                msg += f"• *Summary:* {summary[:120]}\n"

        self.broadcast_message(msg)

    def on_task_finished(self, summary: str, success: bool):
        """Broadcast task completion summary to Telegram."""
        if not self.is_running():
            return
        icon = "✅" if success else "⚠️"
        msg = f"{icon} *EidosBot Task Summary*\n━━━━━━━━━━━━━━━━━━━━━━\n{summary}"
        self.broadcast_message(msg)
