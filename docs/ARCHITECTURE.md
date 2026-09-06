# EidosBot Architecture & System Design

This document details the software architecture, subsystem components, data pipelines, and design decisions for **EidosBot**, a local-first OS-level autonomous agent for Linux Mint (X11).

---

## 1. Architectural Philosophy

1. **Local-First & Cost-Free by Default:** Standard tasks, shell commands, window navigation, and local vision run on the user's RTX 5060 Ti via Ollama without incurring API fees or transmitting private desktop data off-machine.
2. **Deterministic-First (Hybrid 3-Tier Execution):** Never guess pixels when a command-line tool, an OS API, or a direct DOM query can accomplish the task deterministically and instantaneously.
3. **Fail-Safe by Construction:** Safety is not an afterthought. Destructive operations are trapped at the lowest execution layer, a global kernel-level hotkey can kill the agent instantly, and all allowed/rejected actions are auditable.
4. **Transparent & Non-Intrusive:** The agent operates with a transparent PyQt6 overlay that visually highlights intended targets before clicking and communicates state in real time.

---

## 2. High-Level Architecture Diagram

```
                              ┌─────────────────────────────────────────┐
                              │               USER INPUT                │
                              │   (Floating HUD, CLI, or Hotkey)        │
                              └────────────────────┬────────────────────┘
                                                   │
                                                   ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                           EIDOS CORE LOOP                                              │
│                                                                                                        │
│   ┌────────────────────────┐      ┌────────────────────────┐      ┌────────────────────────────────┐   │
│   │   PERCEPTION ENGINE    │      │    COGNITIVE ROUTER    │      │       ACTION DISPATCHER        │   │
│   │                        │      │                        │      │                                │   │
│   │ • X11 Screen Capture   │────▶ │ • Ollama (Local)       │────▶ │ • Tier 1: Shell / CLI / APIs   │   │
│   │ • Window Info (xdotool)│      │   - gemma4-26b (Plan)  │      │ • Tier 2: DOM / AT-SPI Tree    │   │
│   │ • Visual Grounder / SoM│      │   - gemma4-12b (Vision)│      │ • Tier 3: Synthetic Mouse/Keys │   │
│   │ • Clipboard & SysInfo  │      │ • Chutes AI (Cloud)    │      └────────────────┬───────────────┘   │
│   └────────────────────────┘      │   - Qwen 2.5-VL 72B    │                       │                   │
│                                   │   - DeepSeek-R1        │                       │                   │
│                                   └────────────────────────┘                       │                   │
└────────────────────────────────────────────────────────────────────────────────────┼───────────────────┘
                                                                                     │
                                                                                     ▼
                                                                    ┌────────────────────────────────┐
                                                                    │       SAFETY GATEKEEPER        │
                                                                    │                                │
                                                                    │ • Global Kill Switch (Ctrl+    │
                                                                    │   Alt+Esc)                     │
                                                                    │ • Level 0: Auto-Allowed        │
                                                                    │ • Level 1: UI Action Check     │
                                                                    │ • Level 2: Shell Allowlist     │
                                                                    │ • Level 3: Hard Locked Confirm │
                                                                    └────────────────┬───────────────┘
                                                                                     │
                                            ┌────────────────────────────────────────┴───────────────────┐
                                            ▼                                                            ▼
                             ┌──────────────────────────────┐                            ┌──────────────────────────────┐
                             │       EXECUTION TARGETS      │                            │          PyQt6 HUD           │
                             │                              │                            │                              │
                             │ • Linux Mint OS / X11 System │                            │ • Transparent Overlay        │
                             │ • OptimalX Desktop (CDP DOM) │ ◀───────────────────────── │ • Click Reticle & Bounding   │
                             │ • Cursor IDE (CLI/Palette)   │      Visual Feedback       │ • Floating Status Pill       │
                             │ • Browsers & Generic Apps    │                            │ • Command Confirmation Popup │
                             └──────────────────────────────┘                            └──────────────────────────────┘
```

---

## 3. The Sense → Reason → Confirm → Act Loop

Every interaction cycle executes through a six-stage pipeline:

