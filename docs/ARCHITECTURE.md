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
                              │   (Floating HUD, CLI, Hotkey, Telegram) │
                              └────────────────────┬────────────────────┘
                                                   │
                                                   ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                           EIDOS CORE LOOP                                              │
│                                                                                                        │
│   ┌────────────────────────┐      ┌────────────────────────┐      ┌────────────────────────────────┐   │
│   │   PERCEPTION ENGINE    │      │    COGNITIVE ROUTER    │      │       ACTION DISPATCHER        │   │
│   │                        │      │                        │      │                                │   │
│   │ • X11 Screen Capture   │────▶ │ • Ollama (Local LLM)   │────▶ │ • Tier 1: Sandboxed Shell      │   │
│   │ • Window Info (xdotool)│      │   - gemma4-26b (Plan)  │      │   (bwrap kernel isolation)     │   │
│   │ • Playwright DOM Reader│      │   - gemma4-12b (Vision)│      │ • Tier 2: Headless Browser     │   │
│   │ • Skills & Rubric RAG  │      │ • Chutes AI (Cloud)    │      │   (Playwright / CDP DOM)       │   │
│   │ • Episodic Memory RAG  │      │ • Batch Job State Q    │      │ • Tier 3: Synthetic Mouse/Keys │   │
│   └────────────────────────┘      └────────────────────────┘      └────────────────┬───────────────┘   │
└────────────────────────────────────────────────────────────────────────────────────┼───────────────────┘
                                                                                     │
                                                                                     ▼
                                                                    ┌────────────────────────────────┐
                                                                    │       SAFETY GATEKEEPER        │
                                                                    │                                │
                                                                    │ • Global Kill Switch (Ctrl+    │
                                                                    │   Alt+Esc / Telegram /kill)    │
                                                                    │ • Sandboxed Workspace: AUTO    │
                                                                    │ • Host Shell: Allowlist/Prompt │
                                                                    │ • Destructive: Hard Confirm    │
                                                                    └────────────────┬───────────────┘
                                                                                     │
                                            ┌────────────────────────────────────────┴───────────────────┐
                                            ▼                                                            ▼
                             ┌──────────────────────────────┐                            ┌──────────────────────────────┐
                             │       EXECUTION TARGETS      │                            │      FEEDBACK & CONTROLS     │
                             │                              │                            │                              │
                             │ • Linux Mint OS / X11 System │                            │ • PyQt6 Transparent Overlay  │
                             │ • Sandboxed Job Workspace    │ ◀───────────────────────── │ • Floating Status Pill & Chat│
                             │ • Headless Chromium Browser  │      Visual & Remote       │ • Telegram Gateway (Mobile   │
                             │ • OptimalX Desktop (CDP)     │      Feedback Loops        │   Approvals & Milestone Push)│
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
  - Handles multi-monitor setups (identifies active monitor based on mouse pointer or active window location).
  - Implements downscaling, compression, and region-of-interest (ROI) cropping to minimize prompt token usage when calling vision models.
- **`window_info.py` (X11 Window & Process Context):**
  - Uses `xdotool` and `xprop` to retrieve:
    - Active window ID and name.
    - Application `WM_CLASS` (e.g., `google-chrome`, `firefox`, `optimalx`).
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

### 4.6. Memory & Episodic Learning Subsystem (`eidos/memory/`)

*(Detailed specifications in [EPISODIC_MEMORY.md](EPISODIC_MEMORY.md))*

- **`user_store.py` (Tier B: Atomic User Profile):**
  - Manages `~/.eidos/user_profile.yaml` storing explicit, human-readable user habits, paths, and workflow rules.
  - Renders compact fact lists into prompt context (~60 tokens) and exposes direct CRUD operations for GUI editing.
- **`trajectory_logger.py` (LoRA Dataset Recorder):**
  - Appends full multi-step decision trajectories to `~/.eidos/trajectories/episodes.jsonl`.
  - Captures goals, system states, raw `<thought>` tokens, tool calls, results, and outcome evaluation for future SFT and DPO training.
