"""EidosBot main entrypoint supporting PyQt6 Desktop HUD and CLI modes."""

import argparse
import logging
import os
from pathlib import Path
import signal
import sys
from typing import Optional, Set
import yaml

from eidos.core.loop import AgentLoop
from eidos.safety.kill_switch import KillSwitch


class SecretRedactionFilter(logging.Filter):
    """Redacts sensitive bot tokens, bearer keys, and credentials from all log outputs."""

    def __init__(self, explicit_secrets: Optional[Set[str]] = None):
        super().__init__()
        self.explicit_secrets: Set[str] = set()
        if explicit_secrets:
            for s in explicit_secrets:
                if s and len(str(s).strip()) >= 5:
                    self.explicit_secrets.add(str(s).strip())

    def add_secret(self, secret: Optional[str]):
        if secret and len(str(secret).strip()) >= 5:
            self.explicit_secrets.add(str(secret).strip())

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            msg = record.msg
            # Redact Telegram bot token pattern (e.g. 123456789:ABCdefGHIjklMNOpqrsTUVwxyz-1234567)
            import re
            msg = re.sub(r'\b\d{8,11}:[A-Za-z0-9_-]{35}\b', '***TELEGRAM_BOT_TOKEN_REDACTED***', msg)
            # Redact query params: token=... or token%3D...
            msg = re.sub(r'([?&]token(?:%3D|=))([^\s&"\'<>]+)', r'\1***REDACTED***', msg, flags=re.IGNORECASE)
            # Redact Authorization header values: Bearer ...
            msg = re.sub(r'(Bearer\s+)([A-Za-z0-9_\-\.]{8,})', r'\1***REDACTED***', msg)
            # Redact explicit tokens
            for s in self.explicit_secrets:
                msg = msg.replace(s, '***REDACTED***')
            record.msg = msg
        return True


secret_filter = SecretRedactionFilter()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
root_logger = logging.getLogger()
for handler in root_logger.handlers:
    handler.addFilter(secret_filter)

logger = logging.getLogger("eidos")

