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
│                │
│  • Web research engine (multi-tab browser automation & extraction)     │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│  PHASE 5: Voice I/O & Remote Gateway (Complete)                        │
│  • Local Parakeet TDT ONNX push-to-talk STT                            │
│  • Local Kokoro neural TTS progressive audio feedback                  │
│  • Secure remote mobile companion & interactive approvals              │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│  PHASE 6: Dual-Tier Memory & Episodic Trajectory Engine                │
│  • Atomic human-editable User Profile YAML (~/.eidos/user_profile.yaml)│
│  • Append-only structured trajectory recorder for LoRA dataset prep    │
│  • Embedded vector RAG via nomic-embed-text (sqlite-vec / LanceDB)     │
│  • Mid-stream active thought recall (<recall>query</recall>)           │
│  • Gradual identity scaffolding removal & LoRA fine-tuning pipeline    │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│  PHASE 7: Kernel Sandboxing & Headless Playwright Automation           │
│  • Bubblewrap (bwrap) unprivileged filesystem & process isolation      │
│  • Kernel-enforced read-only protection of root and user home          │
│  • Auto-allowed workspace execution (eliminating allowlist fatigue)    │
│  • Headless Playwright Chromium engine replacing visual mouse clicking │
│  • Dynamic JS execution, patent search & clean Markdown extraction     │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│  PHASE 8: Skills, Rubrics & Batch Job Runner (300 Inventions Project)  │
│  • Human & Bot co-authored living SKILL.md documents                   │
│  • Cold-start procedural guidance & standardized evaluation rubrics    │
│  • State-machine batch runner (queue.json) with checkpointing & resume │
│  • Fresh context per item (eliminating context saturation & amnesia)   │
│  • Automated portfolio markdown generation & ranked leaderboard summary│
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│  PHASE 9: Telegram Remote Gateway & Human-in-the-Loop Approvals        │
│  • Bi-directional Telegram Bot API companion (long-polling)            │
│  • Interactive inline approval cards ([Approve], [Reject], [Kill])     │
│  • Batch job milestone progress streaming & remote question handling   │
│  • Remote emergency stop (/kill) and status inspection                 │
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
**Goal:** Provide zero-friction integration with OptimalX Desktop 1.0 and general web research workflows.

#### Deliverables & Tasks:
1. **OptimalX Desktop 1.0 Electron Bridge:**
   - `eidos/integrations/optimalx.py`:
     - Auto-detect or launch OptimalX with `--remote-debugging-port=9222`.
     - Connect via Chrome DevTools Protocol (CDP) WebSocket.
     - Execute native DOM queries (`DOM.querySelector`, `Runtime.evaluate`).
     - Read internal app state directly, bypassing visual ambiguity.

3. **Autonomous Web Research:**
   - Browser control via CDP / Playwright.
   - Multi-tab search, page content summarization, and PDF report downloading.

---

### Phase 5: Voice I/O & Remote Gateway (Complete)
**Goal:** Enable hands-free voice control at your desk and secure mobile remote management.

#### Deliverables & Tasks:
1. **Voice Control Subsystem (`eidos/voice/`):**
   - **Kokoro-82M TTS (`tts.py`):** High-speed local CPU neural speech synthesis with 54 voices (`bm_george`, `af_bella`, etc.) played via `paplay` / `aplay`.
   - **NVIDIA Parakeet TDT 0.6B v2 STT (`stt.py`):** Super-fast INT8 ONNX transcription via sherpa-onnx running on CPU to save VRAM for Ollama LLM.
   - **Audio Capture (`audio_capture.py`):** Clean 16kHz mono microphone recording via `arecord` / `ffmpeg`.
   - **Voice Manager (`voice_manager.py`):** Coordinates push-to-talk, status callbacks, and global hotkeys (`<ctrl>+<alt>+<space>`).
   - **UI Integration:** Status Pill microphone button with live recording animation and automatic prompt population; `speak_aloud` tool for agent responses.