- **`episodic_store.py` (Tier A: Vector RAG):**
  - Local vector database (SQLite with `sqlite-vec` or LanceDB) using `nomic-embed-text:latest` running locally on Ollama.
  - Distills raw trajectories using observation analysis (`_is_tool_observation_successful`): separates verified successful action chains (preserved in full forward order) from exploratory dead ends (cached as the top 2–3 pitfalls with cleaned error messages).
  - Performs macro retrieval at task start to inject top golden paths and cautionary pitfalls into `<episodic_context>`.
  - Automatically syncs and reindexes trajectory logs on startup.
- **`reflector.py` (Post-Task Synthesizer):**
  - Analyzes completed or failed cycles to extract dense lessons and pitfalls avoided.
- **`recall_stream.py` (Active Recall & Stream Splicer):**
  - Intercepts `<recall>query</recall>` tags generated inside model thought streams (supports both local Ollama and cloud Chutes AI SSE streams).
  - Pauses token generation, executes local vector search, splices the recalled memory (including dead ends and proven tool patterns) into the stream, and resumes autoregressive reasoning.

### 4.7. Kernel Sandboxing Subsystem (`eidos/sandbox/`)

*(Detailed specifications in [SANDBOXING.md](SANDBOXING.md))*

- **`bwrap.py` (Bubblewrap Isolation):**
  - Uses unprivileged Linux namespaces (`bwrap`) to enforce read-only protection over `/`, `/usr`, `/etc`, and `~`.
  - Designates only specific workspace directories (e.g. `~/EidosWorkspace` or job target directories) as read-write.
  - Auto-allows sandboxed operations without prompt fatigue while physically preventing destructive file deletions or system alterations.

### 4.8. Headless Browser Subsystem (`eidos/browser/`)

*(Detailed specifications in [PLAYWRIGHT_AUTOMATION.md](PLAYWRIGHT_AUTOMATION.md))*

- **`manager.py` (Playwright Engine):**
  - Replaces fragile coordinate-based mouse clicking for web research with deterministic DOM navigation.
  - Executes dynamic client-side JavaScript, handles Single Page Applications (SPAs), patent databases, and web forms.
  - Converts live page content to dense, token-efficient Markdown using `trafilatura`.

### 4.9. Skills & Collaborative Job Runner (`eidos/skills/` & `eidos/jobs/`)

*(Detailed specifications in [SKILLS_AND_JOBS.md](SKILLS_AND_JOBS.md) and [PROJECT_JOBS.md](PROJECT_JOBS.md))*

- **`skills/manager.py`:**
  - Manages human-and-agent editable `SKILL.md` documents containing standard operating procedures, rubrics, and templates.
- **`jobs/runner.py`:**
  - State machine batch runner managing `queue.json` for processing large-scale projects (e.g., 300 Inventions evaluation) item by item with fresh contexts, checkpointing, and pause/resume.
  - Batch execution is a component a project job can attach. The project brief itself lives in `job.yaml` and is specified in [PROJECT_JOBS.md](PROJECT_JOBS.md).

### 4.10. Remote Telegram Gateway (`eidos/gateway/telegram_bot.py`)

*(Detailed specifications in [TELEGRAM_GATEWAY.md](TELEGRAM_GATEWAY.md))*

- **Bi-Directional Mobile Companion:**
  - Connects the user's phone to the local Ollama agent loop via Telegram Bot API with strict user ID authentication.
  - Presents interactive inline buttons (`[Approve]`, `[Reject]`, `[Kill]`) for host-level or destructive commands.
  - Streams periodic milestone progress updates and clarifying questions during batch job runs.

### 4.11. Mobile PWA Gateway & Tailscale Private Mesh (`web/` & `eidos/gateway/server.py`)

*(Detailed specifications in [MOBILE_PWA_GATEWAY.md](MOBILE_PWA_GATEWAY.md))*