# Suppress verbose third-party HTTP and polling logs (e.g. httpx getUpdates in Telegram bot)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def load_config(config_path: str = "config.yaml") -> dict:
    """Load system configuration from YAML."""
    p = Path(config_path)
    if not p.exists():
        p_example = Path("config.example.yaml")
        if p_example.exists():
            with open(p_example, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def print_banner():
    """Display startup ASCII banner."""
    print("=" * 65)
    print("  🤖  EIDOSBOT - OS-Level Desktop Co-Pilot (PyQt6 HUD & CLI)  🤖")
    print("  Local Model: Ollama | Hardware: RTX 5060 Ti | OS: Linux Mint")
    print("  🛑 Global Kill Switch: Press [ Ctrl + Alt + Esc ] to halt")
    print("=" * 65)


def run_gui_mode(
    cfg: dict,
    kill_switch: KillSwitch,
    start_gateway: bool = False,
    start_telegram: bool = False,
    config_path: str = "config.yaml",
):
    """Launch EidosBot with PyQt6 Transparent HUD and Floating Status Pill."""
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    from eidos.ui.app import UIController

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setApplicationName("EidosBot")
    app.setDesktopFileName("eidosbot.desktop")
    # Prevent Qt from terminating the app when floating dialogs (Settings, Allowlist) close
    app.setQuitOnLastWindowClosed(False)

    from PyQt6.QtGui import QIcon
    from eidos.ui.styles import apply_global_styles

    icon_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets", "eidos_icon.png"))
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # Apply global Dracula styles and high-contrast tooltip theme
    apply_global_styles(app)

    # Allow Python signal handling (e.g. Ctrl+C in terminal)
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    ui_controller = UIController(config=cfg, kill_switch=kill_switch, config_path=config_path)
    agent = AgentLoop(config=cfg, kill_switch=kill_switch)
    ui_controller.set_agent_loop(agent)

    gateway = None
    telegram_gw = None
    gw_cfg = cfg.get("gateway", {})
    tg_cfg = gw_cfg.get("telegram", {})

    # Register known secrets with the redaction filter to prevent accidental log leaks
    if gw_cfg.get("auth_token"):
        secret_filter.add_secret(gw_cfg.get("auth_token"))
    if tg_cfg.get("bot_token"):
        secret_filter.add_secret(tg_cfg.get("bot_token"))

    if gw_cfg.get("enabled", False) or start_gateway:
        from eidos.gateway import RemoteGatewayServer
        gateway = RemoteGatewayServer(
            agent_loop=agent,
            host=gw_cfg.get("host", "0.0.0.0"),
            port=int(gw_cfg.get("port", 8420)),
            auth_token=gw_cfg.get("auth_token"),
            token_env_var=gw_cfg.get("auth_token_env", "EIDOS_GATEWAY_AUTH_TOKEN"),
            token_file=gw_cfg.get("token_file", "~/.eidos/gateway.token"),
        )
        gateway.start()

    if tg_cfg.get("enabled", False) or start_telegram:
        from eidos.gateway import TelegramGateway
        telegram_gw = TelegramGateway(
            agent_loop=agent,
            token=tg_cfg.get("bot_token"),
            allowed_user_ids=tg_cfg.get("allowed_user_ids"),
            token_env_var=tg_cfg.get("bot_token_env", "EIDOS_TELEGRAM_BOT_TOKEN"),
            token_file=tg_cfg.get("token_file", "~/.eidos/telegram.token"),
            notifications_config=tg_cfg.get("notifications"),
            remote_kill_switch_enabled=tg_cfg.get("remote_kill_switch_enabled", True),
        )
        telegram_gw.start()

    # Multi-channel confirmation: Desktop modal + Web Gateway + Telegram
    if gateway:
        ui_controller.set_remote_gateway(gateway)
    if telegram_gw:
        ui_controller.set_telegram_gateway(telegram_gw)
    ui_controller._rebind_confirmation_callbacks()

    ui_controller.show()
    logger.info("EidosBot PyQt6 HUD & Floating Pill active on desktop.")

    QTimer.singleShot(500, ui_controller.maybe_show_profile_onboarding)

    # Periodic timer so Python processes interpreter signals
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(500)

    try:
        app.exec()
    finally:
        if telegram_gw:
            telegram_gw.stop()
        if gateway:
            gateway.stop()
        kill_switch.stop()


def run_cli_mode(
    cfg: dict,
    kill_switch: KillSwitch,
    prompt_args: list,
    max_steps: Optional[int],
    start_gateway: bool = False,
    start_telegram: bool = False,
):
    """Run EidosBot in terminal REPL or one-shot mode."""
    agent = AgentLoop(config=cfg, kill_switch=kill_switch)

    gateway = None
    telegram_gw = None
    gw_cfg = cfg.get("gateway", {})
    tg_cfg = gw_cfg.get("telegram", {})

    # Register known secrets with the redaction filter to prevent accidental log leaks
    if gw_cfg.get("auth_token"):
        secret_filter.add_secret(gw_cfg.get("auth_token"))
    if tg_cfg.get("bot_token"):
        secret_filter.add_secret(tg_cfg.get("bot_token"))

    if gw_cfg.get("enabled", False) or start_gateway:
        from eidos.gateway import RemoteGatewayServer
        gateway = RemoteGatewayServer(
            agent_loop=agent,
            host=gw_cfg.get("host", "0.0.0.0"),
            port=int(gw_cfg.get("port", 8420)),
            auth_token=gw_cfg.get("auth_token"),
            token_env_var=gw_cfg.get("auth_token_env", "EIDOS_GATEWAY_AUTH_TOKEN"),
            token_file=gw_cfg.get("token_file", "~/.eidos/gateway.token"),
        )
        gateway.start()

    if tg_cfg.get("enabled", False) or start_telegram:
        from eidos.gateway import TelegramGateway
        telegram_gw = TelegramGateway(
            agent_loop=agent,
            token=tg_cfg.get("bot_token"),
            allowed_user_ids=tg_cfg.get("allowed_user_ids"),
            token_env_var=tg_cfg.get("bot_token_env", "EIDOS_TELEGRAM_BOT_TOKEN"),
            token_file=tg_cfg.get("token_file", "~/.eidos/telegram.token"),
            notifications_config=tg_cfg.get("notifications"),
            remote_kill_switch_enabled=tg_cfg.get("remote_kill_switch_enabled", True),
        )
        telegram_gw.start()

    if gateway or telegram_gw:
        from eidos.gateway import TelegramGateway
        agent.gatekeeper.custom_prompt_callback = TelegramGateway.combine_callbacks(
            gateway.create_confirmation_callback() if gateway else None,
            telegram_gw.create_confirmation_callback() if telegram_gw else None,
        )

    try:
        # One-shot task mode
        if prompt_args:
            task = " ".join(prompt_args)
            print(f"\n🎯 [Task]: {task}\n")
            result = agent.run_task(task, max_steps=max_steps)
            print("\n" + "-" * 50)
            if result["success"]:
                print(f"✅ Task Completed ({result['steps_taken']} steps):")
                print(result["summary"])
            else:
                print(f"❌ Task Ended ({result['steps_taken']} steps):")
                print(result["summary"])
            return

        # Interactive REPL mode
        print("\nEntering Interactive Session. Type 'exit' or 'quit' to leave.")
        while True:
            if kill_switch.is_triggered():
                print("\n🚨 [WARNING]: Kill switch is currently tripped!")
                reset_choice = input("Reset kill switch to continue? [y/N]: ").strip().lower()
                if reset_choice in ("y", "yes"):
                    kill_switch.reset()
                    print("✅ Kill switch reset.")
                else:
                    print("Exiting...")
                    break

            try:
                user_input = input("\n👤 Eidos> ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nGoodbye!")
                break

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "q"):
                print("Exiting EidosBot. Goodbye!")
                break

            result = agent.run_task(user_input, max_steps=max_steps)
            print("\n" + "-" * 50)
            if result["success"]:
                print(f"✅ [Completed in {result['steps_taken']} steps]:")
                print(result["summary"])
            else:
                print(f"❌ [Result ({result['steps_taken']} steps)]:")
                print(result["summary"])

    finally:
        if telegram_gw:
            telegram_gw.stop()
        if gateway:
            gateway.stop()
        kill_switch.stop()