```
[1. SENSE]    Capture desktop screenshot via mss + query active window info (WM_CLASS, title, PID)
     │
[2. GROUND]   Identify actionable elements (Set-of-Mark markers, OCR text, or direct DOM tree nodes)
     │
[3. REASON]   Prompt local LLM (or Chutes AI) with current visual state, system state, and history
     │
[4. CONFIRM]  Pass proposed tool call through Safety Gatekeeper (verify against risk policy & allowlist)
     │        └── If approval needed: Display HUD prompt → Wait for user Enter/Esc or checkbox
     │
[5. ACT]      Dispatch action (direct shell subprocess, CDP call, or pynput mouse/keyboard injection)
     │        └── Simultaneously draw visual reticle on HUD overlay
     │
[6. VERIFY]   Sleep briefly (100–300ms) for UI rendering, re-inspect state, and check for completion/error
```

---

## 4. Subsystem Breakdown

### 4.1. Perception Subsystem (`eidos/perception/`)

- **`screen.py` (Screen Capture):**
  - Utilizes `mss` (leveraging X11 Shared Memory extension, MIT-SHM) for ultra-fast, low-overhead captures (<15ms per frame at 1080p/1440p).
  - Handles multi-monitor setups (identifies active monitor based on cursor or active window location).
  - Implements downscaling, compression, and region-of-interest (ROI) cropping to minimize prompt token usage when calling vision models.
- **`window_info.py` (X11 Window & Process Context):**
  - Uses `xdotool` and `xprop` to retrieve:
    - Active window ID and name.
    - Application `WM_CLASS` (e.g., `google-chrome`, `cursor`, `optimalx`).
    - Window bounding box `(x, y, width, height)`.
    - Process ID (PID) and resource usage.
- **`grounder.py` (Coordinate Grounding & Set-of-Mark):**
  - Translates semantic descriptions (e.g., "click the Save button") into precise `(x, y)` coordinates.
  - Supports three grounding modes:
    1. *Direct DOM / Accessibility:* Queries bounding boxes directly if inspecting OptimalX Desktop or an AT-SPI accessible application.
    2. *Set-of-Mark (SoM):* Detects interactive bounding boxes (via local lightweight detector or OCR) and overlays numbered tags, allowing the model to choose tag `#14` instead of guessing raw pixel coordinates.
    3. *Normalized Coordinate Prediction:* Normalizes coordinates to a `0–1000` grid for vision models natively trained on coordinate outputs (e.g., Qwen 2.5-VL).

### 4.2. Cognitive & Model Subsystem (`eidos/models/`)

- **`ollama_client.py`:**
  - Manages connections to local Ollama instance (`http://localhost:11434`).
  - Routes tasks based on capabilities:
    - **Reasoning & Tool Selection:** `batiai/gemma4-26b:iq4` or `mistral-small3.2:24b` / `gpt-oss:20b`.
    - **Local Vision & UI Inspection:** `batiai/gemma4-12b:q6` or `gemma4:12b`.
  - Supports standard OpenAI-compatible tool-calling schema (`tools`, `tool_choice`).
- **`chutes_client.py`:**
  - Manages high-speed cloud fallback via Chutes AI endpoints.
  - Triggered automatically when:
    - The local vision model fails or reports low confidence.
    - The user requests complex multi-step code generation or deep visual reasoning.
    - Heavy grounding is required across full 4K multi-monitor screenshots using `Qwen/Qwen2.5-VL-72B-Instruct` or `deepseek-ai/DeepSeek-R1`.
- **`router.py`:**
  - Dynamic routing engine deciding whether an action cycle executes locally or escalates to Chutes AI.

### 4.3. Action Dispatcher (`eidos/actions/`)

- **Tier 1 — Shell & Direct APIs (`shell.py`):**
  - Executes commands directly via Python `subprocess.Popen` with timeout controls and stdout/stderr capture.
  - Bypasses UI entirely for file manipulation, system queries, package installations, and process management.
- **Tier 2 — Accessibility & DOM (`dom.py`):**
  - Interacts directly with Chromium/Electron DOM trees via WebSocket connection to Chrome DevTools Protocol (CDP).
  - Can dispatch native DOM click events, fill input elements via `Runtime.evaluate`, and read element trees directly.