- **Platform-Agnostic Mobile Companion (Android & iOS):**
  - Standalone Progressive Web App (PWA) installable on Android (via Chrome WebAPK) and iPhone (via Safari Home Screen).
  - Encrypted, zero-port-forwarding connectivity anywhere in the world via private Tailscale WireGuard mesh.
  - Real-time typewriter token streaming and collapsible `<thought>` / `<recall>` reasoning accordions over WebSockets.
  - Interactive Level 2 and Level 3 safety approval modals with bash command diffs, working directories, and risk badges.
  - Live desktop screen mirroring with zoom controls and tactile emergency kill switch button.

---

## 5. Application Integration Bridges (`eidos/integrations/`)

### 5.1. OptimalX Desktop 1.0 & Knowledge Ecosystem Bridge (`optimalx.py`)

Because **OptimalX Desktop** and **OptimalX Mobile** are personally developed by the user and synchronized over a private Tailscale WireGuard mesh, EidosBot interfaces directly with the user's complete knowledge base:

1. **Local SQLite Single Source of Truth (`~/OptimalX/data.db`):**
   - Direct read-only connection (`mode=ro`) via WAL mode ensures instantaneous offline note search and retrieval even when OptimalX Desktop is closed.
   - Respects user privacy flags: strictly excludes `ai_blind = 1` and soft-deleted rows.
2. **Online Semantic Vector Search (`POST /api/v1/llm/search`):**
   - When the OptimalX Express server is running on port 7373, EidosBot queries pre-embedded vector chunks (`semantic_chunks` table) indexed via local `nomic-embed-text`.
3. **Safe Mutation & Diff Review Safeguard:**
   - Empty/new notes auto-apply directly to SQLite with SHA-256 `content_hash` and `content_checkpoints`.
   - Edits to existing non-empty notes queue into `pending_change_items` for human-in-the-loop review in the OptimalX desktop UI.
   - Seamless sync parity: OptimalX Mobile automatically pulls changes over Tailscale on subsequent sync cycles.
4. **NoteX Long-Form Markdown Corpus Reader:**
   - Navigates and reads multi-document projects under `~/OptimalX/notex/<global_id>/`.
5. **Remote Debugging & DOM Inspection (CDP):**
   - Connects to Chrome DevTools Protocol WebSocket on port 9222 for direct DOM inspection and UI state evaluation without relying solely on computer vision.