2. **Remote Mobile Gateway (`eidos/gateway/`):**
   - **Fast REST & WebSocket Server (`server.py`):** Secured with Bearer token authentication.
   - **Mobile Web Dashboard:** Responsive Dracula dark-themed web app to monitor status, view live desktop screenshots, submit prompts, trigger vocal alerts, and trip/reset the emergency kill switch.
   - **Dual-Channel Remote Approvals:** When Level 2 / 3 action confirmations are triggered, an interactive approval card with screenshot is pushed to mobile over WebSockets, allowing instant approval/denial from your phone when away from your desk.

---

### Phase 6: Dual-Tier Memory & Episodic Trajectory Engine
**Goal:** Replace prompt-engineered scaffolding with a self-improving autobiographical memory system and automated LoRA dataset extraction.

*(See full specification in [`docs/EPISODIC_MEMORY.md`](EPISODIC_MEMORY.md))*

#### Deliverables & Tasks:
1. **Tier B: Atomic User Profile Store (`eidos/memory/user_store.py`):**
   - Human-readable YAML configuration at `~/.eidos/user_profile.yaml`.
   - Discrete, atomic facts for habits, conventions, paths, and preferences (~60 prompt tokens).
   - Direct CRUD API and UI integration in PyQt6 Settings Dialog for transparent editing and revocation.
2. **Trajectory Recorder (`eidos/memory/trajectory_logger.py`):**
   - Append-only structured JSONL logging at `~/.eidos/trajectories/episodes.jsonl`.
   - Captures task goal, environment snapshot, raw `<thought>` tokens, tool calls, tool results, user interventions, and outcome flags.
3. **Tier A: Local Episodic Vector Database (`eidos/memory/episodic_store.py`) [COMPLETE]:**
   - Embedded local vector storage (`sqlite-vec`) with zero external daemon requirements.
   - 768-dimensional embeddings generated locally via `nomic-embed-text:latest` on Ollama (CPU/GPU).
   - Observation-driven trajectory distillation: evaluates tool outputs to separate verified successful actions (kept in natural forward order) from exploratory dead ends (top 2–3 failure reasons).
   - Semantic retrieval for macro turn-start context (golden paths, pitfalls avoided, and proven tool patterns).
   - Automatic startup synchronization and re-indexing from trajectory JSONL logs.
4. **Active Recall & Mid-Stream Injection (`eidos/memory/recall_stream.py`) [COMPLETE]:**
   - Token-stream interceptor supporting both local `ollama_client.py` and cloud `chutes_client.py` for `<recall>query</recall>` tags.
   - Pauses SSE stream, queries vector memory, splices recalled experience (with dead ends and proven tool pattern), and resumes inference seamlessly.
5. **LoRA Fine-Tuning Pipeline (`training/`):**
   - `export_sft.py`: Formats successful trajectories into ShareGPT/ChatML datasets for Supervised Fine-Tuning.
   - `export_dpo.py`: Pairs mistake traces with recovery actions for Direct Preference Optimization.
   - 3-stage scaffolding reduction: systematically pruning system prompt rules as neural weights absorb habits and identity.

---

### Phase 7: Kernel Sandboxing & Headless Playwright Automation
**Goal:** Eliminate allowlist prompt fatigue and replace fragile coordinate-based mouse clicking with deterministic kernel sandboxing and headless browser automation.

*(See full specifications in [`docs/SANDBOXING.md`](SANDBOXING.md) and [`docs/PLAYWRIGHT_AUTOMATION.md`](PLAYWRIGHT_AUTOMATION.md))*

#### Deliverables & Tasks:
1. **Linux Bubblewrap Sandbox (`eidos/sandbox/bwrap.py`):**
   - Unprivileged kernel filesystem isolation mounting `/`, `/usr`, `/etc`, and `~` as read-only.
   - Designates writeable workspace mounts (`~/EidosWorkspace` or job folders).
   - Updates `ShellExecutor` to auto-allow sandboxed tasks without prompt interruptions.
   - Restricts manual Gatekeeper popups to un-sandboxed host operations.
