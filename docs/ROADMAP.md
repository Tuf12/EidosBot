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
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│  PHASE 10: Mobile PWA Gateway & Tailscale Private Mesh Network         │
│  • Standalone installable PWA for Android (WebAPK) & iOS Safari        │
│  • Encrypted zero-trust remote connectivity via Tailscale WireGuard    │
│  • Real-time typewriter token streaming & collapsible reasoning cards  │
│  • Live desktop viewport mirroring & interactive safety approval cards │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│  PHASE 11: OptimalX Bi-Directional Knowledge & Corpus Integration     │
│  • Single source of truth in local SQLite WAL (~/OptimalX/data.db)     │
│  • Dual-tier retrieval (online HTTP semantic search & offline WAL FTS) │
│  • Human-in-the-loop Diff Review contract (auto-apply vs queued diff)  │
│  • NoteX long-form markdown corpus tree navigation & reading           │
│  • Cryptographic SHA-256 sync compatibility with OptimalX Mobile      │
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

*(See full specifications in [`docs/SKILLS_AND_JOBS.md`](SKILLS_AND_JOBS.md) and [`docs/INVENTION_RESEARCH_PIPELINE.md`](INVENTION_RESEARCH_PIPELINE.md))*

#### Deliverables & Tasks:
1. **Living Skill Documents (`eidos/skills/manager.py`):**
   - Store and manage co-authored `~/.eidos/skills/<name>/SKILL.md` procedures, evaluation rubrics, and templates.
   - Solves cold-start tasks where episodic memory has no prior task history.
   - Reference implementation: `invention-evaluator` skill with 4-dimension scoring rubric and portfolio templates.
2. **State-Machine Batch Runner (`eidos/jobs/runner.py`):**
   - Queue manager utilizing `queue.json` with status checkpoints (`pending`, `in_progress`, `completed`, `needs_review`).
   - Fresh context isolation per item, eliminating amnesia and token window saturation across 300+ items.
   - Pause, resume, retry, and atomic checkpoint controls.
3. **Fresh-Context Sub-Agent Processor (`eidos/jobs/processor.py`):**
   - Ephemeral per-item reasoning session loading the assigned `SKILL.md` and item data.
   - Tool execution integrating `WebResearchEngine` (Brave API) and `PlaywrightManager` (Google Patents).
   - Structured multi-dimensional scoring prompt returning JSON evaluation and justifications.
   - Sandboxed markdown portfolio generation (`<output_dir>/portfolios/<item_id>.md`).
4. **Autonomous Background Batch Loop & Throttling (`eidos/jobs/runner.py`):**
   - Sequential item execution loop in background daemon thread (`run_job`).
   - Configurable cool-down pacing (`pause_between_items_sec`) to respect rate limits and GPU thermals.
   - Integrated with global kill switch and Telegram milestone broadcasts every $N$ items.
5. **Leaderboard Aggregation & Ranking (`eidos/jobs/summary.py`):**
   - Automatic generation of ranked summary leaderboards (`RANKING_SUMMARY.md`) sorted by composite viability score.
   - Multi-tier categorization (Tier 1 High-Viability 80–100, Tier 2 Moderate 60–79, Tier 3 Speculative <60).
   - Export to `RANKING_SUMMARY.csv` and `RANKING_SUMMARY.json`.

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

### Phase 10: Mobile PWA Gateway & Tailscale Private Mesh
**Goal:** Deliver an installable, high-bandwidth Progressive Web App (PWA) running on both Android and iPhone, providing real-time token streaming, live desktop mirroring, and rich safety interception modals over an encrypted Tailscale private mesh network.

*(See full specification in [`docs/MOBILE_PWA_GATEWAY.md`](MOBILE_PWA_GATEWAY.md))*

#### Deliverables & Tasks:
1. **Frontend PWA Scaffolding (`web/`):**
   - React 19 / 18 + Vite + TypeScript + Tailwind CSS with Dracula theme tokens.
   - PWA manifest (`manifest.json`), service worker, and app icons for standalone Android (WebAPK) and iOS home screen launching.
2. **Real-Time Streaming & State Layer:**
   - Resilient WebSocket connection hook (`useEidosWS`) with exponential backoff reconnect.
   - Live token typewriter stream, status transitions, and confirmation queues.
3. **Interactive UI Views & Components:**
   - Markdown chat with collapsible `<thought>` / `<recall>` reasoning accordions.
   - Interactive Level 2/3 safety approval modals with bash command diffs and risk tags.
   - Live desktop screenshot viewport with auto-refresh and pinch-to-zoom.
   - Dracula status pill and persistent tactile emergency kill switch `[🛑 STOP]`.
4. **Backend Gateway Static Serving (`eidos/gateway/server.py`):**
   - Serve compiled `web/dist/` SPA bundle with fallback to embedded dashboard.
   - Expand WebSocket handler to broadcast token streams, thought chunks, and confirmation resolutions.
5. **Tailscale Private Networking Setup:**
   - Zero-port-forwarding WireGuard mesh connecting Linux host, Android phone, and wife's iPhone.

---