- **Tier 3 — Synthetic Input Engine (`mouse_keyboard.py`):**
  - Uses `pynput` and `pyautogui` to simulate human desktop input.
  - Actions supported:
    - `mouse_move(x, y, smooth=True)`
    - `mouse_click(x, y, button="left", clicks=1)`
    - `mouse_drag(start_x, start_y, end_x, end_y)`
    - `mouse_scroll(clicks, direction="down")`
    - `keyboard_type(text, delay_ms=12)`
    - `keyboard_hotkey(*keys)` (e.g., `["ctrl", "shift", "p"]`)

### 4.4. Safety & Guardrail Subsystem (`eidos/safety/`)

- **`kill_switch.py`:**
  - Dedicated background daemon thread running `pynput.keyboard.GlobalHotKeys`.
  - Global shortcut **`Ctrl + Alt + Esc`** triggers an atomic cancellation event:
    - Immediately kills any running child subprocesses.
    - Halts the agent loop.
    - Releases any held mouse buttons or modifier keys.
    - Updates HUD status to `[EMERGENCY STOP ACTIVATED]`.
- **`gatekeeper.py` & `risk_classifier.py`:**
  - Categorizes all requested actions into 4 security tiers:
    - **Level 0 (Passive/Inspection):** Screenshots, window queries, reading clipboard $\rightarrow$ Auto-allowed.
    - **Level 1 (UI Actions):** Mouse clicks, keyboard typing $\rightarrow$ Controlled by global toggle.
    - **Level 2 (Shell Execution):** Terminal commands require approval by default. Supports **"Always allow this command"** checkbox on confirmation.
    - **Level 3 (Destructive Actions):** `rm -rf`, `mkfs`, `dd`, `shutdown`, modifying system `/etc/` $\rightarrow$ Hard-locked confirmation, impossible to auto-allow.
- **`allowlist_manager.py`:**
  - Manages persistent JSON database of allowed commands (`~/.eidos/allowlist.json`).
  - Stores exact commands or regex patterns authorized by the user.
  - Provides API for the PyQt6 GUI to list, toggle, search, and delete allowed commands.
- **`audit_logger.py`:**
  - Appends every proposed action, classification level, user decision, and execution result to `~/.eidos/audit.log` with microsecond timestamps.

### 4.5. PyQt6 HUD & User Interface (`eidos/ui/`)

- **`overlay.py` (Transparent Canvas):**
  - Full-screen borderless window with `Qt.WindowType.FramelessWindowHint` and `Qt.WindowType.WindowStaysOnTopHint`.
  - Sets `Qt.WidgetAttribute.WA_TranslucentBackground`.
  - Enables `Qt.WindowType.WindowTransparentForInput` so mouse events pass through to underlying applications during normal operation.
  - Draws visual reticles, animated click ripple effects, and bounding boxes over target elements.
- **`floating_pill.py` (HUD Status Bar):**
  - Compact draggable or pinned widget positioned on screen.
  - Displays agent status: `● Idle`, `● Thinking (Ollama)`, `● Grounding`, `● Confirming`, `● Executing`.
  - Displays quick buttons: **[Pause]**, **[Stop (Kill)]**, **[Settings]**.
- **`confirmation_dialog.py`:**
  - Clean popup window presented when a Level 2 or Level 3 action is requested.
  - Shows exact command, working directory, and risk explanation.
  - For Level 2 commands: Includes a checkbox: `[ ] Remember and allow this command in the future`.
  - Supports keyboard shortcuts: `Enter` to approve, `Esc` to deny.
- **`allowlist_gui.py`:**
  - Dedicated management window to view all previously approved Level 2 commands.
  - Allows user to search commands, check execution frequency, and click `[Revoke Permission]` to place a command back into required confirmation.

---

## 5. Application Integration Bridges (`eidos/integrations/`)

### 5.1. OptimalX Desktop 1.0 Bridge (`optimalx_bridge.py`)

Because **OptimalX Desktop** is an Electron application built by the user, EidosBot can interface with it at maximum reliability:

