# EidosBot Comprehensive Implementation Guide
## Sandbox, Headless Browser, Skills/Jobs Runner & Telegram Gateway

This guide provides an end-to-end blueprint for engineering, testing, and rolling out the four core enhancements to EidosBot:
1. **Kernel-Level Filesystem Sandboxing (`bwrap`)**
2. **Headless Browser Automation (Playwright / CDP)**
3. **Skills & Collaborative Batch Job Queue Runner (`SKILL.md` + `queue.json`)**

**Old system (item 3).** The job system this guide builds is the batch queue plus skills.

**Updated job concept.** The steps below still build that queue and those skills. A job, in the current model, is the project in [PROJECT_JOBS.md](PROJECT_JOBS.md). The queue is a component of that project. Outside apps attach as integrations and bring their tools from an MCP server. This guide does not build the MCP client.

**Updated job concept (groups).** Jobs are filed under group folders (`jobs/Gmail/`, `jobs/Coding/`). `group.yaml` binds the skill and the server for the jobs inside. See [PROJECT_JOBS.md](PROJECT_JOBS.md) section 14.
4. **Remote Telegram Gateway (Human-in-the-Loop Approvals & Progress Streaming)**

---

## 🗺️ Master Dependency & Component Map

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 EIDOSBOT ENHANCEMENT MAP                               │
├─────────────────────────┬───────────────────────────────┬──────────────────────────────┤
│ Subsystem               │ New / Modified Files          │ Core Dependencies            │
├─────────────────────────┼───────────────────────────────┼──────────────────────────────┤
│ 1. Sandboxing           │ • eidos/sandbox/bwrap.py      │ bubblewrap (system package)  │
│                         │ • eidos/actions/shell.py      │ Linux user namespaces        │
│                         │ • tests/test_sandbox.py       │                              │
├─────────────────────────┼───────────────────────────────┼──────────────────────────────┤
│ 2. Browser Engine       │ • eidos/browser/manager.py    │ playwright                   │
│                         │ • eidos/core/context.py       │ trafilatura                  │
│                         │ • eidos/core/loop.py          │ chromium binary              │
│                         │ • tests/test_browser.py       │                              │
├─────────────────────────┼───────────────────────────────┼──────────────────────────────┤
│ 3. Skills & Job Runner  │ • eidos/skills/manager.py     │ pyyaml, pydantic             │
│    & Research Pipeline  │ • eidos/jobs/runner.py        │ json, asyncio, threading     │
│                         │ • eidos/jobs/processor.py     │ brave search api, playwright │
│                         │ • eidos/jobs/summary.py       │                              │
│                         │ • ~/.eidos/skills/*/SKILL.md  │                              │
│                         │ • tests/test_jobs.py          │                              │
├─────────────────────────┼───────────────────────────────┼──────────────────────────────┤
│ 4. Telegram Gateway     │ • eidos/gateway/telegram.py   │ python-telegram-bot          │
│                         │ • eidos/safety/gatekeeper.py  │ asyncio, threading           │
│                         │ • tests/test_telegram.py      │                              │
└─────────────────────────┴───────────────────────────────┴──────────────────────────────┘
```

---

## 📦 Step 0: Dependency Installation

### 1. System Packages (Linux Mint / Ubuntu)
```bash
sudo apt update
sudo apt install -y bubblewrap
```

### 2. Python Requirements
Add the following to `requirements.txt`:
```text
playwright>=1.40.0
trafilatura>=1.6.0
python-telegram-bot>=21.0
```

Install and download the headless Chromium binary:
```bash
pip install -r requirements.txt
playwright install chromium
```

---

## 🛡️ Step 1: Kernel Sandboxing Implementation (`eidos/sandbox/`)

### Objectives
- Isolate shell commands by default to prevent accidental file deletion or system corruption.
- Mount root `/` and user home `~` as read-only.
- Mount only the active workspace (e.g., `~/EidosWorkspace` or job directory) as read-write.
- Eliminate prompt fatigue for sandboxed tasks.

### Tasks:
1. **Create `eidos/sandbox/bwrap.py`:**
   - Implement `BubblewrapSandbox` class with `build_command()` and `run()`.
   - Auto-detect if `bwrap` is installed via `shutil.which("bwrap")`.
   - Handle directory creation for designated writeable paths.
2. **Update `eidos/actions/shell.py`:**
   - Add parameter `sandboxed: bool = True` to `ShellExecutor.execute()`.
   - If `sandboxed=True` and sandbox is enabled in configuration, route execution through `BubblewrapSandbox`.
   - Bypasses manual Gatekeeper prompt for sandboxed workspace operations while logging execution to `~/.eidos/audit.log`.
3. **Configuration in `config.yaml`:**
   ```yaml
   sandbox:
     enabled: true
     engine: "bwrap"
     default_workspace: "~/EidosWorkspace"
     network_access: true
   ```
4. **Verification Test (`tests/test_sandbox.py`):**
   - Test 1: Write file inside workspace (Expect: Success).
   - Test 2: Attempt `rm` or write to `/tmp_test` outside workspace (Expect: Kernel `Read-only file system` error).

---

## 🌐 Step 2: Headless Playwright Browser (`eidos/browser/`)

### Objectives
- Completely replace fragile screen capture + mouse clicking for web tasks.
- Extract clean Markdown from dynamic Single Page Applications (SPAs) and patent engines.
- Run silently in the background without opening visible windows or stealing mouse focus.

### Tasks:
1. **Create `eidos/browser/manager.py`:**
   - Implement `PlaywrightManager` singleton managing background Chromium instance.
   - Methods: `navigate(url)`, `extract_markdown(selector)`, `click_element(selector)`, `fill_input(selector, text)`, `take_page_screenshot()`, `close()`.
   - Use `trafilatura` to clean extracted HTML into dense, token-efficient Markdown.
2. **Expose Tools in `eidos/core/context.py`:**
   - Register `browser_open`, `browser_extract`, `browser_click`, `browser_type`, `browser_screenshot`, `browser_close` in `EIDOS_TOOLS`.
3. **Wire Tool Dispatcher in `eidos/core/loop.py`:**
   - Connect incoming tool calls to `PlaywrightManager` methods.
4. **Verification Test (`tests/test_browser.py`):**
   - Test navigating to a Wikipedia or Google Patents search page.
   - Verify extracted text returns valid markdown with non-empty content.

---

## 🛠️ Step 3: Domain-Agnostic Skills & Batch Job Runner (`eidos/skills/` & `eidos/jobs/`)

### Objectives
- Enable human and EidosBot to co-author living `SKILL.md` rubrics and procedures for **any** arbitrary task.
- Build a persistent, **100% domain-agnostic** state-machine batch runner (`queue.json`) capable of executing multi-item projects (e.g., 300+ items) reliably without context window bloat or amnesia.
- Ensure all schemas, Pydantic models, and runner classes use generic structures (`input: Dict[str, Any]`, `result: Dict[str, Any]`), never hardcoding domain-specific fields.

### Tasks:
1. **Create `eidos/skills/manager.py`:**
   - Manage discovery and parsing of `~/.eidos/skills/<skill-name>/SKILL.md`.
   - Methods: `list_skills()`, `load_skill(name)`, `create_skill(name, content)`, `update_skill(name, diff)`.
2. **Create `eidos/jobs/runner.py`:**
   - Implement domain-agnostic `BatchJobRunner` class managing `queue.json`.
   - Methods: `initialize_job(name, skill, input_list, output_dir)`, `step_next()`, `get_status()`, `pause()`, `resume()`.
   - Ensures each item runs with a fresh context window, injecting only the specified `SKILL.md` and current item payload.
3. **Verification Test (`tests/test_jobs.py`):**
   - Initialize a mock job with 3 generic test items (e.g., summarizing 3 test notes).
   - Step through execution and verify `queue.json` transitions from `pending` $\rightarrow$ `completed`.
   - Verify artifacts and result summaries are recorded generically in `queue.json`.
4. **Author Example Case Study Skill (Completed):**
   - Created `~/.eidos/skills/invention-evaluator/SKILL.md` defining the 4-dimension commercial viability rubric, patent search steps, and portfolio template.
5. **Build Fresh-Context Sub-Agent Processor (`eidos/jobs/processor.py`):**
   - Implement `SkillJobProcessor` to execute individual items with pristine reasoning context.
   - Integrate `WebResearchEngine` (Brave Search API) and `PlaywrightManager` (Google Patents headless Chromium extraction).
   - Query `ModelRouter` with structured scoring prompt returning JSON evaluation for Dimensions A, B, C, and D with justifications.
   - Format and write standardized markdown portfolio files (`<output_dir>/portfolios/<item_id>.md`).
6. **Autonomous Background Batch Execution Loop (`eidos/jobs/runner.py`):**
   - Add `run_job()` and daemon thread background runner to `BatchJobRunner`.
   - Implement cool-down pacing delay (`pause_between_items_sec`, default 2.0s) to protect API limits and GPU thermals.
   - Expose `run_batch_job` tool in `eidos/core/context.py` and wire dispatcher in `eidos/core/loop.py`.
   - Check emergency kill switch (`<ctrl>+<alt>+<esc>`), `/pause`, and `/kill` between each step.
7. **Build Leaderboard Aggregator (`eidos/jobs/summary.py`):**
   - Compile `RANKING_SUMMARY.md` and `RANKING_SUMMARY.csv` upon batch completion.
   - Categorize evaluated inventions into Tier 1 (80–100), Tier 2 (60–79), and Tier 3 (<60).

---

## 📱 Step 4: Telegram Gateway & Remote Approvals (`eidos/gateway/`)

### Objectives
- Enable mobile delegation, status monitoring, and question-answering when away from the PC.
- Provide interactive inline buttons (`[Approve]`, `[Reject]`, `[Kill]`) for host-level or destructive commands.
- Push batch job milestone summaries and clarifications directly to your phone.

### Tasks:
1. **Create `eidos/gateway/telegram_bot.py`:**
   - Use `python-telegram-bot` with long-polling.
   - Security: Restrict access strictly to `allowed_user_ids` from `config.yaml`.
   - Command handlers: `/status`, `/jobs`, `/pause`, `/resume`, `/kill`.
2. **Gatekeeper Integration (`eidos/safety/gatekeeper.py`):**
   - When a Level 2 or Level 3 action triggers confirmation and remote mode is active (or GUI is unavailable):
     - Gateway posts message with `InlineKeyboardMarkup` buttons.
     - Awaits user button tap asynchronously.
     - Passes approval or denial back to the agent loop.
3. **Milestone Progress Pushes:**
   - Hook `BatchJobRunner` into the Telegram Gateway to broadcast progress every $N$ items (e.g., item 10, 20, 30...).
4. **Verification Test (`tests/test_telegram.py`):**
   - Test user ID filtering (reject unauthorized IDs).
   - Test callback query parsing and Gatekeeper unblocking.

---

## 🌐 Step 5: Mobile PWA Gateway & Tailscale Private Mesh (`web/` & `eidos/gateway/server.py`)

### Objectives
- Build an installable Progressive Web App (PWA) running on both Android (via Chrome WebAPK) and iPhone (via Safari Home Screen).
- Connect securely across cellular and Wi-Fi networks via **Tailscale** WireGuard private mesh without open router ports.
- Deliver real-time typewriter token streaming and collapsible `<thought>` / `<recall>` reasoning accordions.
- Provide interactive Level 2/3 safety approval cards with syntax-highlighted commands, affected directories, and 1-tap Approve / Always Allow / Reject / Kill actions.
- Mirror desktop screenshots in real time with viewport zoom and tactile emergency stop controls.

### Tasks:
1. **Frontend Scaffolding (`web/`):**
   - Initialize Vite + React + TypeScript + Tailwind CSS project in `web/`.
   - Configure Dracula theme tokens matching EidosBot PyQt6 desktop HUD.
   - Configure Web App Manifest (`manifest.json`), service worker, and app icons (192px & 512px) for Android & iOS standalone installation.
2. **Real-time WebSocket & State Layer (`web/src/hooks/useEidosWS.ts`):**
   - Resilient WebSocket connection to `ws://100.x.y.z:8420/ws` with exponential backoff reconnect.
   - Manage real-time message stream, active agent state (`idle`, `thinking`, `executing`, `killed`), and pending safety confirmations.
3. **Interactive UI Components (`web/src/components/`):**
   - `ChatView`: Typewriter token renderer, markdown support, code blocks with copy buttons.
   - `ThoughtAccordion`: Collapsible cards for `<thought>` model deliberation and `<recall>` memory searches.
   - `SafetyModal`: Rich confirmation card for Level 2 shell commands and Level 3 actions.
   - `ScreenViewport`: Live desktop capture mirroring with refresh and zoom controls.
   - `Header`: Dracula status pill and persistent tactile `[🛑 STOP]` emergency kill switch.
4. **Backend Gateway Serving (`eidos/gateway/server.py`):**
   - Update `RemoteGatewayServer` to serve compiled `web/dist/` assets with SPA fallback, retaining embedded HTML dashboard fallback when `web/dist` is not yet built.
   - Expand WebSocket handler to broadcast token streams, thought chunks, and confirmation resolutions.
5. **Multi-Device Installation & Verification:**
   - Linux host Tailscale authentication (`tailscale up`).
   - Android test: Chrome navigate $\rightarrow$ "Install App" (verify WebAPK standalone launch).
   - iPhone test: Safari navigate $\rightarrow$ "Add to Home Screen" (verify iOS standalone launch).

---

## 🔄 Step 6: End-to-End Inventions Project Execution Walkthrough

*(See full technical specification in [`docs/INVENTION_RESEARCH_PIPELINE.md`](INVENTION_RESEARCH_PIPELINE.md))*

Once the sub-agent processor and background runner are wired, the complete 300 Inventions workflow executes autonomously:

### 6.1. Ingesting Invention Concepts
Place your invention list in the workspace (e.g., `~/EidosWorkspace/inventions.csv`):
```csv
id,title,category,description,target_audience
INV-001,Solar Dew Condenser,Cleantech,"Atmospheric water extraction unit powered by portable solar collector with peltier cooling array.",Hikers & off-grid homesteaders
INV-002,Ultrasonic Anti-Fouling Hull Ring,Marine,"Ring-shaped ultrasonic transducer collar preventing barnacle adhesion on boat hulls.",Recreational boat owners & marinas
```

### 6.2. Launching Execution
Launch via CLI, Desktop HUD chat, or Telegram:
```bash
# Via CLI
python3 -m eidos.main --job-init "2026_Inventions" \
                      --skill "invention-evaluator" \
                      --items "~/EidosWorkspace/inventions.csv" \
                      --run
```
Or in Desktop HUD / Telegram chat:
> **User:** *"Eidos, review the invention-evaluator skill. Here is ~/EidosWorkspace/inventions.csv with 300 concepts. Initialize the batch job in ~/EidosWorkspace/jobs/inventions/ and start running in the background."*  
> **Eidos:** *"Initialized job '2026_Inventions' with 300 pending items. Background runner started with 2.0s pacing. I'll push milestone updates to Telegram every 10 items."*

### 6.3. Autonomous Sub-Agent Research & Scoring
For every item in the queue:
1. **Fresh Sub-Agent Session:** Spins up an isolated context window with `invention-evaluator/SKILL.md` injected.
2. **Prior Art Search:** Queries Brave Search API and navigates Google Patents headlessly via Playwright Chromium.
3. **Rubric Scoring:** ModelRouter scores Dimensions A, B, C, and D (0–25 each) with detailed justifications.
4. **Portfolio Generation:** Writes `<output_dir>/portfolios/<item_id>.md`.
5. **Atomic Checkpoint:** Updates `queue.json` immediately.

### 6.4. Remote Telemetry & Human-in-the-Loop Clarification
- Telegram pushes progress every 10 items with average scores and time estimates.
- Ambiguous or malformed concepts transition to `needs_review` and alert the user via Telegram/PWA without halting the queue.

### 6.5. Final Outputs & Ranked Leaderboard
- **300 Portfolios:** Individual markdown files in `<output_dir>/portfolios/`.
- **`RANKING_SUMMARY.md`:** Ranked leaderboard grouping inventions into Tier 1 (80–100), Tier 2 (60–79), and Tier 3 (<60).
- **`RANKING_SUMMARY.csv`:** Structured export for spreadsheet analysis.
