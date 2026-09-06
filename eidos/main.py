"""EidosBot main entrypoint and interactive CLI co-pilot."""

import argparse
import logging
import os
from pathlib import Path
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
    print("  🤖  EIDOSBOT - OS-Level Desktop Co-Pilot (Phase 1 CLI MVP)  🤖")
    print("  Local Model: Ollama | Hardware: RTX 5060 Ti | OS: Linux Mint")
    print("  🛑 Global Kill Switch: Press [ Ctrl + Alt + Esc ] to halt")
    print("=" * 65)


def cli_entrypoint():
    """Entrypoint for terminal execution."""
    parser = argparse.ArgumentParser(description="EidosBot OS-Level Autonomous Desktop Agent")
    parser.add_argument("prompt", nargs="*", help="Goal or prompt for EidosBot (optional)")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--steps", type=int, default=15, help="Maximum cycle steps per task")
    args = parser.parse_args()

    cfg = load_config(args.config)
    print_banner()

    # Initialize Global Kill Switch
    hotkey = cfg.get("safety", {}).get("kill_switch_hotkey", "<ctrl>+<alt>+<esc>")
    kill_switch = KillSwitch(hotkey_str=hotkey)
    kill_switch.start()

    # Initialize Agent Loop
    agent = AgentLoop(config=cfg, kill_switch=kill_switch)

    try:
        # One-shot task mode
        if args.prompt:
            task = " ".join(args.prompt)
            print(f"\n🎯 [Task]: {task}\n")
            result = agent.run_task(task, max_steps=args.steps)
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

            result = agent.run_task(user_input, max_steps=args.steps)
            print("\n" + "-" * 50)
            if result["success"]:
                print(f"✅ [Completed in {result['steps_taken']} steps]:")
                print(result["summary"])
            else:
                print(f"❌ [Result ({result['steps_taken']} steps)]:")
                print(result["summary"])

    finally:
        kill_switch.stop()


if __name__ == "__main__":
    cli_entrypoint()