1. **Remote Debugging Hook:** OptimalX can be launched with `--remote-debugging-port=9222`.
2. **Direct WebSocket CDP Connection:**
   - EidosBot connects via `aiohttp` / `websockets` to `ws://localhost:9222/devtools/page/...`.
   - Dispatches `DOM.getDocument`, `DOM.querySelector`, and `Runtime.evaluate`.
   - Reads exact element boundaries without relying on computer vision.
3. **IPC Bridge Fallback:**
   - If CDP is not used, a lightweight local Unix domain socket (`/tmp/optimalx-eidos.sock`) can allow OptimalX and EidosBot to exchange JSON-RPC messages directly.

### 5.2. Cursor IDE Bridge (`cursor_bridge.py`)

EidosBot interacts with Cursor via:
- **CLI Invocations:** `cursor -g /path/to/file:line:col` or opening workspace folders.
- **Command Palette Injection:** Sends synthetic `Ctrl+Shift+P` followed by typed command and `Enter`.
- **Terminal Control:** Inspects Cursor's integrated terminal processes or invokes tasks directly.

---

## 6. Directory Layout & Module Specifications

```text
EidosBot/
├── README.md
├── requirements.txt
├── config.example.yaml
├── config.yaml                     # User configuration (Ollama models, Chutes API key, UI settings)
├── docs/
│   ├── ARCHITECTURE.md             # This document
│   ├── FEATURES.md                 # Feature breakdown & safety matrix
│   └── ROADMAP.md                  # Implementation roadmap
├── eidos/
│   ├── __init__.py
│   ├── main.py                     # Main CLI and application launcher
│   ├── core/
│   │   ├── __init__.py
│   │   ├── loop.py                 # Core agent cycle (Sense -> Reason -> Act)
│   │   ├── context.py              # Conversation history & active task state
│   │   └── types.py                # Pydantic schemas for actions, tools, and events
│   ├── perception/
│   │   ├── __init__.py
│   │   ├── screen.py               # X11 screenshot capture (mss)
│   │   ├── window_info.py          # Active window metadata (xdotool, xprop)
│   │   └── grounder.py             # Coordinate grounding & SoM annotation
│   ├── actions/
│   │   ├── __init__.py
│   │   ├── mouse_keyboard.py       # pynput / pyautogui input simulation
│   │   ├── shell.py                # Safe subprocess execution
│   │   └── cdp.py                  # Chrome DevTools Protocol helper for Electron/Browsers
│   ├── safety/
│   │   ├── __init__.py
│   │   ├── kill_switch.py          # Background global hotkey listener (Ctrl+Alt+Esc)
│   │   ├── gatekeeper.py           # Action interception and policy enforcement
│   │   ├── risk_classifier.py      # Level 0–3 command risk categorization
│   │   ├── allowlist.py            # Persistent storage for user-whitelisted commands
│   │   └── audit.py                # Append-only audit logger
│   ├── models/
│   │   ├── __init__.py
│   │   ├── base.py                 # Base LLM client interface
│   │   ├── ollama_client.py        # Local Ollama connector (gemma4, mistral, etc.)
│   │   ├── chutes_client.py        # Cloud Chutes AI connector (Qwen 2.5-VL, DeepSeek)
│   │   └── router.py               # Model routing and fallback logic
│   ├── integrations/
│   │   ├── __init__.py
│   │   ├── optimalx.py             # OptimalX Desktop Electron bridge
│   │   └── cursor_ide.py           # Cursor IDE automation adapter
│   └── ui/
│       ├── __init__.py
│       ├── app.py                  # PyQt6 application lifecycle
│       ├── hud.py                  # Transparent click-through overlay
│       ├── pill.py                 # Draggable status pill
│       ├── dialogs.py              # Level 2/3 confirmation modals
│       └── allowlist_dialog.py     # Allowed command review & revocation UI
└── tests/
    ├── test_screen.py              # Test screen capture
    ├── test_window_info.py         # Test X11 window detection
    ├── test_safety.py              # Test kill switch and risk classifier
    └── test_allowlist.py           # Test allowlist serialization & revocation
```
