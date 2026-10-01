"""EidosBot main entrypoint supporting PyQt6 Desktop HUD and CLI modes."""

import argparse
import logging
import os
from pathlib import Path
import signal
import sys
import yaml

from eidos.core.loop import AgentLoop
from eidos.safety.kill_switch import KillSwitch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("eidos")

# Suppress verbose third-party HTTP and polling logs (e.g. httpx getUpdates in Telegram bot)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


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


def run_gui_mode(cfg: dict, kill_switch: KillSwitch, start_gateway: bool = False, start_telegram: bool = False):
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

    ui_controller = UIController(config=cfg, kill_switch=kill_switch)
    agent = AgentLoop(config=cfg, kill_switch=kill_switch)
    ui_controller.set_agent_loop(agent)

    gateway = None
    telegram_gw = None
    gw_cfg = cfg.get("gateway", {})
    if gw_cfg.get("enabled", False) or start_gateway:
        from eidos.gateway import RemoteGatewayServer
        gateway = RemoteGatewayServer(
            agent_loop=agent,
            host=gw_cfg.get("host", "0.0.0.0"),
            port=int(gw_cfg.get("port", 8420)),
            auth_token=gw_cfg.get("auth_token"),
        )
        gateway.start()

    tg_cfg = gw_cfg.get("telegram", {})
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
    max_steps: int,
    start_gateway: bool = False,
    start_telegram: bool = False,
):
    """Run EidosBot in terminal REPL or one-shot mode."""
    agent = AgentLoop(config=cfg, kill_switch=kill_switch)

    gateway = None
    telegram_gw = None
    gw_cfg = cfg.get("gateway", {})
    if gw_cfg.get("enabled", False) or start_gateway:
        from eidos.gateway import RemoteGatewayServer
        gateway = RemoteGatewayServer(
            agent_loop=agent,
            host=gw_cfg.get("host", "0.0.0.0"),
            port=int(gw_cfg.get("port", 8420)),
            auth_token=gw_cfg.get("auth_token"),
        )
        gateway.start()

    tg_cfg = gw_cfg.get("telegram", {})
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


def cli_entrypoint():
    """Main CLI and GUI launcher entrypoint."""
    parser = argparse.ArgumentParser(description="EidosBot OS-Level Autonomous Desktop Agent")
    parser.add_argument("prompt", nargs="*", help="Goal or prompt for EidosBot (optional)")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--steps", type=int, default=15, help="Maximum cycle steps per task")
    parser.add_argument("--gui", action="store_true", help="Launch PyQt6 Desktop HUD and Floating Pill")
    parser.add_argument("--cli", action="store_true", help="Force headless CLI mode")
    parser.add_argument("--gateway", action="store_true", help="Start remote mobile gateway server")
    parser.add_argument("--telegram", "--with-telegram", dest="telegram", action="store_true", help="Start remote Telegram approval bot gateway")
    args = parser.parse_args()

    cfg = load_config(args.config)
    print_banner()

    # Initialize Global Kill Switch
    hotkey = cfg.get("safety", {}).get("kill_switch_hotkey", "<ctrl>+<alt>+<esc>")
    kill_switch = KillSwitch(hotkey_str=hotkey)
    kill_switch.start()

    # Determine mode:
    # 1. If explicit --gui -> GUI mode
    # 2. If prompt supplied -> one-shot CLI mode
    # 3. If explicit --cli -> interactive CLI REPL
    # 4. Default: GUI mode if DISPLAY is available, otherwise CLI
    if args.gui:
        run_gui_mode(cfg, kill_switch, start_gateway=args.gateway, start_telegram=args.telegram)
    elif args.prompt or args.cli:
        run_cli_mode(cfg, kill_switch, args.prompt, args.steps, start_gateway=args.gateway, start_telegram=args.telegram)
    else:
        # Default with no arguments: Check if DISPLAY is available
        if os.environ.get("DISPLAY"):
            run_gui_mode(cfg, kill_switch, start_gateway=args.gateway, start_telegram=args.telegram)
        else:
            run_cli_mode(cfg, kill_switch, args.prompt, args.steps, start_gateway=args.gateway, start_telegram=args.telegram)


if __name__ == "__main__":
    cli_entrypoint()
