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
| **Level 2** | **Shell & State Modification** | Shell execution (`mkdir`, `curl`, `ffmpeg`, `python3 script.py`, launching applications) | **Strict Confirmation Required** | **Yes (Per-command allowlist with checkbox)** |
| **Level 3** | **Destructive / High-Risk** | `rm`, `rmdir`, `dd`, `mkfs`, `shutdown`, `reboot`, editing `/etc/*`, `chmod -R`, `chown`, killing system PIDs | **Hard-Locked Confirmation** | **NO. Never bypassable.** |

---

### 1.2. Level 2 Shell Confirmation & Dynamic Allowlist System

To balance high security with workflow convenience, Level 2 commands start with strict confirmation on every command, with a built-in mechanism to remember commands you trust.

#### The Approval Flow
1. The agent decides it needs to run a shell command: e.g., `du -sh ~/Downloads`.
2. The agent pauses the loop and prompts the user via the HUD Confirmation Dialog:
   ```text
   ┌────────────────────────────────────────────────────────┐
   │ ⚠️  EIDOSBOT SHELL COMMAND REQUEST                      │
   │                                                        │
   │ Command:  du -sh ~/Downloads                             │
   │ Directory: /home/user/Documents                          │
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
│ Search: [ du                                           ]                     │
├───────────────────────┬──────────────┬─────────────┬───────────┬─────────────┤
│ Command               │ Added On     │ Last Used   │ Run Count │ Action      │
├───────────────────────┼──────────────┼─────────────┼───────────┼─────────────┤
│ du -sh ~/Downloads    │ 2026-09-06   │ 10 mins ago │ 14        │ [ Revoke ]  │
│ curl -sI example.com  │ 2026-09-06   │ 2 mins ago  │ 6         │ [ Revoke ]  │
│ npm run build         │ 2026-09-05   │ Yesterday   │ 3         │ [ Revoke ]  │
│ ffmpeg -i clip.mp4    │ 2026-09-04   │ 2 days ago  │ 22        │ [ Revoke ]  │
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

### 1.5. Kernel-Enforced Filesystem Sandboxing (`bwrap`)

*(Detailed specifications in [SANDBOXING.md](SANDBOXING.md))*

To eliminate "prompt fatigue" from endless allowlist confirmation popups while maintaining absolute security, EidosBot integrates Linux **Bubblewrap (`bwrap`)**:
- **Kernel-Level Read-Only Mounts:** Root (`/`), system directories (`/etc`, `/usr`), and the user home directory (`~`) are mounted strictly read-only.
- **Dedicated Writeable Workspace:** Only designated task folders (e.g., `~/EidosWorkspace` or active batch job folders) are mounted read-write (`--bind`).
- **Elimination of Allowlist Fatigue:** Shell commands executed inside the sandbox are **auto-allowed** without interrupting the user, because even a rogue `rm -rf /` or recursive deletion physically fails at the kernel VFS layer with `Read-only file system`.
- **Targeted Host Mode:** Only actions requiring actual host system changes (e.g., system package installs or root configuration) trigger the Gatekeeper confirmation dialog or Telegram approval card.

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

### 2.2. Floating Status Pill & Expandable Chat UI (`StatusPill` & `ChatHistoryWidget`)
A compact, draggable HUD widget (positioned by default in the top-right corner of the desktop) with a seamless, collapsible rich-text Chat Interface:
- **Expandable / Collapsible Chat View:**
  - **Expand Button (`💬` / `▲`) & Shortcut (`Ctrl+E`):** Instantly toggle between the ultra-compact floating desktop pill (~90px) and a full-featured Dracula-themed floating Chat UI (~600px).
  - **Auto-Expand on Prompt/Response:** When entering a prompt or receiving an assistant response, the view expands to clearly present the conversational response.
  - **Unread Badge (`💬 (N)`):** If collapsed when a response or task completes, shows an unread indicator so text responses are never missed.
  - **Draggable & Resizable:** Smoothly drag from the header anywhere across displays; resize with the interactive `QSizeGrip` corner.
- **Rich Chat Bubbles & Content Rendering:**
  - **User Messages:** Distinctly styled bubble (`👤 You`) with timestamp.
  - **Assistant Responses:** Dracula markdown formatting (`🤖 EidosBot`) supporting code blocks with language tags, monospace syntax formatting, bold/italics, bulleted lists, and a one-click `📋 Copy` button.
  - **Reasoning / Thought Bubbles:** Collapsible and italicized (`💭 Reasoning`) tracking the model's internal step-by-step thinking.
  - **Tool & Action Cards:** Live log badges (`⚡ Action`) showing command invocations (shell, active window, screenshot) and execution previews.
  - **Multi-Turn Context & Automatic SQLite Persistence:** Conversations and message turns are immediately saved to `~/.eidos/eidos.db`. Upon kill and restart, the last active conversation and all its messages are automatically restored into the chat UI and loop reasoning memory.
  - **➕ New Chat:** Create a fresh conversation thread at any time while keeping past conversations safely stored.
  - **📜 Past Conversations Manager (`ConversationsDialog`):** Full-featured window accessible via the `[📜 Past Chats]` button or context menu to search by topic or keyword, preview message snippets, open/resume any past chat, and delete old threads with confirmation.
  - **Auto-Titling:** Intelligently derives human-readable conversation titles from the initial user prompt.
- **Visual Status Indicator:**
  - 🟢 **Idle / Ready:** Waiting for user command or voice prompt.
  - 🟡 **Thinking:** LLM is reasoning (shows provider and active model).
  - 🔵 **Perceiving:** Capturing screen or querying window tree.
  - 🟠 **Awaiting Approval:** Shell command pending user confirmation.
  - 🟣 **Executing:** Typing, clicking, or running process.
  - 🔴 **Killed / Error:** Stopped via `Ctrl+Alt+Esc` or encountered an exception.
- **Controls on the Pill:**
  - **[ 💬 Expand / ▲ Collapse ]** button (toggles full chat view, `Ctrl+E`).
  - **[ 🛑 Stop / 🔄 Reset ]** button (GUI equivalent of emergency kill switch).
  - **[ 🛡️ Allowlist ]** button (opens the Command Allowlist Manager).
  - **[ ⚙️ Settings ]** button (opens Model & Safety preferences).
  - **[ 🎙️ Push-to-Talk ]** button (voice transcription into prompt bar).

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

### 4.2. Web Research & Browser Bridge
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
- **Complex Multi-Step Planning / Reasoning:** `deepseek-ai/DeepSeek-R1`
  - Escalated when deep logical or multi-stage planning is required before executing a task.
- **Routing Rules:**
  - If screen resolution > 1440p and target element is under 20x20 pixels $\rightarrow$ Escalate visual crop to Chutes.
  - If local model hallucinates invalid tools 2 times consecutively $\rightarrow$ Escalate prompt to Chutes.
  - User can manually toggle `[Force Cloud]` or `[Local Only]` from the Status Pill.

### 5.3. Speech Audio Integration
- **STT (Speech-to-Text):** Local NVIDIA Parakeet TDT 0.6B v2 (sherpa-onnx INT8) for push-to-talk voice commands on CPU.
- **TTS (Text-to-Speech):** Local Kokoro-82M neural TTS with 54 voices on CPU, played progressively.

---

## 6. Dual-Tier Memory & Episodic Learning System

For full design specifications, see [EPISODIC_MEMORY.md](EPISODIC_MEMORY.md).

### 6.1. Separation of Agent Experience vs. User Personalization
- **Tier A (Agent Experiential Memory):** Vector-indexed database of past task goals, thinking chains, executed actions, and post-task reflection summaries. Evaluates tool observations to separate proven forward action patterns from exploratory dead ends (capturing the top 2–3 failure reasons like missing tools or bad flags). Macro RAG queries retrieve past golden paths and cautionary pitfalls to prevent repeating mistakes.
- **Tier B (User Profile Memory):** Human-auditable, atomic fact list stored at `~/.eidos/user_profile.yaml`. Avoids narrative summaries to eliminate hallucination drift. Allows individual viewing, editing, and revocation via GUI.

### 6.2. Strategic Trajectory Logging for LoRA Fine-Tuning
- Append-only dataset recorded at `~/.eidos/trajectories/episodes.jsonl`.
- Captures full environmental state, internal `<thought>` tokens, tool calls, exit codes, and user intervention flags.
- **SFT / LoRA Export:** Automatically converts successful trajectories to ShareGPT/ChatML format for local fine-tuning.
- **DPO / ORPO Export:** Extracts mistake-and-recovery pairs to teach the model to avoid bad decisions.

### 6.3. Conscious Active Recall & Thought-Stream Injection
- **Turn-Start Macro RAG:** Injects top 2 relevant past episodes into context before step 1.
- **Active Recall (`<recall>`):** Model can consciously query its own memory mid-thought (`<recall>search query</recall>`). The streaming reader intercepts the tag, pauses inference, splices the retrieved memory, and resumes generation seamlessly.
- **Identity Scaffolding Phase-Out:** As LoRA fine-tuning cycles bake behavioral principles into neural weights, long system prompts are systematically pruned, allowing identity to emerge from episodic continuity and model weights.

---

## 7. Skills & Collaborative Batch Job Runner

For full design specifications, see [SKILLS_AND_JOBS.md](SKILLS_AND_JOBS.md) and [INVENTION_RESEARCH_PIPELINE.md](INVENTION_RESEARCH_PIPELINE.md).

### 7.1. Living Human & Bot Editable Skills (`SKILL.md`)
- Stored under `~/.eidos/skills/<skill-name>/SKILL.md` or in project workspaces.
- Defines standard operating procedures, evaluation rubrics, output templates, and specialized queries for repeatable tasks.
- Solves the cold-start problem for tasks EidosBot has not done before, allowing the user and bot to co-author and refine guidelines before execution.

### 7.2. State-Machine Batch Job Engine (`queue.json`)
- Designed for large-scale projects (e.g. evaluating 300 invention concepts, batch document analysis, bulk code refactoring).
- Manages an external JSON state queue tracking item statuses (`pending`, `in_progress`, `completed`, `failed`, `needs_review`).
- **Context Isolation:** Runs each item in a clean context window injecting the skill rubric, preventing token saturation or quality degradation over long runs.
- Supports pause, resume, milestone notifications, and ranking summary aggregation.

---

## 8. Headless Browser Automation (Playwright & CDP)

For full design specifications, see [PLAYWRIGHT_AUTOMATION.md](PLAYWRIGHT_AUTOMATION.md).

### 8.1. Deterministic DOM Automation vs. Visual Mouse Clicking
- Runs headless Chromium in the background, completely bypassing the fragile screen-screenshot-and-mouse-clicking loop for web tasks.
- Zero screen clutter or focus stealing; runs silently while the user works on other desktop apps.
- Interacts with full client-side rendered Single Page Applications (SPAs), handles dynamic JavaScript, and overcomes Brave Search API limits.

### 8.2. Tool Suite & Content Extraction
- Provides dedicated browser tools: `browser_open`, `browser_extract`, `browser_click`, `browser_type`, `browser_screenshot`, `browser_close`.
- Converts live DOM trees into dense, token-efficient Markdown using `trafilatura` for instant LLM comprehension.

---

## 9. Telegram Remote Gateway & Mobile Companion

For full design specifications, see [TELEGRAM_GATEWAY.md](TELEGRAM_GATEWAY.md).

### 9.1. Continuous Remote Companion
- Bridges local Ollama intelligence to your mobile phone via the Telegram Bot API using long-polling (no open ports or public URLs required).
- Strict user ID whitelisting prevents unauthorized access.

### 9.2. Interactive Remote Approvals
- When host-level execution or destructive operations require user consent, the gateway sends an interactive Telegram message with inline buttons (`[Approve]`, `[Reject]`, `[Kill]`).
- Tapping a button unblocks the Gatekeeper and resumes agent execution.

### 9.3. Remote Job Management & Ambiguity Handling
- Receive periodic batch job milestone summaries (e.g. every 10 items).
- EidosBot can ask clarifying questions directly on Telegram when encountering ambiguous concepts during unattended runs.
- Remote emergency stop command (`/kill`) instantly halts all running subprocesses and agent loops.

---

## 10. Mobile PWA Gateway & Tailscale Private Mesh

For full design specifications, see [MOBILE_PWA_GATEWAY.md](MOBILE_PWA_GATEWAY.md).

### 10.1. Interactive Progressive Web App (PWA)
- Standalone installable companion on Android (WebAPK) and iOS (Safari Home Screen PWA).
- Built with React 19/18, Vite, TypeScript, and Dracula Tailwind theme tokens.
- Served directly by the local Python gateway server on port 8420.

### 10.2. Real-Time Streaming & Visual Desktop Mirroring
- Full typewriter token streaming over WebSockets without third-party rate limits.
- Collapsible `<thought>` and `<recall>` accordions revealing the agent's internal reasoning.
- Live desktop viewport mirroring with zoom controls and tactile emergency kill switch button.

### 10.3. Tailscale Zero-Trust Mesh
- Encrypted WireGuard private mesh connecting host machine, Android phone, and other devices without open router ports or public IP exposure.

---

## 11. OptimalX Bi-Directional Note & Knowledge Integration

For full design specifications, see [OPTIMALX_INTEGRATION.md](OPTIMALX_INTEGRATION.md).

### 11.1. Synchronized Single Source of Truth
- **Universal Mobile & Desktop Sync:** Notes created on the go in OptimalX Mobile sync over Tailscale WireGuard to OptimalX Desktop.
- **Local SQLite Single Source:** All synchronized notes, folders, and summaries converge in `~/OptimalX/data.db` (SQLite in WAL mode).
- **Direct Offline Access:** EidosBot reads notes via `mode=ro` WAL queries even when OptimalX Desktop is closed.

### 11.2. Dual-Tier Retrieval Engine
- **Tier 1 (Online):** When OptimalX Desktop Express server is running on port 7373, EidosBot queries `POST /api/v1/llm/search` for cosine-ranked snippet matches against pre-computed `nomic-embed-text` chunks.
- **Tier 2 (Offline / Cold-Start):** Direct SQLite WAL queries across `notes`, `subfolders`, and `parent_folders`.
- **NoteX Corpus Reader:** Multi-file project traversal under `~/OptimalX/notex/<globalId>/`.

### 11.3. Human-in-the-Loop Diff Review & Mutation Safety
- **Auto-Apply on Empty Notes:** New notes or blank documents auto-write immediately with SHA-256 `content_hash` and `content_checkpoints`, allowing batch jobs to generate documentation without blocking.
- **Diff Review for Existing Notes:** Modifications to existing non-empty notes queue into `pending_change_items` (`status = 'pending'`), requiring user approval in the OptimalX desktop UI.
- **Mobile Sync Parity:** All writes strictly adhere to `content_hash` and `origin_device_id` conventions so OptimalX Mobile pulls changes seamlessly.
- **Strict Privacy & Immutability:** Notes marked `ai_blind = 1` are completely invisible to EidosBot; notes marked `ai_locked = 1` are strictly read-only.



