# EidosBot

EidosBot is a local-first desktop co-pilot for Linux on X11. You talk to it from a floating PyQt6 pill on the desktop, a terminal, Telegram, or a phone web app. It plans with a local Ollama model (and can escalate to Chutes or Meta), then acts through a shell, a headless browser, the desktop itself, or an attached app.

It is built to stay on one machine. Screen contents, notes, and workspace files stay local unless you turn on a cloud model or a remote chat channel.

## What you can do with it

**Talk to it.** The default launch is a draggable status pill with an expandable chat. Conversations persist in `~/.eidos/eidos.db` and restore on the next start. Push-to-talk (`Ctrl+Alt+Space`) transcribes speech into the prompt, and replies can be spoken aloud with Kokoro. The same conversation is available from the CLI, Telegram, and the web app on port `8420`.

**Act on the machine.** The agent starts with a small set of tools (shell, skills, jobs, the clock, file sharing). Specialized tools load only when a skill is opened:

| Skill | What it unlocks |
|---|---|
| `desktop-gui` | Screenshots, the active window, and mouse and keyboard input on X11 |
| `browser-automation` | Headless Chromium: open, click, type, extract, screenshot |
| `web-research` | Search, fetch articles, download files, open a URL on the desktop |
| `optimalx` | Search and edit OptimalX notes and NoteX documents |
| `user-memory` | Read and update `~/.eidos/user_profile.yaml` |
| `skills-manager` | Author and revise living `SKILL.md` procedures |
| `batch-runner` | Checkpointed queues for large item lists |
| `teacher` | Short spoken tutoring turns, tracked as a job |

Shell commands that stay inside `~/EidosWorkspace` run in Bubblewrap. The home directory and the rest of the system are mounted read-only there, so workspace writes do not need a confirmation for every command. Host commands and destructive actions still go through the safety gate.

**Remember.** A user profile holds stable facts and workflow rules. Episodic memory stores past task trajectories in a local vector database (`nomic-embed-text` on Ollama) and retrieves them at the start of a related task. After a task, a reflector can write a short critique, and an idle consolidator can merge repeated episodes.

**Work inside a project.** A job is a folder under `~/EidosWorkspace/jobs/` with a short brief, notes, and artifacts. Focus survives the chat turn until you or the agent close it. An outside app attaches as an integration: its tools come from that app's MCP server and are loaded only while the integration is in focus. OptimalX stays a built-in skill because it does not ship an MCP server.

**Reach it away from the desk.**

- **Web app** (`web/`): chat, live reasoning, safety approval cards, shared files, and a desktop screenshot. The Python gateway serves the built app from `web/dist` on port `8420`. Put it on a Tailscale address if you want it on a phone without opening a port.
- **Telegram**: remote prompts, approval buttons, job milestones, and `/kill`. Only user IDs in `allowed_user_ids` can talk to the bot.

## Safety

`Ctrl+Alt+Esc` stops the agent loop, kills child processes it started, and releases held input. Telegram `/kill` does the same when the bot is running.

| Level | Examples | Default |
|---|---|---|
| 0 Passive | Screenshots, window title, directory listings | Allowed |
| 1 UI | Clicks, typing, hotkeys | Allowed. Set `safety.confirm_level_1_ui` to prompt |
| 2 Host shell and MCP | Commands outside the sandbox, MCP tool calls | Allowed. Set `safety.confirm_level_2_shell` to prompt, with an optional “remember this command” allowlist |
| 3 Destructive | `rm`, `sudo`, disk tools, shutdown, mass `chmod` | Always asks. Cannot be added to the allowlist |

Approvals, denials, and executions are appended to `~/.eidos/audit.log`. Profile writes ask for confirmation when `memory.confirm_profile_writes` is true.

Sandboxed shell is a separate path: writes are limited to the configured workspace (and the active job folder), so those commands are allowed without a prompt. A command that must change the rest of the system runs on the host and is classified above.

## Models

`config.yaml` chooses the provider. `models.provider_preference` is `local`, `cloud`, or `auto`.

- **Local:** Ollama at `http://localhost:11434`. The example config uses a reasoning model and a separate vision model. If the reasoning model can see images, the vision model stays unloaded.
- **Cloud:** Chutes (`models.cloud`) or Meta Muse (`models.meta`, key from the config or `MODEL_API_KEY`). Cloud is used when you select it, or when local inference is busy and `models.compute` says to prefer cloud for background work such as the reflector.

The example model names are defaults, not a requirement. Point `reasoning_model` and `vision_model` at whatever you have pulled.

## Requirements

