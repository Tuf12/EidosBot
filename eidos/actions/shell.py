"""Safe shell command execution engine with gatekeeper integration and kernel sandboxing."""

import logging
import os
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional

from eidos.safety.gatekeeper import Gatekeeper
from eidos.safety.risk_classifier import RiskClassifier, RiskLevel
from eidos.sandbox.bwrap import BubblewrapSandbox
from eidos.sandbox.shell_mode import ShellModeRequest, resolve_shell_sandboxed

logger = logging.getLogger(__name__)


class ShellExecutor:
    """Executes shell commands with safety checks, timeout control, and kill switch awareness."""

    def __init__(
        self,
        gatekeeper: Optional[Gatekeeper] = None,
        default_timeout: float = 30.0,
        sandbox: Optional[BubblewrapSandbox] = None,
        sandbox_config: Optional[Dict[str, Any]] = None,
    ):
        self.gatekeeper = gatekeeper or Gatekeeper()
        self.default_timeout = default_timeout
        self.stop_checker: Optional[Callable[[], bool]] = None
        self.sandbox: Optional[BubblewrapSandbox] = None
        self.sandbox_enabled: bool = False
        self._init_sandbox(sandbox, sandbox_config)

    def _init_sandbox(
        self,
        sandbox: Optional[BubblewrapSandbox] = None,
        sandbox_config: Optional[Dict[str, Any]] = None,
    ):
        if sandbox is not None:
            self.sandbox = sandbox
            self.sandbox_enabled = True
            self.default_workspace = sandbox.workspace_dir
            self.default_shell_mode = "auto"
            return

        cfg = sandbox_config or {}
        self.default_workspace = os.path.abspath(
            os.path.expanduser(cfg.get("default_workspace", "~/EidosWorkspace"))
        )
        self.default_shell_mode = str(cfg.get("default_shell_mode", "auto"))
        self.sandbox_enabled = bool(cfg.get("enabled", False))
        if self.sandbox_enabled:
            if BubblewrapSandbox.is_available():
                self.sandbox = BubblewrapSandbox(
                    workspace_dir=cfg.get("default_workspace", "~/EidosWorkspace"),
                    allow_network=cfg.get("network_access", True),
                    extra_writable=cfg.get("writable_paths"),
                    allow_dev_gpu=cfg.get("allow_dev_gpu", True),
                )
            else:
                logger.warning("Sandbox enabled in configuration but bwrap is not available. Disabling sandbox.")
                self.sandbox = None
                self.sandbox_enabled = False
        else:
            self.sandbox = None

    def configure_sandbox(self, sandbox_config: Dict[str, Any]):
        """Dynamically update sandbox configuration."""
        self._init_sandbox(None, sandbox_config)

    def writable_roots(self) -> List[str]:
        """Directories that are read-write inside the bwrap nest."""
        roots: List[str] = []
        if self.sandbox is not None:
            roots.append(self.sandbox.workspace_dir)
            roots.extend(self.sandbox.extra_writable)
        elif hasattr(self, "default_workspace"):
            roots.append(self.default_workspace)
        # Deduplicate while preserving order
        seen = set()
        unique: List[str] = []
        for p in roots:
            absp = os.path.abspath(os.path.expanduser(p))
            if absp not in seen:
                seen.add(absp)
                unique.append(absp)
        return unique

    def resolve_sandboxed(
        self,
        requested: ShellModeRequest,
        command: str,
        cwd: Optional[str],
    ) -> bool:
        """Map tool argument + config policy to a concrete bwrap vs host decision."""
        return resolve_shell_sandboxed(
            requested,
            command=command,
            cwd=cwd,
            writable_roots=self.writable_roots(),
            sandbox_enabled=self.sandbox_enabled and self.sandbox is not None,
            default_shell_mode=getattr(self, "default_shell_mode", "auto"),
        )

    @staticmethod
    def _is_background_command(command: str) -> bool:
        """Return True when a shell command intentionally detaches into the background."""
        stripped = command.strip()
        if not stripped.endswith("&"):
            return False
        if stripped.endswith("&&") or stripped.endswith("&>"):
            return False
        return True

    def _stop_requested(self) -> bool:
        """Return True when the user asked the running task to stop."""
        checker = self.stop_checker
        if checker is None:
            return False
        try:
            return bool(checker())
        except Exception:
            return False

    def _killed_process_result(
        self,
        *,
        command: str,
        start_time: float,
        sandboxed: bool,
        by_user: bool,
    ) -> Dict[str, Any]:
        duration_ms = (time.time() - start_time) * 1000
        if by_user:
            error = "Process killed: task stopped by user."
            stderr = "Aborted by user stop"
        else:
            error = "Process killed: Emergency kill switch activated during execution."
            stderr = "Aborted by kill switch"
        return {
            "success": False,
            "denied": False,
            "error": error,
            "stdout": "",
            "stderr": stderr,
            "exit_code": -9,
            "duration_ms": duration_ms,
            "command": command,
            "sandboxed": sandboxed,
        }

    def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
        sandboxed: bool = False,
    ) -> Dict[str, Any]:
        """
        Execute a shell command safely.

        If sandboxed=True and sandbox is enabled in configuration, execution
        is isolated via Bubblewrap (root and home read-only, workspace read-write),
        auto-allowed without prompt fatigue, and logged to audit log.
        Otherwise, execution requires Gatekeeper policy evaluation.
        """
        cmd_clean = command.strip()
        timeout_val = timeout if timeout is not None else self.default_timeout

        use_sandbox = sandboxed and self.sandbox_enabled and (self.sandbox is not None)

        if use_sandbox:
            work_dir = os.path.abspath(cwd) if cwd else self.sandbox.workspace_dir

            # Step 1: Kill switch check
            if self.gatekeeper.kill_switch.is_triggered():
                self.gatekeeper.audit.log(
                    "shell",
                    {"command": cmd_clean, "cwd": work_dir, "sandboxed": True},
                    3,
                    "killed",
                    "Kill switch is active",
                )
                return {
                    "success": False,
                    "denied": False,
                    "error": "Action aborted: Global kill switch is active.",
                    "stdout": "",
                    "stderr": "Aborted by kill switch",
                    "exit_code": -9,
                    "duration_ms": 0.0,
                    "command": cmd_clean,
                    "sandboxed": True,
                }

            shell_risk, shell_risk_reason = RiskClassifier.classify_shell_command(cmd_clean)
            if shell_risk == RiskLevel.LEVEL_3_DESTRUCTIVE:
                allowed, deny_reason = self.gatekeeper.evaluate(
                    "shell",
                    {"command": cmd_clean, "cwd": work_dir, "sandboxed": True},
                )
                if not allowed:
                    return {
                        "success": False,
                        "denied": True,
                        "error": f"Execution denied by safety gatekeeper: {deny_reason}",
                        "stdout": "",
                        "stderr": "",
                        "exit_code": -1,
                        "duration_ms": 0.0,
                        "command": cmd_clean,
                        "sandboxed": True,
                    }

            # Step 2: Auto-allow and log to audit logger
            self.gatekeeper.audit.log(
                "shell",
                {"command": cmd_clean, "cwd": work_dir, "sandboxed": True},
                0,
                "auto_allowed",
                "Kernel sandboxed workspace execution (bwrap)",
            )

            start_time = time.time()
            try:
                bwrap_cmd = self.sandbox.build_command(cmd_clean, cwd=work_dir)
            except Exception as e:
                return {
                    "success": False,
                    "denied": False,
                    "error": f"Failed to build sandbox command: {str(e)}",
                    "stdout": "",
                    "stderr": str(e),
                    "exit_code": -1,
                    "duration_ms": 0.0,
                    "command": cmd_clean,
                    "sandboxed": True,
                }

            if self._is_background_command(cmd_clean):
                try:
                    process = subprocess.Popen(
                        bwrap_cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    duration_ms = (time.time() - start_time) * 1000
                    return {
                        "success": True,
                        "denied": False,
                        "error": "",
                        "stdout": f"Started background process in sandbox (pid {process.pid})",
                        "stderr": "",
                        "exit_code": 0,
                        "duration_ms": round(duration_ms, 2),
                        "command": cmd_clean,
                        "background": True,
                        "pid": process.pid,
                        "sandboxed": True,
                    }
                except Exception as e:
                    duration_ms = (time.time() - start_time) * 1000
                    return {
                        "success": False,
                        "denied": False,
                        "error": f"Failed to start background process in sandbox: {str(e)}",
                        "stdout": "",
                        "stderr": str(e),
                        "exit_code": -1,
                        "duration_ms": round(duration_ms, 2),
                        "command": cmd_clean,
                        "sandboxed": True,
                    }

            process = None
            try:
                process = subprocess.Popen(
                    bwrap_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

                poll_interval = 0.05
                elapsed = 0.0
                while elapsed < timeout_val:
                    ret = process.poll()
                    if ret is not None:
                        break

                    killed = self.gatekeeper.kill_switch.is_triggered()
                    stopped = self._stop_requested()
                    if killed or stopped:
                        process.kill()
                        process.wait()
                        return self._killed_process_result(
                            command=cmd_clean,
                            start_time=start_time,
                            sandboxed=True,
                            by_user=stopped and not killed,
                        )

                    time.sleep(poll_interval)
                    elapsed = time.time() - start_time

                if process.poll() is None:
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
                        "command": cmd_clean,
                        "sandboxed": True,
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
                    "command": cmd_clean,
                    "sandboxed": True,
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
                    "command": cmd_clean,
                    "sandboxed": True,
                }

        # Step 1: Gatekeeper safety evaluation (Mode B: Host privilege mode)
        work_dir = os.path.abspath(cwd) if cwd else (self.sandbox.workspace_dir if self.sandbox else self.default_workspace)
        if not os.path.exists(work_dir):
            try:
                os.makedirs(work_dir, exist_ok=True)
            except Exception:
                work_dir = os.getcwd()
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
                "command": cmd_clean,
                "sandboxed": False,
            }

        # Step 2: Execute command with process tracking on host
        start_time = time.time()

        if self._is_background_command(cmd_clean):
            try:
                process = subprocess.Popen(
                    cmd_clean,
                    shell=True,
                    cwd=work_dir,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    executable="/bin/bash",
                )
                duration_ms = (time.time() - start_time) * 1000
                return {
                    "success": True,
                    "denied": False,
                    "error": "",
                    "stdout": f"Started background process (pid {process.pid})",
                    "stderr": "",
                    "exit_code": 0,
                    "duration_ms": round(duration_ms, 2),
                    "command": cmd_clean,
                    "background": True,
                    "pid": process.pid,
                    "sandboxed": False,
                }
            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                return {
                    "success": False,
                    "denied": False,
                    "error": f"Failed to start background process: {str(e)}",
                    "stdout": "",
                    "stderr": str(e),
                    "exit_code": -1,
                    "duration_ms": round(duration_ms, 2),
                    "command": cmd_clean,
                    "sandboxed": False,
                }

        process = None
        try:
            process = subprocess.Popen(
                cmd_clean,
                shell=True,
                cwd=work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                executable="/bin/bash",
            )

            # Poll for completion or kill switch
            poll_interval = 0.05
            elapsed = 0.0
            while elapsed < timeout_val:
                ret = process.poll()
                if ret is not None:
                    break

                killed = self.gatekeeper.kill_switch.is_triggered()
                stopped = self._stop_requested()
                if killed or stopped:
                    process.kill()
                    process.wait()
                    return self._killed_process_result(
                        command=cmd_clean,
                        start_time=start_time,
                        sandboxed=False,
                        by_user=stopped and not killed,
                    )

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
                    "command": cmd_clean,
                    "sandboxed": False,
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
                "command": cmd_clean,
                "sandboxed": False,
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
                "command": cmd_clean,
                "sandboxed": False,
            }
