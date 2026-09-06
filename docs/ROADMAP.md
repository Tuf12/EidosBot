# EidosBot Implementation Roadmap

This roadmap defines the step-by-step implementation plan for **EidosBot**, from initial CLI MVP to a full PyQt6 transparent HUD with native app bridges and remote control capabilities.

---

## 📅 Roadmap Overview

```
┌────────────────────────────────────────────────────────────────────────┐
│  PHASE 1: Core Engine & Safety Baseline (CLI MVP)                      │
│  • X11 screen capture & active window detection                        │
│  • Ctrl+Alt+Esc global hardware kill switch                            │
│  • Level 0–3 Risk Classifier & Persistent Allowlist                    │
│  • Ollama local model integration (gemma4-26b & 12b)                   │
│  • Sense → Reason → Confirm → Act execution loop                       │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│  PHASE 2: PyQt6 Visual HUD & Allowlist GUI                             │
│  • Transparent click-through overlay canvas                            │
│  • Target reticle & click animation generator                          │
│  • Draggable Floating Status Pill                                      │
│  • Interactive Confirmation Dialog (with "Remember Command" checkbox)   │
│  • Command Allowlist Manager GUI (view, search, revoke permissions)   │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│  PHASE 3: Cloud Escalation & Advanced Visual Grounding                 │
│  • Chutes AI client integration (Qwen 2.5-VL 72B & DeepSeek-R1)        │
│  • Intelligent Model Router (Local-first with cloud fallback)          │
│  • Set-of-Mark (SoM) bounding box generator & coordinate mapper       │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│  PHASE 4: Target App Integrations & Web Research                       │
│  • OptimalX Desktop 1.0 Electron Bridge (CDP / DOM Inspection)         │
│  • Cursor IDE automation adapter (CLI & Command Palette)               │
│  • Web research engine (multi-tab browser automation & extraction)     │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│  PHASE 5: Voice I/O & Remote Gateway (Future Expansion)                │
│  • Local Whisper push-to-talk STT                                      │
│  • Local Piper / Kokoro TTS voice feedback                             │
│  • Secure remote companion (encrypted mobile web / Telegram bridge)   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Detailed Phase Breakdown

### Phase 1: Core Engine & Safety Baseline (CLI MVP)
**Goal:** Deliver a fully functional, headless/CLI desktop agent with absolute safety guardrails, capable of executing basic desktop and terminal workflows.

#### Deliverables & Tasks:
1. **Repository & Environment Setup:**
   - Define `requirements.txt` (`mss`, `pynput`, `pyautogui`, `pillow`, `pydantic`, `pyyaml`, `requests`, `aiohttp`, `xdotool`).
   - Create `config.example.yaml` and configuration loader.
2. **Perception Engine:**
   - `eidos/perception/screen.py`: Implement X11 screen capture via `mss` with downscaling and base64 encoding.
   - `eidos/perception/window_info.py`: Query active window ID, `WM_CLASS`, PID, and title using `xdotool` and `xprop`.
3. **Safety & Guardrails:**
   - `eidos/safety/kill_switch.py`: Implement global hotkey listener for **`Ctrl + Alt + Esc`** using `pynput` daemon thread.
   - `eidos/safety/risk_classifier.py`: Categorize commands into Level 0 (Passive), Level 1 (UI), Level 2 (Shell), and Level 3 (Destructive).
   - `eidos/safety/allowlist.py`: JSON-backed storage (`~/.eidos/allowlist.json`) supporting add, remove, match, and check operations.
   - `eidos/safety/gatekeeper.py`: Command interceptor; prompts user in terminal (`[Y]es, [N]o, [A]lways allow this command`).
   - `eidos/safety/audit.py`: Append-only audit logger writing to `~/.eidos/audit.log`.
4. **Action Engine:**
   - `eidos/actions/shell.py`: Subprocess execution with timeout, stream capture, and abort signals.
   - `eidos/actions/mouse_keyboard.py`: Clicks, double clicks, scrolls, text typing, and key combinations.
5. **Cognitive Loop & Model Client:**
   - `eidos/models/ollama_client.py`: Connect to local Ollama (`gemma4-26b` for tool calling and `gemma4-12b` for vision).
   - `eidos/core/loop.py`: Execute the basic Sense $\rightarrow$ Reason $\rightarrow$ Confirm $\rightarrow$ Act cycle.
   - `eidos/main.py`: Interactive CLI prompt for giving tasks to EidosBot.

---

### Phase 2: PyQt6 Visual HUD & Allowlist GUI
**Goal:** Replace CLI prompts with an intuitive, non-intrusive desktop overlay, live status pill, and GUI management for the command allowlist.

#### Deliverables & Tasks:
1. **PyQt6 Application Infrastructure:**
   - `eidos/ui/app.py`: Qt event loop manager running alongside the async agent thread.
2. **Transparent Click-Through HUD:**
   - `eidos/ui/hud.py`:
     - Fullscreen transparent window with `WA_TranslucentBackground`.
     - `WindowTransparentForInput` flag to pass clicks through to underlying applications.
     - Paint engine to render glowing reticles and highlight target boxes before clicks execute.
3. **Floating Status Pill:**
   - `eidos/ui/pill.py`:
     - Compact, draggable widget indicating live state (Idle, Thinking, Perceiving, Executing, Error).
     - Quick buttons for **Pause**, **Kill (Emergency Stop)**, and **Allowlist Manager**.
4. **Interactive Confirmation Dialog:**
   - `eidos/ui/dialogs.py`:
     - Native popup dialog for Level 2 & Level 3 actions.
     - Displays full command, target directory, and risk classification.
     - Checkbox: `[ ] Remember and allow this command in the future` (enabled only for Level 2).
     - Keyboard shortcuts: `Enter` to approve, `Esc` to deny.
5. **Command Allowlist Manager GUI:**
   - `eidos/ui/allowlist_dialog.py`:
     - Searchable table displaying all approved Level 2 commands.
     - Details: Command pattern, date added, execution count, last executed timestamp.
     - **`[ Revoke ]`** button for each item to instantly put the command back into required confirmation.
     - Manual command add button.

---

### Phase 3: Cloud Escalation & Advanced Visual Grounding
**Goal:** Supercharge accuracy on complex multi-monitor layouts and intricate UI elements using high-performance cloud models and Set-of-Mark parsing.

#### Deliverables & Tasks:
1. **Chutes AI Connector:**
   - `eidos/models/chutes_client.py`:
     - High-speed async client for Chutes AI endpoints.
     - Support for `Qwen/Qwen2.5-VL-72B-Instruct` (visual grounding) and `deepseek-ai/DeepSeek-R1` (complex planning).
2. **Cognitive Router:**
   - `eidos/models/router.py`:
     - Automatic escalation rules:
       - Escalate to Chutes if target element < 25px or local model confidence is low.
       - Escalate to Chutes if task requires multi-file code synthesis.
     - Manual override toggle on the Status Pill (`Local Only` / `Auto-Cloud` / `Force Cloud`).
3. **Visual Grounding Pipeline:**
   - `eidos/perception/grounder.py`:
     - Implement Set-of-Mark (SoM) annotation: detect interactable UI elements and badge them with numeric IDs (`#1`, `#2`, `#3`).
     - OCR text bounding box detection for pinpointing buttons by text label.

