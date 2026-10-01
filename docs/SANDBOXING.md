# EidosBot Sandboxing Architecture & Filesystem Isolation

This document defines the technical architecture, security model, and implementation details for **EidosBot's Sandboxed Execution Engine** using Linux Bubblewrap (`bwrap`).

---

## 1. Motivation: Eliminating Prompt Fatigue while Maximizing Safety

### 1.1. The Flaws of Purely Regex-Based Command Filtering
In early versions of EidosBot, safety relied primarily on regex pattern matching in `eidos/safety/risk_classifier.py` and user approval dialogs:
- **Confirmation Fatigue:** Asking the user to manually approve every `mkdir`, `python3`, `curl`, and `find` command disrupts autonomous workflows and causes users to blindly approve prompts without reading them.
- **Subprocess Blindspots:** A regex parser inspecting `python3 process_batch.py` sees a standard Level 2 command. However, if that script contains a buggy wildcard file deletion (`shutil.rmtree()` or `os.remove()`), the regex gatekeeper cannot see inside the running script.

### 1.2. The Sandboxing Solution
By isolating shell commands inside a **kernel-enforced filesystem sandbox**, we achieve **structural security by default**:
1. **Physical Impossibility of Destruction:** The entire root filesystem (`/`), system directories (`/etc`, `/usr`, `/bin`), and the user's home folder (`~`) are mounted **strictly read-only**.
2. **Elimination of Per-Command Allowlist Prompts:** Within the sandboxed workspace, EidosBot can freely create, modify, compile, and run scripts without interrupting the user, because even a rogue `rm -rf /` will instantly fail with `Read-only file system` at the Linux kernel level.
3. **Targeted Confirmation:** Only commands requiring **un-sandboxed host access** (e.g., system package installs or global desktop changes) trigger confirmation dialogs.

---

## 2. Linux Bubblewrap (`bwrap`) Architecture

EidosBot leverages **Bubblewrap (`bwrap`)**, the unprivileged containerization technology built into modern Linux distributions (including Linux Mint, Ubuntu, and Debian), originally developed for Flatpak.

### 2.1. Why Bubblewrap?
- **No Root or Sudo Required:** Runs entirely unprivileged using user namespaces (`CLONE_NEWUSER`).
- **Zero Overhead:** Unlike Docker or full virtual machines, `bwrap` launches instantaneously (< 5ms startup overhead) and shares the host kernel directly.
- **Pre-installed Baseline:** Bubblewrap is already installed out of the box on Linux Mint as part of standard desktop packaging.

### 2.2. Sandbox Filesystem Mapping

```
┌────────────────────────────────────────────────────────────────────────┐
│                        EIDOS SANDBOX BOUNDARY                          │
├─────────────────────────┬─────────────────┬────────────────────────────┤
│ Host Path               │ Sandbox Mount   │ Permission                 │
├─────────────────────────┼─────────────────┼────────────────────────────┤
│ /                       │ /               │ 🔒 READ-ONLY (--ro-bind)   │
│ /usr, /bin, /lib, /lib64│ (system paths)  │ 🔒 READ-ONLY (--ro-bind)   │
│ /etc                    │ /etc            │ 🔒 READ-ONLY (--ro-bind)   │
│ /home/user              │ /home/user      │ 🔒 READ-ONLY (--ro-bind)   │
│ /tmp                    │ /tmp            │ ⚡ RAM TMPFS (--tmpfs)     │
│ /dev                    │ /dev            │ 🛡️ SAFE DEV (--dev)       │
│ /proc                   │ /proc           │ 🛡️ SAFE PROC (--proc)      │
│ ~/EidosWorkspace        │ ~/EidosWorkspace│ ✏️ READ-WRITE (--bind)     │
│ (Active Job Folder)     │ (Target Folder) │ ✏️ READ-WRITE (--bind)     │
└─────────────────────────┴─────────────────┴────────────────────────────┘
```

### 2.3. Kernel-Enforced Behavioral Example

If an autonomous script or command runs inside the sandbox:

```bash
# Attempting destructive actions inside the sandbox:
rm -rf /etc/hosts
# Output: rm: cannot remove '/etc/hosts': Read-only file system

rm -rf ~/Pictures
# Output: rm: cannot remove '/home/tuf/Pictures': Read-only file system

# Permitted actions within designated workspace:
echo "Portfolio for Item #1" > ~/EidosWorkspace/inventions/item_1.md
# Output: Success!
```

---

## 3. The Dual-Mode Execution Model

EidosBot classifies shell execution into two distinct execution modes:

