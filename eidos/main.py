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


def run_gui_mode(cfg: dict, kill_switch: KillSwitch):
    """Launch EidosBot with PyQt6 Transparent HUD and Floating Status Pill."""
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    from eidos.ui.app import UIController

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setApplicationName("EidosBot")

    # Allow Python signal handling (e.g. Ctrl+C in terminal)
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    ui_controller = UIController(config=cfg, kill_switch=kill_switch)
    agent = AgentLoop(config=cfg, kill_switch=kill_switch)
    ui_controller.set_agent_loop(agent)

    ui_controller.show()
    logger.info("EidosBot PyQt6 HUD & Floating Pill active on desktop.")

    # Periodic timer so Python processes interpreter signals
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(500)

    try:
        app.exec()
    finally:
        kill_switch.stop()


def run_cli_mode(cfg: dict, kill_switch: KillSwitch, prompt_args: list, max_steps: int):
    """Run EidosBot in terminal REPL or one-shot mode."""
    agent = AgentLoop(config=cfg, kill_switch=kill_switch)

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
        kill_switch.stop()


def cli_entrypoint():
    """Main CLI and GUI launcher entrypoint."""
    parser = argparse.ArgumentParser(description="EidosBot OS-Level Autonomous Desktop Agent")
    parser.add_argument("prompt", nargs="*", help="Goal or prompt for EidosBot (optional)")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--steps", type=int, default=15, help="Maximum cycle steps per task")
    parser.add_argument("--gui", action="store_true", help="Launch PyQt6 Desktop HUD and Floating Pill")
    parser.add_argument("--cli", action="store_true", help="Force headless CLI mode")
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
        run_gui_mode(cfg, kill_switch)
    elif args.prompt or args.cli:
        run_cli_mode(cfg, kill_switch, args.prompt, args.steps)
    else:
        # Default with no arguments: Check if DISPLAY is available
        if os.environ.get("DISPLAY"):
            run_gui_mode(cfg, kill_switch)
        else:
            run_cli_mode(cfg, kill_switch, args.prompt, args.steps)


if __name__ == "__main__":
    cli_entrypoint()
