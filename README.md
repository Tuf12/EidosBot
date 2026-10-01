# EidosBot 🤖🖥️

**EidosBot** is a local-first, OS-level autonomous desktop co-pilot engineered for Linux Mint (X11). It combines local multi-model AI (via Ollama on an RTX 5060 Ti 16GB) with high-speed cloud acceleration (via Chutes AI), featuring a transparent visual HUD overlay, granular safety guardrails, and a hybrid 3-tier execution model.

---

## 🌟 Core Highlights

- **Local-First & Privacy-Centric:** Powered locally by Ollama using quantized models (`gemma4-26b:iq4`, `gemma4-12b`, `gpt-oss:20b`) with fast local vision and tool routing.
- **Cloud Escalation via Chutes AI:** Seamlessly offloads heavy visual grounding (e.g., Qwen 2.5-VL 72B) or deep multi-step reasoning when local models require extra precision.
- **Hybrid 3-Tier Execution:**
  1. *Tier 1 (CLI & Direct APIs):* Instant, deterministic execution via bash, Python, and Chrome DevTools Protocol (CDP).
  2. *Tier 2 (Accessibility & DOM Inspection):* Exact element coordinates via Linux AT-SPI and Electron debugging ports.
  3. *Tier 3 (Visual Grounding & Synthetic Input):* Screen capture, Set-of-Mark grounding, and mouse/keyboard injection as a universal fallback.
- **Granular Safety & Confirmation Engine:**
  - **Level 0 (Passive):** Window inspection, clipboard read, directory read (auto-allowed).
  - **Level 1 (UI Navigation):** Mouse movements, typing, clicks (toggleable confirmation).
  - **Level 2 (Shell & System Changes):** Shell execution requires explicit confirmation by default. Features a **"Don't ask again for this command"** checkbox on approval, and a dedicated **GUI command management list** to review, re-enable, or revoke permissions.
  - **Level 3 (Destructive):** File deletions (`rm`), system config changes, shutdowns (hard-locked confirmation; never bypassable).
  - **Global Hardware Kill Switch:** `Ctrl + Alt + Esc` instantly halts all agent loops and releases input control.
- **PyQt6 Click-Through HUD:** A transparent overlay that draws target bounding boxes/reticles on elements before clicking, accompanied by a floating status pill showing what the agent is currently doing.
- **Target Integrations:**
  - **OptimalX Desktop 1.0:** Direct Electron DOM/CDP integration for ultra-fast, robust interaction.
  - **General Desktop & Web Research:** Autonomous browser automation and cross-app workflows.

---

## 📂 Documentation

Detailed specifications and architectural guides are available in the [`docs/`](./docs) folder:

- 📐 **[Design and Structure (docs/ARCHITECTURE.md)](./docs/ARCHITECTURE.md)**: Deep dive into the Sense → Reason → Act loop, modular subsystem architecture, X11 integration, and data flow.
- ⚡ **[Feature Specifications (docs/FEATURES.md)](./docs/FEATURES.md)**: Full breakdown of safety tiers, the dynamic confirmation/allowlist manager, HUD mechanics, and app-specific bridges.
- 🗺️ **[Roadmap & Implementation Plan (docs/ROADMAP.md)](./docs/ROADMAP.md)**: Step-by-step phased milestones from MVP CLI loop to full PyQt6 HUD and remote access.

---

## 🖥️ System & Hardware Baseline

| Component | Specification |
|---|---|
| **OS** | Linux Mint (Cinnamon Desktop) |
| **Display Server** | X11 (`:0`, X11 SHM / `mss` screen grab, `pynput` / `pyautogui` input injection) |
| **GPU** | NVIDIA GeForce RTX 5060 Ti (16 GB VRAM) |
| **System RAM** | 32 GB DDR4/DDR5 |
| **Local LLM Engine** | Ollama (`gemma4-26b:iq4`, `gemma4-12b`, `gpt-oss:20b`, `mistral-small3.2:24b`) |
| **Cloud Acceleration** | Chutes AI (Serverless Qwen 2.5-VL / DeepSeek) |
| **UI Framework** | PyQt6 (Transparent Frameless Overlay + Control Floating Pill) |

---

## 🏗️ Repository Structure

```text
EidosBot/
├── README.md                 # Project overview and quickstart
├── config.yaml               # System configuration & model endpoints
├── requirements.txt          # Python dependencies
├── docs/                     # Technical specifications
│   ├── ARCHITECTURE.md       # Subsystem design & data flow
│   ├── FEATURES.md           # Safety levels, allowlist, HUD & integrations
│   └── ROADMAP.md            # Phased implementation plan
├── eidos/                    # Main package
│   ├── core/                 # Agent loop, state machine, context manager
│   ├── safety/               # Kill switch, confirmation gatekeeper, command audit
│   ├── perception/           # Screen capture, X11 window info, visual grounding
│   ├── actions/              # Mouse, keyboard, and shell execution engine
│   ├── models/               # Ollama and Chutes AI API connectors
│   ├── integrations/         # OptimalX Desktop & web research bridges
│   └── ui/                   # PyQt6 HUD overlay, confirmation dialogs, settings UI
└── tests/                    # Unit and integration test suite
```

---

## 🚀 Quickstart (Coming in Phase 1)

```bash
# 1. Clone repository
git clone https://github.com/your-username/EidosBot.git
cd EidosBot

# 2. Set up virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure API keys and settings
cp config.example.yaml config.yaml

# 5. Run EidosBot
python3 -m eidos.main
```

---

## 🔒 Safety First

EidosBot is engineered with guardrails at the operating system level:
- Hit **`Ctrl + Alt + Esc`** at any time to immediately kill active operations.
- Destructive commands are classified and blocked pending manual user consent.
- All executed shell commands and approvals are logged in `~/.eidos/audit.log`.