- Linux with an X11 session (developed on Linux Mint). Wayland is not supported for screen capture and input.
- Python 3.10+
- [Ollama](https://ollama.com) running locally, plus the models named in your config (including `nomic-embed-text` if episodic memory is on)
- `bubblewrap` (`bwrap`) for the sandbox. It is part of a normal Linux Mint install.
- Chromium for Playwright, if you use the browser skill
- Optional: a Chutes or Meta API key, a Telegram bot token, and the OptimalX voice environment for speech

Voice reuses the OptimalX voice virtualenv (Kokoro for speech, Parakeet for transcription). See `OptimalX_Voice/voice/SETUP.md`. Desktop chat still works with `voice.enabled: false`.

## Quickstart

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 -m playwright install chromium

cp config.example.yaml config.yaml
# Set models.cloud.api_key or models.meta, and gateway tokens, only if you use them.

python3 -m eidos.main
```

With `DISPLAY` set and no other flags, that opens the pill. `pip install -e .` also installs an `eidos` command that calls the same entrypoint.

The web app is a separate build. The gateway serves it once `web/dist` exists:

```bash
cd web && npm install && npm run build
```

Then start with the gateway enabled (see below) and open `http://<host>:8420`.

## How to launch

```bash
python3 -m eidos.main                         # pill if DISPLAY is set, otherwise a REPL
python3 -m eidos.main --gui                   # force the pill
python3 -m eidos.main --cli                   # REPL. Type exit to leave
python3 -m eidos.main "summarize ~/notes"     # one shot, then exit
python3 -m eidos.main --gui --gateway         # also serve the web app on :8420
python3 -m eidos.main --gui --telegram        # also start the Telegram bot
```

`--gateway` and `--telegram` override `gateway.enabled` and `gateway.telegram.enabled` for that process. Tokens are read from the config, from `EIDOS_GATEWAY_AUTH_TOKEN` and `EIDOS_TELEGRAM_BOT_TOKEN`, or from `~/.eidos/gateway.token` and `~/.eidos/telegram.token`.

Batch queues can run without the pill:

```bash
python3 -m eidos.main --job-init "my-queue" --skill batch-runner --items items.csv
python3 -m eidos.main --job-run
python3 -m eidos.main --job-status
python3 -m eidos.main --job-summary
```

`--steps N` caps a single task. Omit it and the task runs until it finishes, stalls, or hits `agent.safety_step_fuse`.

## Configuration

`config.example.yaml` is the template. On startup, a missing `config.yaml` falls back to that example. Sections you will actually edit:

| Section | Controls |
|---|---|
| `models` | Ollama URLs and model names, Chutes, Meta, and when background work may use cloud |
| `safety` | Kill-switch chord, which levels ask before acting, allowlist and audit paths |
| `sandbox` | Bubblewrap on or off, writable paths, network, memory and time limits |
| `voice` | TTS voice, devices, push-to-talk chord, whether replies are spoken |
| `gateway` | Web bind address, port, auth token, Telegram allowlist and notification toggles |
| `memory` | Profile path, vector store, embedding model, reflector, consolidation |
| `integrations.optimalx` | OptimalX CDP port and launch command |
| `skills` / `jobs` | Where skill folders and job folders live |

State that is not in the repo lives under `~/.eidos/` (conversations, profile, allowlist, audit log, episodic database, tokens) and `~/EidosWorkspace/` (jobs, notes, artifacts, user skills).

## Layout

```text
EidosBot/
├── config.example.yaml       # Config template
├── requirements.txt
├── pyproject.toml            # Package metadata and the `eidos` script
├── eidos/
│   ├── main.py               # GUI, CLI, gateway, and batch-job launcher
│   ├── core/                 # Agent loop, tool registry, prompt context
│   ├── models/               # Ollama, Chutes, Meta, and the router
│   ├── safety/               # Kill switch, risk levels, allowlist, audit log
│   ├── sandbox/              # Bubblewrap shell
│   ├── perception/           # X11 capture, window info, visual grounding
│   ├── actions/              # Shell and mouse/keyboard
│   ├── browser/              # Playwright
│   ├── skills/               # SKILL.md loader and built-in skills
│   ├── jobs/                 # Project focus and batch queues
│   ├── memory/               # Profile, episodes, reflector, consolidation
│   ├── voice/                # Kokoro playback and speech-to-text
│   ├── integrations/         # OptimalX, web research, MCP client
│   ├── gateway/              # Web app server and Telegram bot
│   └── ui/                   # Pill, chat, HUD, settings, dialogs
├── web/                      # React PWA. Build output is web/dist
├── OptimalX_Voice/           # Shared speech environment
└── docs/                     # Subsystem notes
```

## More detail

The docs below match the running subsystems. A few older design notes still describe earlier file names and a step-by-step build plan.

- [Architecture](docs/ARCHITECTURE.md) — sense, reason, confirm, act
- [Features](docs/FEATURES.md) — safety levels, pill, and HUD
- [Sandboxing](docs/SANDBOXING.md) — Bubblewrap mounts and host vs sandbox shell
- [Skills and batch jobs](docs/SKILLS_AND_JOBS.md) — skill loading and `queue.json`
- [Project jobs](docs/PROJECT_JOBS.md) — briefs, focus, groups, and MCP integrations
- [Episodic memory](docs/EPISODIC_MEMORY.md)
- [Playwright](docs/PLAYWRIGHT_AUTOMATION.md)
- [OptimalX](docs/OPTIMALX_INTEGRATION.md)
- [Telegram](docs/TELEGRAM_GATEWAY.md)
- [Mobile web app](docs/MOBILE_PWA_GATEWAY.md) and [Tailscale](docs/TAILSCALE_AND_EIDOS.md)