```
                               ┌────────────────────────────────┐
                               │     PROPOSED SHELL COMMAND     │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                              /─────────────────────────────────\
                             <  Requires Host Access or Root?    >
                              \─────────────────────────────────/
                                       │                │
                                      YES               NO
                                       │                │
                                       ▼                ▼
                         ┌───────────────────┐    ┌─────────────────────────┐
                         │   HOST PRIVILEGE  │    │     SANDBOXED MODE      │
                         │       MODE        │    │      (Bubblewrap)       │
                         ├───────────────────┤    ├─────────────────────────┤
                         │ • Level 2/3 Gate  │    │ • Root / Home is R/O    │
                         │ • Confirmation UI │    │ • Workspace is R/W      │
                         │ • Audit logged    │    │ • AUTO-ALLOWED          │
                         │ • Kill switch active│   │ • No prompt fatigue     │
                         └───────────────────┘    └─────────────────────────┘
```

### 3.1. Mode A: Sandboxed Mode (Default for Autonomous Work)
- **Used For:** Data processing, running user Python scripts, compiling code, batch job processing (e.g. 300 Inventions evaluation), downloading web artifacts, and file parsing.
- **Safety Policy:** **Auto-Allowed.** No popup dialog or allowlist lookup required because the kernel prevents any damage outside the designated project folder.
- **Timeout & Kill Switch:** Standard timeout (e.g. 30s) and global hardware kill switch (`Ctrl+Alt+Esc`) remain fully operational.

### 3.2. Mode B: Host / Privileged Mode (Exceptions Only)
- **Used For:** Commands that explicitly require global system changes (e.g. `apt install`, modifying system configuration, mounting external drives, or replacing files outside the sandbox after user review).
- **Safety Policy:** **Strict Confirmation Required.** Passes through `Gatekeeper` and presents the PyQt6 confirmation dialog (or Telegram approval if remote).

---

## 4. Working with Host Files: Staging vs. Direct Mount Workflows

A common requirement is having EidosBot work on existing files on your computer without risking accidental corruption. EidosBot supports two elegant workflows:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        EXTERNAL FILE INTERACTION WORKFLOWS                             │
├────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                        │
│  WORKFLOW 1: STAGING & USER-APPROVED PROMOTION (Safest)                                │
│                                                                                        │
│   [ Host File (Read-Only) ] ───▶ [ Copy to Sandbox: ~/EidosWorkspace/staging/ ]        │
│   (e.g., ~/Documents/notes.txt)        │                                               │
│                                        ▼                                               │
│                                  [ Edit & Test in Sandbox ]                            │
│                                  (Freely iterate without risk)                         │
│                                        │                                               │
│                                        ▼                                               │
│                                  [ Present Diff to User ]                              │
│                                  (HUD Popup / Telegram Card)                           │
│                                        │                                               │
│                         ┌──────────────┴──────────────┐                                │
│                         ▼                             ▼                                │
│                   [ Approved ]                   [ Rejected ]                          │
│                         │                             │                                │
│                         ▼                             ▼                                │
│               [ Backup original to .bak ]       [ Discard Sandbox ]                    │
│               [ Overwrite original file ]                                              │
│                                                                                        │
├────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                        │
│  WORKFLOW 2: DIRECT WORKSPACE BINDING (For Dedicated Project Folders)                  │
│                                                                                        │
│   User designates folder: "Work on ~/Projects/MyWebsite"                               │
│        │                                                                               │
│        ▼                                                                               │
│   bwrap --ro-bind / / --bind ~/Projects/MyWebsite ~/Projects/MyWebsite                 │
│        │                                                                               │
│        ▼                                                                               │
│   EidosBot directly reads & writes files inside ~/Projects/MyWebsite in-place.         │
│   The REST of the computer (system files, home folder, photos) remains 100% read-only. │
│                                                                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### 4.1. Workflow 1: The Staging & Promotion Pattern (Copy $\rightarrow$ Edit $\rightarrow$ Diff $\rightarrow$ Replace)
When working on sensitive single files outside the sandbox:
1. **Read & Copy In:** Because the entire filesystem is mounted read-only in the sandbox, EidosBot can read any file. It copies `~/Documents/budget.csv` into `~/EidosWorkspace/staging/budget.csv`.
2. **Autonomous Modification:** EidosBot modifies, formats, or processes the file freely inside the sandbox.
3. **Diff Presentation:** EidosBot uses `git diff` or Python's `difflib` to present a concise change preview to you:
   > *"I've refactored `budget.csv`. 14 rows cleaned, 2 formula columns added. Would you like me to replace the original file?"*
