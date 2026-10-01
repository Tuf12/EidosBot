# EidosBot Comprehensive Implementation Guide
## Sandbox, Headless Browser, Skills/Jobs Runner & Telegram Gateway

This guide provides an end-to-end blueprint for engineering, testing, and rolling out the four core enhancements to EidosBot:
1. **Kernel-Level Filesystem Sandboxing (`bwrap`)**
2. **Headless Browser Automation (Playwright / CDP)**
3. **Skills & Collaborative Batch Job Queue Runner (`SKILL.md` + `queue.json`)**
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
│                         │ • eidos/jobs/runner.py        │ json, asyncio                │
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
4. **Author Example Case Study Skill (Optional):**
   - Provide `invention-evaluator/SKILL.md` as a user-level demonstration skill, demonstrating how rubrics and templates are passed at runtime without touching system code.

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

## 🔄 Step 5: End-to-End Inventions Project Execution Walkthrough

Once all steps are implemented, the 300 Inventions workflow proceeds seamlessly:

```bash
# 1. Start EidosBot with Sandbox & Telegram enabled
python3 -m eidos.main --with-telegram

# 2. In chat (or via Telegram):
User: "Eidos, let's review the invention-evaluator skill. Here is my inventions.csv with 300 concepts. Initialize the batch job in ~/Documents/Inventions/."

# 3. EidosBot verifies SKILL.md and initializes queue:
Eidos: "Initialized job '2026_inventions' with 300 pending items. Sandboxed workspace set to ~/Documents/Inventions/. Starting execution."

# 4. User steps away from PC:
- EidosBot runs in the bwrap sandbox.
- It browses Google Patents and the web headlessly via Playwright.
- Writes portfolios to ~/Documents/Inventions/portfolios/inv_001.md...
- Sends Telegram updates every 10 items.
- Asks clarifying questions via Telegram buttons when concepts are ambiguous.

# 5. Final Output:
- 300 individual markdown portfolios.
- RANKING_SUMMARY.md sorted by composite Viability Score (100 to 0).
- Zero accidental file deletions, zero prompt fatigue, zero context crashes.
```