---

### Phase 4: Target App Integrations & Web Research
**Goal:** Provide zero-friction, deep integration with OptimalX Desktop 1.0, Cursor IDE, and general web research workflows.

#### Deliverables & Tasks:
1. **OptimalX Desktop 1.0 Electron Bridge:**
   - `eidos/integrations/optimalx.py`:
     - Auto-detect or launch OptimalX with `--remote-debugging-port=9222`.
     - Connect via Chrome DevTools Protocol (CDP) WebSocket.
     - Execute native DOM queries (`DOM.querySelector`, `Runtime.evaluate`).
     - Read internal app state directly, bypassing visual ambiguity.
2. **Cursor IDE Adapter:**
   - `eidos/integrations/cursor_ide.py`:
     - Direct CLI commands: `cursor --goto <file>:<line>:<col>`.
     - Command palette automation (`Ctrl+Shift+P`) with verified focus states.
3. **Autonomous Web Research:**
   - Browser control via CDP / Playwright.
   - Multi-tab search, page content summarization, and PDF report downloading.

---

### Phase 5: Voice I/O & Remote Gateway (Future Expansion)
**Goal:** Enable hands-free voice control at your desk and secure mobile remote management.

#### Deliverables & Tasks:
1. **Voice Control:**
   - Push-to-talk keybinding for local Faster-Whisper transcription.
   - Text-to-Speech audio feedback via Piper or Kokoro.
2. **Remote Mobile Gateway:**
   - End-to-end encrypted WebSocket / Telegram bot bridge.
   - Allows sending prompts to EidosBot from your phone.
   - Pushes interactive approval notifications (with screenshot) to mobile for Level 2/3 confirmations when away from your PC.

---

## 🚦 Recommended Next Steps

With the specifications, architecture, and roadmap documented, the recommended next step is:
1. Create `config.example.yaml` and `requirements.txt`.
2. Begin **Phase 1 Implementation**:
   - Build the safety module (`kill_switch.py`, `risk_classifier.py`, `allowlist.py`, `gatekeeper.py`).
   - Build perception (`screen.py`, `window_info.py`).
   - Build local Ollama integration and the core loop.