def run_batch_cli(
    cfg: dict,
    kill_switch: KillSwitch,
    args: argparse.Namespace,
    start_gateway: bool = False,
    start_telegram: bool = False,
) -> int:
    """Execute batch job operations directly from CLI without full GUI."""
    agent = AgentLoop(config=cfg, kill_switch=kill_switch)

    gateway = None
    telegram_gw = None
    gw_cfg = cfg.get("gateway", {})
    tg_cfg = gw_cfg.get("telegram", {})

    if gw_cfg.get("auth_token"):
        secret_filter.add_secret(gw_cfg.get("auth_token"))
    if tg_cfg.get("bot_token"):
        secret_filter.add_secret(tg_cfg.get("bot_token"))

    if gw_cfg.get("enabled", False) or start_gateway:
        from eidos.gateway import RemoteGatewayServer
        gateway = RemoteGatewayServer(
            agent_loop=agent,
            host=gw_cfg.get("host", "0.0.0.0"),
            port=int(gw_cfg.get("port", 8420)),
            auth_token=gw_cfg.get("auth_token"),
            token_env_var=gw_cfg.get("auth_token_env", "EIDOS_GATEWAY_AUTH_TOKEN"),
            token_file=gw_cfg.get("token_file", "~/.eidos/gateway.token"),
        )
        gateway.start()

    if tg_cfg.get("enabled", False) or start_telegram:
        from eidos.gateway import TelegramGateway
        telegram_gw = TelegramGateway(
            agent_loop=agent,
            token=tg_cfg.get("bot_token"),
            allowed_user_ids=tg_cfg.get("allowed_user_ids"),
            token_env_var=tg_cfg.get("bot_token_env", "EIDOS_TELEGRAM_BOT_TOKEN"),
            token_file=tg_cfg.get("token_file", "~/.eidos/telegram.token"),
            notifications_config=tg_cfg.get("notifications"),
            remote_kill_switch_enabled=tg_cfg.get("remote_kill_switch_enabled", True),
        )
        telegram_gw.start()

    try:
        active_job_id = None

        if args.job_init:
            if not args.items:
                print("❌ Error: --items <path_to_csv_or_json> is required when using --job-init.")
                return 1
            try:
                queue = agent.jobs.initialize_job(
                    job_name=args.job_init,
                    skill_name=args.skill,
                    input_data=args.items,
                    output_dir=args.output_dir,
                )
                active_job_id = queue.job_id
                print(f"✅ Initialized batch job '{queue.job_name}'")
                print(f"   • Job ID:     {queue.job_id}")
                print(f"   • Skill:      {queue.skill_name}")
                print(f"   • Items:      {queue.total_items}")
                print(f"   • Output Dir: {queue.output_dir}")
            except Exception as e:
                print(f"❌ Failed to initialize batch job: {e}")
                return 1

        if args.job_run is not None:
            target = None if args.job_run == "__ACTIVE__" else args.job_run
            if not target and active_job_id:
                target = active_job_id
            print(f"\n🚀 Starting batch job execution (pacing: {args.pause_sec}s, max_items: {args.max_items or 'all'})...")
            try:
                res = agent.jobs.run_job(
                    job_id_or_path=target,
                    pause_between_items_sec=args.pause_sec,
                    max_items=args.max_items,
                    kill_switch_checker=kill_switch.is_triggered,
                )
                print("\n" + "=" * 50)
                if res.get("aborted"):
                    print("🛑 Batch run aborted by emergency kill switch.")
                elif res.get("done"):
                    print("🏆 Batch run completed successfully!")
                elif res.get("paused"):
                    print("⏸️  Batch run paused.")
                else:
                    print(f"ℹ️  Batch run finished: {res.get('message', '')}")

                print(f"   • Completed: {res.get('completed_items')}/{res.get('total_items')} ({res.get('progress_percent')}%)")
                summary = res.get("summary")
                if summary and summary.get("success"):
                    print(f"   • Average Viability: {summary.get('average_score')}/100")
                    print(f"   • 🏆 Tier 1: {summary.get('tier_1_count')} | 💡 Tier 2: {summary.get('tier_2_count')} | ⚠️ Tier 3: {summary.get('tier_3_count')}")
                    print(f"   • Leaderboard: {summary.get('markdown_path')}")
                    print(f"   • CSV Export:  {summary.get('csv_path')}")
            except Exception as e:
                print(f"❌ Error during batch execution: {e}")
                return 1

        if args.job_status is not None:
            target = None if args.job_status == "__ACTIVE__" else args.job_status
            status = agent.jobs.get_status(job_id_or_path=target)
            if "error" in status:
                print(f"⚠️ {status['error']}")
            else:
                print(f"\n📊 Batch Job Status: {status.get('job_name')} ({status.get('job_id')})")
                print(f"   • Status:    {status.get('status')}")
                print(f"   • Progress:  {status.get('completed_items')}/{status.get('total_items')} ({status.get('progress_percent')}%)")
                print(f"   • Pending:   {status.get('pending_items')}")
                print(f"   • Failed:    {status.get('failed_items')}")
                print(f"   • Output:    {status.get('output_dir')}")

        if args.job_summary is not None:
            target = None if args.job_summary == "__ACTIVE__" else args.job_summary
            summary = agent.jobs.generate_summary(job_id_or_path=target)
            if not summary.get("success"):
                print(f"⚠️ {summary.get('error', 'Could not generate ranking summary.')}")
            else:
                print(f"\n🏆 Ranking Leaderboard Generated:")
                print(f"   • Evaluated:     {summary.get('completed_items')}/{summary.get('total_items')}")
                print(f"   • Average Score: {summary.get('average_score')}/100")
                print(f"   • 🏆 Tier 1 (80-100): {summary.get('tier_1_count')}")
                print(f"   • 💡 Tier 2 (60-79):  {summary.get('tier_2_count')}")
                print(f"   • ⚠️ Tier 3 (<60):    {summary.get('tier_3_count')}")
                print(f"   • Markdown: {summary.get('markdown_path')}")
                print(f"   • CSV:      {summary.get('csv_path')}")

        return 0
    finally:
        if telegram_gw:
            telegram_gw.stop()
        if gateway:
            gateway.stop()
        kill_switch.stop()


