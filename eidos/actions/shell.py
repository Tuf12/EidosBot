"""Safe shell command execution engine with gatekeeper integration."""

import logging
import os
import subprocess
import time
from typing import Any, Dict, Optional

from eidos.safety.gatekeeper import Gatekeeper

logger = logging.getLogger(__name__)


class ShellExecutor:
    """Executes shell commands with safety checks, timeout control, and kill switch awareness."""

    def __init__(self, gatekeeper: Optional[Gatekeeper] = None, default_timeout: float = 30.0):
        self.gatekeeper = gatekeeper or Gatekeeper()
        self.default_timeout = default_timeout

    def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Execute a shell command safely after passing through the Gatekeeper.
        """
        cmd_clean = command.strip()
        work_dir = os.path.abspath(cwd) if cwd else os.getcwd()
        timeout_val = timeout if timeout is not None else self.default_timeout

        # Step 1: Gatekeeper safety evaluation
        details = {"command": cmd_clean, "cwd": work_dir}
        allowed, reason = self.gatekeeper.evaluate("shell", details)
        if not allowed:
            logger.warning("Shell execution blocked by safety policy: %s", reason)
            return {
                "success": False,
                "denied": True,
                "error": f"Execution denied by safety gatekeeper: {reason}",
                "stdout": "",
                "stderr": "",
                "exit_code": -1,
                "duration_ms": 0.0,
                "command": cmd_clean
            }

        # Step 2: Execute command with process tracking
        start_time = time.time()
        process = None
        try:
            process = subprocess.Popen(
                cmd_clean,
                shell=True,
                cwd=work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                executable="/bin/bash"
            )

            # Poll for completion or kill switch
            poll_interval = 0.05
            elapsed = 0.0
            while elapsed < timeout_val:
                ret = process.poll()
                if ret is not None:
                    break

                # Emergency kill check
                if self.gatekeeper.kill_switch.is_triggered():
                    process.kill()
                    process.wait()
                    duration_ms = (time.time() - start_time) * 1000
                    return {
                        "success": False,
                        "denied": False,
                        "error": "Process killed: Emergency kill switch activated during execution.",
                        "stdout": "",
                        "stderr": "Aborted by kill switch",
                        "exit_code": -9,
                        "duration_ms": duration_ms,
                        "command": cmd_clean
                    }

                time.sleep(poll_interval)
                elapsed = time.time() - start_time

            if process.poll() is None:
                # Timeout reached
                process.kill()
                stdout, stderr = process.communicate()
                duration_ms = (time.time() - start_time) * 1000
                return {
                    "success": False,
                    "denied": False,
                    "error": f"Command timed out after {timeout_val} seconds.",
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": -1,
                    "duration_ms": duration_ms,
                    "command": cmd_clean
                }

            stdout, stderr = process.communicate()
            duration_ms = (time.time() - start_time) * 1000
            return {
                "success": (process.returncode == 0),
                "denied": False,
                "error": stderr.strip() if process.returncode != 0 else "",
                "stdout": stdout.strip(),
                "stderr": stderr.strip(),
                "exit_code": process.returncode,
                "duration_ms": round(duration_ms, 2),
                "command": cmd_clean
            }

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            if process and process.poll() is None:
                try:
                    process.kill()
                except Exception:
                    pass
            return {
                "success": False,
                "denied": False,
                "error": f"Subprocess exception: {str(e)}",
                "stdout": "",
                "stderr": str(e),
                "exit_code": -1,
                "duration_ms": round(duration_ms, 2),
                "command": cmd_clean
            }
