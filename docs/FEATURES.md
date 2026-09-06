# EidosBot Feature Specifications

This document outlines the functional features, security controls, user interface components, and integration specifications for **EidosBot**.

---

## 1. Safety & Confirmation Engine

The safety system is the central pillar of EidosBot. It guarantees user sovereignty over system execution, preventing accidental damage while allowing seamless productivity.

### 1.1. Safety Classification Matrix

| Level | Classification | Examples | Default Behavior | Can Bypass / Remember? |
|---|---|---|---|---|
| **Level 0** | **Passive / Read-Only** | Screen capture, active window title/class, clipboard read, listing directory contents (`ls`, `pwd`), checking system stats | **Auto-Allowed** | Always allowed |
| **Level 1** | **UI Navigation** | Mouse move, left/right click, double click, scroll, typing text, application hotkeys (`Ctrl+C`, `Ctrl+V`) | **Configurable** (default: Auto-allowed with visual HUD reticle) | Yes (Global toggle in Settings) |
| **Level 2** | **Shell & State Modification** | Shell execution (`mkdir`, `git status`, `curl`, `python3 script.py`, editing files, launching applications) | **Strict Confirmation Required** | **Yes (Per-command allowlist with checkbox)** |
| **Level 3** | **Destructive / High-Risk** | `rm`, `rmdir`, `dd`, `mkfs`, `shutdown`, `reboot`, editing `/etc/*`, `chmod -R`, `chown`, killing system PIDs | **Hard-Locked Confirmation** | **NO. Never bypassable.** |

---

### 1.2. Level 2 Shell Confirmation & Dynamic Allowlist System

To balance high security with workflow convenience, Level 2 commands start with strict confirmation on every command, with a built-in mechanism to remember commands you trust.

#### The Approval Flow
1. The agent decides it needs to run a shell command: e.g., `git status`.
2. The agent pauses the loop and prompts the user via the HUD Confirmation Dialog:
   ```text
   ┌────────────────────────────────────────────────────────┐
   │ ⚠️  EIDOSBOT SHELL COMMAND REQUEST                      │
   │                                                        │
   │ Command:  git status                                   │
   │ Directory: /home/tuf/CursorProjects/EidosBot           │
   │ Risk:     Level 2 (Shell Execution)                    │
   │                                                        │
   │ [X] Remember and allow this command in the future      │
   │                                                        │
   │     [ Approve (Enter) ]       [ Reject (Esc) ]         │
   └────────────────────────────────────────────────────────┘
   ```
3. If the user clicks **[Approve]** without checking the box:
   - The command runs once. Future invocations will still prompt for confirmation.
4. If the user checks **`[X] Remember and allow this command in the future`**:
   - The command (or normalized command pattern) is added to `~/.eidos/allowlist.json`.
   - Subsequent executions of this exact command run automatically without interrupting the user.

