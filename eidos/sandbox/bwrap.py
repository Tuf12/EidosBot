"""Kernel-level filesystem sandboxing engine using Linux Bubblewrap (bwrap)."""

import logging
import os
import shutil
import subprocess
from typing import List, Optional

logger = logging.getLogger(__name__)


class BubblewrapSandbox:
    """Provides unprivileged filesystem isolation for shell commands using Bubblewrap.

    Features:
    - Root filesystem (/) and user home (~) are mounted read-only (--ro-bind).
    - Fresh scratch space created in RAM tmpfs (/tmp).
    - Designates writeable workspace directory (--bind) where files can be freely modified.
    - Optional network isolation (--unshare-net).
    - GPU device passthrough (/dev/dri, /dev/nvidia*) for local compute when permitted.
    """

    def __init__(
        self,
        workspace_dir: str = "~/EidosWorkspace",
        allow_network: bool = True,
        extra_writable: Optional[List[str]] = None,
        allow_dev_gpu: bool = True,
        bwrap_bin: Optional[str] = None,
    ):
        self.workspace_dir = os.path.abspath(os.path.expanduser(workspace_dir))
        self.allow_network = allow_network
        self.allow_dev_gpu = allow_dev_gpu
        self.bwrap_bin = bwrap_bin or shutil.which("bwrap") or "/usr/bin/bwrap"

        # Resolve and store extra writable directories
        self.extra_writable: List[str] = []
        if extra_writable:
            for p in extra_writable:
                resolved = os.path.abspath(os.path.expanduser(p))
                self.extra_writable.append(resolved)

        # Ensure designated workspace exists
        os.makedirs(self.workspace_dir, exist_ok=True)
        for p in self.extra_writable:
            try:
                os.makedirs(p, exist_ok=True)
            except Exception as e:
                logger.debug("Could not create extra writable path %s: %s", p, e)

    @classmethod
    def is_available(cls, bwrap_bin: Optional[str] = None) -> bool:
        """Check if bubblewrap (bwrap) executable is installed and runnable in current environment."""
        path = bwrap_bin or shutil.which("bwrap")
        if not path or not os.path.exists(path) or not os.access(path, os.X_OK):
            return False
        # Verify kernel user namespaces are permitted (e.g. not blocked inside containers/sandboxes)
        try:
            res = subprocess.run([path, "--ro-bind", "/", "/", "true"], capture_output=True, timeout=2)
            return res.returncode == 0
        except Exception:
            return False

    def build_command(self, user_command: str, cwd: Optional[str] = None) -> List[str]:
        """Construct the bwrap execution argument vector."""
        if not self.is_available(self.bwrap_bin):
            raise RuntimeError(f"Bubblewrap executable not found or not executable: {self.bwrap_bin}")

        args: List[str] = [
            self.bwrap_bin,
            "--ro-bind", "/", "/",               # System root mounted read-only
            "--dev", "/dev",                     # Minimal safe device nodes
        ]

        # Bind GPU device nodes if available and permitted
        if self.allow_dev_gpu:
            if os.path.exists("/dev/dri"):
                args.extend(["--dev-bind", "/dev/dri", "/dev/dri"])
            for dev_node in [
                "/dev/nvidia0",
                "/dev/nvidiactl",
                "/dev/nvidia-uvm",
                "/dev/nvidia-uvm-tools",
                "/dev/nvidia-modeset",
            ]:
                if os.path.exists(dev_node):
                    args.extend(["--dev-bind", dev_node, dev_node])

        args.extend([
            "--proc", "/proc",                   # Process info
            "--tmpfs", "/tmp",                   # Fresh scratch space
            "--bind", self.workspace_dir, self.workspace_dir,  # Read-write workspace
        ])

        # Bind extra designated writeable paths
        for path in self.extra_writable:
            if os.path.exists(path):
                args.extend(["--bind", path, path])

        # Network isolation if disabled
        if not self.allow_network:
            args.append("--unshare-net")

        # Set execution directory
        target_dir = self.workspace_dir
        if cwd:
            expanded_cwd = os.path.abspath(os.path.expanduser(cwd))
            if os.path.exists(expanded_cwd):
                target_dir = expanded_cwd

        args.extend(["--chdir", target_dir])
        args.extend(["--", "/bin/bash", "-c", user_command])
        return args

    def run(
        self,
        user_command: str,
        cwd: Optional[str] = None,
        timeout: float = 30.0,
    ) -> subprocess.CompletedProcess:
        """Execute a user command within the bubblewrap sandbox."""
        cmd = self.build_command(user_command, cwd=cwd)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