2. **Headless Playwright Engine (`eidos/browser/manager.py`):**
   - Background Chromium engine executing JavaScript and Single Page Applications (SPAs).
   - Clean HTML-to-Markdown extraction via `trafilatura`.
   - Tool suite: `browser_open`, `browser_extract`, `browser_click`, `browser_type`, `browser_screenshot`, `browser_close`.
   - Replaces screen-capture-and-mouse-clicking loops for web workflows.

---

### Phase 8: Skills, Rubrics & Batch Job Runner (300 Inventions Project)
**Goal:** Enable collaborative human/bot procedural guides and a resilient state-machine queue runner for executing multi-item projects without context degradation.

*(See full specification in [`docs/SKILLS_AND_JOBS.md`](SKILLS_AND_JOBS.md))*

#### Deliverables & Tasks:
1. **Living Skill Documents (`eidos/skills/manager.py`):**
   - Store and manage co-authored `~/.eidos/skills/<name>/SKILL.md` procedures, evaluation rubrics, and templates.
   - Solves cold-start tasks where episodic memory has no prior task history.
   - Reference implementation: `invention-evaluator` skill with 4-dimension scoring rubric and portfolio templates.
2. **State-Machine Batch Runner (`eidos/jobs/runner.py`):**
   - Queue manager utilizing `queue.json` with status checkpoints (`pending`, `in_progress`, `completed`, `needs_review`).
   - Fresh context isolation per item, eliminating amnesia and token window saturation across 300+ items.
   - Automatic generation of ranked summary leaderboards (`RANKING_SUMMARY.md`).
   - Pause, resume, and retry controls.

---

### Phase 9: Telegram Remote Gateway & Human-in-the-Loop Approvals
**Goal:** Turn EidosBot into a 24/7 continuous companion accessible from your phone with interactive approvals and batch job streaming.

*(See full specification in [`docs/TELEGRAM_GATEWAY.md`](TELEGRAM_GATEWAY.md))*

#### Deliverables & Tasks:
1. **Telegram Gateway Daemon (`eidos/gateway/telegram_bot.py`):**
   - Long-polling bi-directional bot connecting your phone to the local Ollama agent loop.
   - Strict numeric user ID whitelist security.
2. **Interactive Remote Approvals:**
   - Inline keyboard cards (`[Approve]`, `[Reject]`, `[Kill]`) for host shell actions or ambiguous job steps.
   - Asynchronous callback resolution directly unblocking the `Gatekeeper`.
3. **Remote Monitoring & Emergency Stop:**
   - Milestone progress push notifications (e.g. every 10 completed inventions).
   - Remote `/status`, `/jobs`, `/pause`, `/resume`, and `/kill` commands.

---

## 🚦 Recommended Next Steps

With the architecture and full specifications in place, the immediate implementation roadmap is:
1. **Install System Dependencies:** Install `bubblewrap` and `playwright` (`pip install playwright trafilatura python-telegram-bot && playwright install chromium`).
2. **Deploy Kernel Sandboxing (`eidos/sandbox/bwrap.py`):** Hook into `ShellExecutor` so shell commands become safe by default, immediately removing allowlist prompt fatigue.
3. **Deploy Headless Browser Subsystem (`eidos/browser/manager.py`):** Wire `browser_*` tools into `EIDOS_TOOLS` to enable web research without mouse clicks.
4. **Deploy Skills & Job Runner (`eidos/skills/` & `eidos/jobs/`):** Set up the `invention-evaluator` `SKILL.md` and `queue.json` state machine for the 300 Inventions project.
5. **Launch Telegram Bot Gateway (`eidos/gateway/telegram_bot.py`):** Wire the bot token and interactive inline approvals for remote human-in-the-loop operation.
3. Build the vector database connector (`episodic_store.py`) backed by local `nomic-embed-text`.
4. Implement `<recall>` tag interception in `ollama_client.py`.
5. Add the User Profile manager tab to the PyQt6 Settings Dialog.