#### Dynamic Allowlist Management GUI
If the user accidentally whitelisted a command, or wants to tighten permissions later, they can open the **Command Allowlist Manager** from the GUI floating pill:

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ 🛡️  EidosBot - Shell Command Allowlist Manager                                │
├──────────────────────────────────────────────────────────────────────────────┤
│ Search: [ git                                          ]                     │
├───────────────────────┬──────────────┬─────────────┬───────────┬─────────────┤
│ Command               │ Added On     │ Last Used   │ Run Count │ Action      │
├───────────────────────┼──────────────┼─────────────┼───────────┼─────────────┤
│ git status            │ 2026-09-06   │ 10 mins ago │ 14        │ [ Revoke ]  │
│ git diff              │ 2026-09-06   │ 2 mins ago  │ 6         │ [ Revoke ]  │
│ npm run build         │ 2026-09-05   │ Yesterday   │ 3         │ [ Revoke ]  │
│ python3 -m pytest     │ 2026-09-04   │ 2 days ago  │ 22        │ [ Revoke ]  │
├───────────────────────┴──────────────┴─────────────┴───────────┴─────────────┤
│ [ + Add Custom Command ]                                   [ Close (Esc) ]   │
└──────────────────────────────────────────────────────────────────────────────┘
```

- **Instant Revocation:** Clicking **`[ Revoke ]`** instantly removes the command from the allowlist. The very next time EidosBot attempts that command, it will pause and ask for confirmation again.
- **Audit Logging:** Every approval, revocation, and execution is appended to `~/.eidos/audit.log`.

---

### 1.3. Level 3 Destructive Action Rules

Commands identified as Level 3 trigger a prominent red warning dialog and **cannot** be saved to the allowlist:
- `rm` / `unlink` / `shred` (any deletion)
- Disk formatting tools (`dd`, `mkfs`, `fdisk`, `parted`)
- System power commands (`shutdown`, `reboot`, `systemctl stop/disable`)
- Root privileges (`sudo`, `su`, `doas`)
- Mass file attribute changes (`chmod -R`, `chown -R`)
- Killing non-child processes (`kill -9`, `pkill`, `killall`)

The dialog displays a distinct red banner:
> **"DANGER: Destructive Action Detected. This action cannot be added to the allowlist and requires explicit one-time authorization."**

---

### 1.4. Global Hardware Kill Switch (`Ctrl + Alt + Esc`)

- A lightweight background thread constantly listens for the key combination `Ctrl + Alt + Esc` using `pynput.keyboard.GlobalHotKeys`.
- When pressed:
  1. A thread-safe `Event` (`stop_requested`) is set immediately.
  2. Any active child process launched by EidosBot receives `SIGKILL`.
  3. Mouse and keyboard input queues are cleared, and held keys are released.
  4. The PyQt6 HUD border flashes red and the status pill switches to: `🛑 [EMERGENCY STOPPED]`.
  5. The loop enters an idle state until explicitly reset by the user.

---

## 2. PyQt6 Visual HUD & User Interface

The UI is engineered to be lightweight, hardware-accelerated, and completely transparent so it never gets in the way of desktop work.

### 2.1. Transparent Overlay Canvas (`HUDOverlay`)
- **Window Attributes:**
  - Borderless, fullscreen (`x=0, y=0, width=ScreenW, height=ScreenH`).
  - Transparent background (`WA_TranslucentBackground`).
  - Stays on top of all windows (`WindowStaysOnTopHint`).
  - Mouse event passthrough (`WindowTransparentForInput`): Allows clicks to pass directly through to whatever desktop application is underneath.
- **Visual Reticles & Grounding Feedback:**
  - **Click Target:** Renders an animated pulse/circle around the target `(x, y)` coordinate for 250ms prior to clicking.
  - **Set-of-Mark Overlays:** When grounding, displays translucent colored bounding boxes with numeric badges `#1, #2, #3...` over interactive UI elements.
  - **Active Region Highlight:** Highlights the current window or bounding box being operated on.

### 2.2. Floating Status Pill (`StatusPill`)
A small, draggable widget (placed by default in the top-right corner of the primary screen):
- **Visual Status Indicator:**
  - 🟢 **Idle / Ready:** Waiting for user command or voice prompt.
  - 🟡 **Thinking:** LLM is reasoning (shows which model: `Ollama: gemma4-26b` or `Cloud: Qwen 2.5-VL`).
  - 🔵 **Perceiving:** Capturing screen or querying window tree.
  - 🟠 **Awaiting Approval:** Shell command pending user confirmation.
  - 🟣 **Executing:** Typing, clicking, or running process.
  - 🔴 **Killed / Error:** Stopped via `Ctrl+Alt+Esc` or encountered an exception.
- **Controls on the Pill:**
  - **[ ⏸ Pause / ▶ Resume ]** button.
  - **[ 🛑 Kill ]** button (GUI equivalent of `Ctrl+Alt+Esc`).
  - **[ 🛡️ Allowlist ]** button (opens the Command Allowlist Manager).
  - **[ ⚙️ Settings ]** button.

---

## 3. Hybrid 3-Tier Execution Engine

Rather than relying purely on pixels, EidosBot automatically selects the most efficient execution tier:

```
                    ┌───────────────────────────────┐
                    │       INCOMING TASK GOAL      │
                    └──────────────┬────────────────┘
                                   │
           ┌───────────────────────┼───────────────────────┐
           ▼                       ▼                       ▼
  [Can execute via CLI?]   [Target is OptimalX/CDP?]  [Arbitrary Desktop App]
           │                       │                       │
           ▼                       ▼                       ▼
    TIER 1 (SHELL)          TIER 2 (DOM/A11Y)       TIER 3 (VISION/GUI)
 • bash / subprocess     • Chrome DevTools Protocol • Screenshot (mss)
 • Instant execution     • QuerySelector / Eval     • Set-of-Mark Grounding
 • Zero pixel ambiguity  • Exact element bounds     • Mouse/Keyboard inject
```