### Phase 11: OptimalX Bi-Directional Knowledge & Corpus Integration (Desktop + Mobile Sync)
**Goal:** Enable EidosBot to search, read, create, and propose edits to personal notes and knowledge bases created in OptimalX (synced from Android Mobile over Tailscale mesh and Desktop), backed by local SQLite WAL, live semantic search, and human-in-the-loop Diff Review.

*(See full specification in [`docs/OPTIMALX_INTEGRATION.md`](OPTIMALX_INTEGRATION.md))*

#### Deliverables & Tasks:
1. **Core Read & Search Engine (`eidos/integrations/optimalx.py`):**
   - Connect to local Express server on `http://127.0.0.1:7373` with Bearer auth loaded from `~/.config/optimalx-desktop/config.json`.
   - **Tier 1 (Online):** Query `POST /api/v1/llm/search` for cosine-ranked snippet matches against pre-computed `nomic-embed-text` chunks.
   - **Tier 2 (Offline / Cold-Start):** Fall back to direct SQLite queries on `file:/home/tuf/OptimalX/data.db?mode=ro` across `notes`, `subfolders`, and `parent_folders`.
   - Enforce strict privacy filters: exclude soft-deleted notes (`deleted_at IS NOT NULL`) and private notes (`ai_blind = 1`).
2. **NoteX Corpus & Hierarchy Navigation:**
   - Implement `list_folders(parent_folder_id=None)` to map folder trees and resolve subfolder IDs.
   - Implement `read_note(subfolder_id, title)` returning full markdown, word count, summary, and `ai_locked` status.
   - Implement `read_notex_doc(subfolder_id, doc_path)` reading long-form project markdown from `~/OptimalX/notex/<globalId>/`.
3. **Safe Mutation & Diff Review Applier:**
   - Auto-apply direct writes when creating new notes or writing to empty notes (`shouldAutoApply == true`), generating SHA-256 `content_hash`, UUID `global_id`, and creating a checkpoint in `content_checkpoints`.
   - Queue modifications to existing non-empty notes into `pending_change_items` (`status = 'pending'`) for human-in-the-loop review in the OptimalX desktop UI.
   - Support direct append mode for machine logging and batch agent outputs.
   - Preserve complete sync protocol compatibility so OptimalX Mobile pulls changes seamlessly over Tailscale WireGuard mesh.
4. **Agent Tool Suite & Cognitive Routing:**
   - Register tool schemas in `eidos/core/context.py`: `optimalx_search_notes`, `optimalx_read_note`, `optimalx_list_folders`, `optimalx_write_note`, `optimalx_read_notex_doc`.
   - Route dispatches in `eidos/core/loop.py` to `self.optimalx`.
   - Update `eidos/safety/risk_classifier.py`: Level 0 (Passive / Auto-allowed) for reads/search, Level 1 (Safe Action) for writes/proposals.
5. **Episodic Memory RAG & Batch Job Integration:**
   - Connect `<recall>` thought streams (`eidos/memory/recall_stream.py`) to query personal OptimalX notes when user projects are referenced.
   - Allow batch job processors (`eidos/jobs/processor.py`) to pull reference notes during multi-item runs.
6. **Comprehensive Verification & Test Suite:**
   - Unit and integration tests in `tests/test_optimalx_notes.py`:
     - Live and mocked HTTP semantic search on port 7373.
     - Direct read-only SQLite WAL query verification against `data.db`.
     - `ai_blind` privacy enforcement verification.
     - New note direct-write vs existing note Diff Review queuing verification.
     - Content hash cryptographic validity (SHA-256) matching mobile sync expectations.

---

### Phase 12: Dynamic Tool Scoping & Lean System Prompt Architecture
**Goal:** Replace the monolithic 1,050-line system prompt and 49-tool global catalog with a minimal ~120-line base kernel and just-in-time (JIT) tool scoping via living `SKILL.md` documents, reducing token overhead by ~90%, preventing the 15-hop budget from being starved, and eliminating attention dilution/OptimalX hallucination biases.

*(See full specification in [`docs/SKILLS_AND_JOBS.md`](SKILLS_AND_JOBS.md))*

#### Deliverables & Tasks:
1. [x] **Decoupled Tool Registry (`eidos/core/tool_registry.py`):**
   - Partition global tool catalog into `BASE_TOOLS` (6 universal tools: `execute_shell`, `load_skill`, `close_skill`, `get_current_time`, `finish_task`, `share_file`) and `SPECIALIZED_TOOLS` (on-demand functions). `share_file` stays in the base set so the web app, including a phone, can receive files without loading a skill.
   - Purge redundant deadweight tools (`optimalx_reject_pending_review`, `optimalx_accept_pending_review`, `optimalx_list_pending_reviews`).