*(See full specification in [`docs/OPTIMALX_INTEGRATION.md`](OPTIMALX_INTEGRATION.md))*





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
│   ├── ROADMAP.md                  # Implementation roadmap
│   ├── OPTIMALX_INTEGRATION.md     # OptimalX bi-directional note & knowledge bridge spec
│   ├── EPISODIC_MEMORY.md          # Episodic memory, recall & LoRA training spec
│   ├── SKILLS_AND_JOBS.md          # Skills, rubrics & batch job runner spec
│   ├── INVENTION_RESEARCH_PIPELINE.md # Inventions research & evaluation pipeline spec
│   ├── SANDBOXING.md               # Kernel filesystem isolation (bwrap) spec
│   ├── PLAYWRIGHT_AUTOMATION.md    # Headless browser automation spec
│   ├── TELEGRAM_GATEWAY.md         # Telegram mobile companion & approvals spec
│   ├── MOBILE_PWA_GATEWAY.md       # Mobile PWA companion & Tailscale spec
│   └── IMPLEMENTATION_GUIDE.md     # Step-by-step master implementation plan
├── eidos/
│   ├── __init__.py
│   ├── main.py                     # Main CLI and application launcher
│   ├── core/
│   │   ├── __init__.py
│   │   ├── loop.py                 # Core agent cycle (Sense -> Reason -> Act)
│   │   ├── context.py              # Conversation history & active task state
│   │   ├── clock.py                # Internal high-accuracy date & time tracking
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
│   ├── sandbox/
│   │   ├── __init__.py
│   │   └── bwrap.py                # Bubblewrap kernel isolation wrapper
│   ├── browser/
│   │   ├── __init__.py
│   │   └── manager.py              # Headless Playwright engine & DOM parser
│   ├── skills/
│   │   ├── __init__.py
│   │   └── manager.py              # Human/Bot co-authored SKILL.md manager
│   ├── jobs/
│   │   ├── __init__.py
│   │   └── runner.py               # Persistent queue state machine (queue.json)
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
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── conversation_store.py   # SQLite persistent conversation & message store (~/.eidos/eidos.db)
│   │   ├── user_store.py           # Atomic YAML user profile manager
│   │   ├── trajectory_logger.py    # Append-only JSONL episode recorder
│   │   ├── episodic_store.py       # Vector DB interface (nomic-embed-text)
│   │   ├── reflector.py            # Post-task critic & lesson synthesizer
│   │   ├── recall_stream.py        # Active recall token stream interceptor
│   │   └── types.py                # Pydantic schemas for episodes and facts
│   ├── voice/
│   │   ├── __init__.py
│   │   ├── voice_manager.py        # Audio coordination & push-to-talk listener
│   │   ├── tts.py                  # Local Kokoro-82M neural TTS
│   │   ├── stt.py                  # Local NVIDIA Parakeet TDT 0.6B ONNX STT
│   │   ├── audio_capture.py        # ALSA/ffmpeg 16kHz mono recording
│   │   ├── speech_stream.py        # Progressive TTS chunk streaming
│   │   └── chunker.py              # Sentence & clause streaming chunker
│   ├── gateway/
│   │   ├── __init__.py
│   │   ├── server.py               # Mobile REST & WebSocket gateway server
│   │   └── telegram_bot.py         # Telegram bot & remote approval bridge
│   ├── integrations/
│   │   ├── __init__.py
│   │   ├── optimalx.py             # OptimalX Desktop Electron bridge
│   │   
│   │   └── web_research.py         # Web search and content fetcher
│   └── ui/
│       ├── __init__.py
│       ├── app.py                  # PyQt6 application lifecycle
│       ├── hud.py                  # Transparent click-through overlay
│       ├── pill.py                 # Draggable status pill with voice controls
│       ├── chat.py                 # Integrated conversation UI
│       ├── conversations_dialog.py # Past conversations manager GUI
│       ├── dialogs.py              # Level 2/3 confirmation modals
│       ├── settings_dialog.py      # Live settings configuration dialog
│       └── allowlist_dialog.py     # Allowed command review & revocation UI
├── training/
│   ├── export_sft.py               # Export trajectories to ShareGPT / ChatML
│   ├── export_dpo.py               # Extract preference pairs from mistakes
│   └── train_lora.py               # Local LoRA fine-tuning runner
├── web/                            # Mobile PWA companion frontend (React + Tailwind + Vite)
│   ├── package.json
│   ├── vite.config.ts
│   ├── public/
│   │   ├── manifest.json           # Web App Manifest (Android WebAPK & iOS Safari)
│   │   └── icons/                  # 192x192 & 512x512 app launcher icons
│   ├── src/
│   │   ├── App.tsx                 # Root layout, status pill & views
│   │   ├── hooks/useEidosWS.ts     # Resilient WebSocket connection hook
│   │   └── components/             # ChatView, ScreenViewport, SafetyModal
│   └── dist/                       # Compiled production bundle (served by server.py)
└── tests/
    ├── test_screen.py              # Test screen capture
    ├── test_window_info.py         # Test X11 window detection
    ├── test_safety.py              # Test kill switch and risk classifier
    ├── test_allowlist.py           # Test allowlist serialization & revocation
    ├── test_voice.py               # Test TTS & STT voice pipelines
    ├── test_gateway.py             # Test mobile gateway server
    └── test_memory.py              # Test episodic vector store & trajectories
```