1. **Tier 1 (CLI & APIs):**
   - File manipulation, network requests, system monitoring, text processing, package commands.
   - Run through `subprocess` with timeout and audit logging.
2. **Tier 2 (Accessibility & DOM Tree):**
   - Directly inspects internal DOM nodes of Electron apps (OptimalX Desktop) and Chromium browsers via WebSocket CDP.
   - Queries Linux AT-SPI for standard GTK/Qt desktop widgets.
   - Clicks are triggered either by exact bounding coordinates or native DOM events (`element.click()`).
3. **Tier 3 (Visual Grounding & Synthetic Input):**
   - For applications with no API or accessibility tree.
   - Captures screen using `mss` (X11 Shared Memory).
   - Generates candidate bounding boxes or visual grid.
   - LLM predicts coordinates.
   - `pynput` / `pyautogui` injects hardware mouse and keyboard events.

---

## 4. Application Bridges

### 4.1. OptimalX Desktop 1.0 (Electron App) Bridge
Because you developed OptimalX Desktop, EidosBot provides first-class native control:
- **CDP Bridge:** OptimalX is started or attached with `--remote-debugging-port=9222`.
- **Target Discovery:** EidosBot queries `http://127.0.0.1:9222/json` to locate the OptimalX webview / window target.
- **Capabilities:**
  - `find_element(selector)`: Locates buttons, tables, cards, and inputs with 100% precision.
  - `click_element(selector)`: Triggers synthetic or native click.
  - `read_state()`: Extracts data directly from the DOM or state store without OCR.
  - `inject_script(js)`: Executes automation scripts directly inside the renderer process.

### 4.2. Cursor IDE Bridge
- **CLI Navigation:** `cursor -g /path/to/project/file.py:line:col` for jumping directly to code.
- **Workspace Management:** Opens project workspaces deterministically.
- **Command Palette Automation:** Emits `Ctrl+Shift+P` and types target commands (e.g., `Reload Window`, `Terminal: Create New Terminal`).

### 4.3. Web Research & Browser Bridge
- Automatically inspects the active browser window.
- Can drive existing browser instances or launch headless/headful automation via Playwright/CDP.
- Reads webpage text, downloads research PDFs, extracts tables, and summarizes content locally.

---

## 5. Model Routing & Local/Cloud Orchestration

EidosBot optimizes for low latency, zero API costs, and privacy by prioritizing the local hardware:

### 5.1. Local Models (Ollama on RTX 5060 Ti 16GB VRAM)
- **Primary Reasoner / Tool Caller:** `batiai/gemma4-26b:iq4` or `mistral-small3.2:24b`
  - High reasoning capability, understands JSON tool calling, executes fast on 16GB VRAM.
- **Local Vision & Grounding:** `batiai/gemma4-12b:q6` or `gemma4:12b`
  - Evaluates screenshots, detects UI landmarks, handles simple visual queries.
- **Fast Chat / Text Extraction:** `gpt-oss:20b` or `ministral-3:14b`
  - Text summarization and prompt transformation.

### 5.2. Cloud Fallback (Chutes AI)
- **Vision Grounding Powerhouse:** `Qwen/Qwen2.5-VL-72B-Instruct`
  - Escalated when the local model cannot pinpoint a small or ambiguous UI element.
- **Complex Multi-Step Coding / Reasoning:** `deepseek-ai/DeepSeek-R1`
  - Escalated when deep mathematical or algorithmic planning is required before executing a multi-stage task.
- **Routing Rules:**
  - If screen resolution > 1440p and target element is under 20x20 pixels $\rightarrow$ Escalate visual crop to Chutes.
  - If local model hallucinates invalid tools 2 times consecutively $\rightarrow$ Escalate prompt to Chutes.
  - User can manually toggle `[Force Cloud]` or `[Local Only]` from the Status Pill.

### 5.3. Speech Audio Integration (Optional / Future Phase)
- **STT (Speech-to-Text):** Local Whisper model (`whisper.cpp` or Faster-Whisper on GPU) for push-to-talk voice commands.
- **TTS (Text-to-Speech):** Local Piper or Kokoro TTS for voice status feedback.