def cli_entrypoint():
    """Main CLI and GUI launcher entrypoint."""
    parser = argparse.ArgumentParser(description="EidosBot OS-Level Autonomous Desktop Agent")
    parser.add_argument("prompt", nargs="*", help="Goal or prompt for EidosBot (optional)")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument(
        "--steps",
        type=int,
        default=None,
        help="Maximum cycle steps per task (default: agent.max_steps, or 50)",
    )
    parser.add_argument("--gui", action="store_true", help="Launch PyQt6 Desktop HUD and Floating Pill")
    parser.add_argument("--cli", action="store_true", help="Force headless CLI mode")
    parser.add_argument("--gateway", "--with-gateway", dest="gateway", action="store_true", help="Start remote mobile gateway server")
    parser.add_argument("--telegram", "--with-telegram", dest="telegram", action="store_true", help="Start remote Telegram approval bot gateway")

    # Batch Job Subsystem Arguments
    parser.add_argument("--job-init", dest="job_init", help="Initialize a batch job queue with given job name")
    parser.add_argument("--skill", dest="skill", default="invention-evaluator", help="Skill procedure name for batch job (default: invention-evaluator)")
    parser.add_argument("--items", dest="items", help="Path to items file (.csv or .json) or JSON string")
    parser.add_argument("--output-dir", dest="output_dir", help="Destination directory for batch job queue and artifacts")
    parser.add_argument("--run", "--job-run", dest="job_run", nargs="?", const="__ACTIVE__", default=None, help="Execute batch job (optionally specify job ID or path)")
    parser.add_argument("--pause-sec", dest="pause_sec", type=float, default=2.0, help="Pacing delay between items in seconds (default: 2.0)")
    parser.add_argument("--max-items", dest="max_items", type=int, default=None, help="Maximum number of items to process in this run")
    parser.add_argument("--job-status", dest="job_status", nargs="?", const="__ACTIVE__", default=None, help="Inspect status of a batch job")
    parser.add_argument("--job-summary", dest="job_summary", nargs="?", const="__ACTIVE__", default=None, help="Generate or view ranked leaderboard summary for a batch job")

    args = parser.parse_args()

    cfg = load_config(args.config)
    print_banner()

    # Initialize Global Kill Switch
    hotkey = cfg.get("safety", {}).get("kill_switch_hotkey", "<ctrl>+<alt>+<esc>")
    kill_switch = KillSwitch(hotkey_str=hotkey)
    kill_switch.start()

    # Determine mode:
    # 0. If batch job operations requested -> run batch CLI
    if args.job_init or args.job_run is not None or args.job_status is not None or args.job_summary is not None:
        sys.exit(run_batch_cli(cfg, kill_switch, args, start_gateway=args.gateway, start_telegram=args.telegram))

    # 1. If explicit --gui -> GUI mode
    # 2. If prompt supplied -> one-shot CLI mode
    # 3. If explicit --cli -> interactive CLI REPL
    # 4. Default: GUI mode if DISPLAY is available, otherwise CLI
    if args.gui:
        run_gui_mode(
            cfg,
            kill_switch,
            start_gateway=args.gateway,
            start_telegram=args.telegram,
            config_path=args.config,
        )
    elif args.prompt or args.cli:
        run_cli_mode(cfg, kill_switch, args.prompt, args.steps, start_gateway=args.gateway, start_telegram=args.telegram)
    else:
        # Default with no arguments: Check if DISPLAY is available
        if os.environ.get("DISPLAY"):
            run_gui_mode(
                cfg,
                kill_switch,
                start_gateway=args.gateway,
                start_telegram=args.telegram,
                config_path=args.config,
            )
        else:
            run_cli_mode(cfg, kill_switch, args.prompt, args.steps, start_gateway=args.gateway, start_telegram=args.telegram)


if __name__ == "__main__":
    cli_entrypoint()