2. [x] **Built-in System Skills Catalog (`eidos/skills/builtin/`):**
   - Package specialized toolsets into self-contained skills with `tools_required: [...]` frontmatter:
     - `desktop-gui`: Screen capture, visual grounding, mouse/keyboard injection.
     - `browser-automation`: Headless Playwright, DOM extraction, SPA interactions.
     - `optimalx`: Notes, search, folders, NoteX doc reader, and safe writes.
     - `batch-runner`: Queue initialization, stepping, status checks, and summaries.
     - `web-research`: Search, fetch, download, and desktop browser launching.
     - `user-memory`: User profile facts and workflow rules.
     - `skills-manager`: Authoring and updating custom `SKILL.md` procedures.
3. [x] **Lean Base System Prompt (`eidos/core/context.py`):**
   - Shrink prompt from ~1,050 lines to ~50 lines.
   - Eliminate all domain-specific instruction bloat (including Principle 15 OptimalX bias and coordinate math lectures).
   - Retain only core environment snapshot, episodic/profile blocks, and universal behavioral principles.
4. [x] **Active Skill Scope Engine (`eidos/core/loop.py`):**
   - Maintain `active_skill` state across loop steps.
   - Assemble model payload dynamically on each step (`BASE_TOOLS + active_skill.tools`).
   - Append active `SKILL.md` procedure to system prompt while active; drop both upon `close_skill()` or `finish_task()`.
5. [x] **Hop Budget & Shell Determinism Protection:**
   - Reinforce shell-first scripting via `execute_shell` for multi-step data tasks to finish deterministically.
6. [x] **Regression & Safety Verification:**
   - Add unit tests (`tests/test_tool_registry.py`, `tests/test_skills.py`, `tests/test_context.py`, `tests/test_dynamic_scoping.py`) with zero system overload and isolated test execution.

---

## 🚦 Recommended Next Steps

With the core subsystems, processor, background batch runner, and ranking leaderboard generator fully deployed and verified, the status of the pipeline milestones is:

1. [x] **Implement Fresh-Context Sub-Agent Processor (`eidos/jobs/processor.py`):**
   - Created `SkillJobProcessor` to execute item-level research workflows.
   - Wired `WebResearchEngine` (Brave Search) and `PlaywrightManager` (Google Patents / web DOM).
   - Prompted `ModelRouter` to score the 4 dimensions defined in `invention-evaluator/SKILL.md` (Feasibility, Demand, Novelty, Margins).
   - Standardized markdown portfolios (`<output_dir>/portfolios/<id>.md`) written inside sandboxed workspace.
2. [x] **Build Continuous Batch Execution Loop & Controls (`eidos/jobs/runner.py`):**
   - Added `run_job()` and background thread runner with pacing delays (`pause_between_items_sec`) to protect API limits and GPU thermals.
   - Wired `run_batch_job` tool into `eidos/core/context.py` and `eidos/core/loop.py`.
   - Maintained responsiveness to emergency kill switch (`<ctrl>+<alt>+<esc>`), `/pause`, `/resume`, and `/kill`.
3. [x] **Build Ranking Leaderboard Generator (`eidos/jobs/summary.py`):**
   - Scans `queue.json` upon completion (or on demand) to compile `RANKING_SUMMARY.md`, `RANKING_SUMMARY.csv`, and `RANKING_SUMMARY.json`.
   - Categorizes evaluated inventions into Tier 1 (80–100), Tier 2 (60–79), and Tier 3 (<60), with a dedicated Needs Review section.
   - Exposed via `generate_batch_summary` tool and Telegram companion command `/summary`.
4. [x] **Implement OptimalX Bi-Directional Note & Knowledge Bridge (Phase 11):**
   - [x] **Phase 11.1:** Build dual-tier read & search in `eidos/integrations/optimalx.py` (live port 7373 HTTP semantic vector search + direct SQLite WAL fallback on `~/OptimalX/data.db`).
   - [x] **Phase 11.2:** Add folder hierarchy listing and NoteX markdown document reader.
   - [x] **Phase 11.3:** Build safe mutation engine (direct write with SHA-256 and checkpoints for empty notes, queued `pending_change_items` Diff Review for existing notes, checkpoint pruning, accept/reject operations).
   - [x] **Phase 11.4:** Register tools in `eidos/core/context.py` (`optimalx_search_notes`, `optimalx_read_note`, `optimalx_list_folders`, `optimalx_write_note`, `optimalx_read_notex_doc`, `optimalx_list_notex_docs`, `optimalx_list_pending_reviews`, `optimalx_accept_pending_review`, `optimalx_reject_pending_review`), route cognitive loop dispatches in `eidos/core/loop.py`, and classify risk levels in `eidos/safety/risk_classifier.py`.
   - [x] **Phase 11.5:** Episodic Memory RAG & Batch Job Integration (extend `<recall>` streams in `recall_stream.py` and `jobs/processor.py` to cross-reference personal notes without database duplication).
   - [x] **Phase 11.6:** End-to-end integration and regression testing across the entire pipeline.
5. [ ] **Ingest Invention List & Launch Run:**
   - Place target invention list (`inventions.csv` or `inventions.json`) in `~/EidosWorkspace/`.
   - Initialize and launch batch job via GUI chat, CLI, or Telegram companion.
   - Cross-reference prior OptimalX invention notes during batch scoring.
   - Monitor live milestone telemetry and review generated portfolios.