4. **User-Approved Promotion:**
   - If approved, a single Level 2 host command executes:
     `cp ~/Documents/budget.csv ~/Documents/budget.csv.bak && cp ~/EidosWorkspace/staging/budget.csv ~/Documents/budget.csv`
   - An automatic `.bak` copy is preserved for safety.
   - If rejected, the staging directory is discarded and your original file was never touched.

### 4.2. Workflow 2: Direct Workspace Mount (`extra_writable`)
When you are working on an entire active project directory (such as a coding repo or a folder of 300 invention files):
- You can designate `~/Documents/Inventions` as an extra writeable path for that session or batch job.
- The `bwrap` container binds that folder as read-write (`--bind`), allowing EidosBot to create and edit files directly in place.
- **The rest of your computer remains kernel-protected:** EidosBot still cannot delete or write to `/etc`, `~/.bashrc`, `~/Pictures`, or any other directory outside `~/Documents/Inventions`.

---

## 5. Configuration Specification (`config.yaml`)

Sandboxing settings are defined under the `sandbox` block in `config.yaml`:

```yaml
sandbox:
  enabled: true
  engine: "bwrap"                       # "bwrap" on Linux; "none" fallback
  default_workspace: "~/EidosWorkspace" # Default writeable directory
  
  # Paths that are mounted read-write inside the sandbox
  writable_paths:
    - "~/EidosWorkspace"
    - "~/Documents/Inventions"          # Specific job directories can be added

  # Security & Resource limits
  network_access: true                  # Set to false for strictly offline tasks
  max_memory_mb: 4096                   # Memory limit via cgroups / ulimit
  max_execution_time_sec: 60.0          # Process timeout
  allow_dev_gpu: true                   # Expose /dev/dri and NVIDIA devices for local compute
```

---

## 6. Technical Implementation (`eidos/sandbox/`)

### 6.1. Command Builder (`bwrap_runner.py`)
The Python sandbox wrapper dynamically constructs the `bwrap` command arguments:

```python
import os
import subprocess
from typing import List, Optional

class BubblewrapSandbox:
    def __init__(self, workspace_dir: str, allow_network: bool = True, extra_writable: Optional[List[str]] = None):
        self.workspace_dir = os.path.abspath(os.path.expanduser(workspace_dir))
        self.allow_network = allow_network
        self.extra_writable = [os.path.abspath(os.path.expanduser(p)) for p in (extra_writable or [])]
        os.makedirs(self.workspace_dir, exist_ok=True)

    def build_command(self, user_command: str) -> List[str]:
        args = [
            "bwrap",
            "--ro-bind", "/", "/",               # Entire root is read-only
            "--dev", "/dev",                     # Minimal safe device nodes
            "--proc", "/proc",                   # Process info
            "--tmpfs", "/tmp",                   # Fresh scratch space
            "--bind", self.workspace_dir, self.workspace_dir, # Writeable workspace
        ]

        # Bind extra writeable directories for active jobs
        for path in self.extra_writable:
            if os.path.exists(path):
                args.extend(["--bind", path, path])

        if not self.allow_network:
            args.append("--unshare-net")        # Disconnect network if offline mode

        args.extend(["--chdir", self.workspace_dir])
        args.extend(["--", "/bin/bash", "-c", user_command])
        return args

    def run(self, user_command: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
        cmd = self.build_command(user_command)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
```

### 6.2. Integration with `ShellExecutor` (`eidos/actions/shell.py`)
`ShellExecutor` is updated to check if `sandbox.enabled` is active:
- If `sandboxed=True` (default), the command is dispatched directly through `BubblewrapSandbox.run()`.
- The Gatekeeper logs this execution as `auto_allowed (sandboxed)`.
- If the command requires host execution, it reverts to the standard Gatekeeper prompt flow.

---

## 7. Summary of Benefits

| Challenge | Before (Allowlist & Popups Only) | After (Kernel Sandboxing) |
|---|---|---|
| **Autonomous Jobs** | Stalls every few steps waiting for user to click "Approve" | Runs completely unattended inside the workspace directory |
| **Prompt Fatigue** | User gets tired of clicking "Yes" and starts approving blindly | Zero prompts for workspace tasks; prompts only occur for real host actions |
| **Script Vulnerabilities** | `python3 script.py` could delete files via internal Python calls | Kernel physically rejects writes outside the workspace, even from scripts |
| **Setup Complexity** | Heavy Docker containers or virtual machines required | Uses pre-installed `bwrap` on Linux Mint with zero background daemon overhead |
