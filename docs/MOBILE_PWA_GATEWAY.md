# EidosBot Mobile PWA Gateway & Tailscale Remote Interface Specification

This document defines the architecture, network topology, user experience, data contracts, and implementation plan for the **EidosBot Mobile Progressive Web App (PWA)** and **Tailscale Private Mesh Network**.

---

## 1. System Vision & Purpose

The **EidosBot Mobile PWA Gateway** provides an interactive, platform-agnostic companion interface for mobile devices (Android and iOS/iPhone) and remote computers.

While the existing **Telegram Gateway** (`docs/TELEGRAM_GATEWAY.md`) serves as an ambient notification surface (lock-screen push alerts, batch job milestone updates, and emergency kill switches), the **Mobile PWA Gateway** serves as the **high-bandwidth, interactive workspace**:

1. **Sub-Second Token Streaming:** Full typewriter-style token streaming and live output generation over WebSockets without Telegram rate limits or message size caps.
2. **Transparent Reasoning & Scratchpads:** Progressive rendering of `<thought>` chains and `<recall>` episodic memory lookups in collapsible, unobtrusive UI accordions.
3. **Interactive Safety Interceptions:** High-fidelity Level 2 (Shell) and Level 3 (Destructive) confirmation cards displaying syntax-highlighted commands, affected working directories, and risk explanations with 1-tap Approve, Always Allow, or Reject actions.
4. **Live Desktop Mirroring:** Low-latency viewport mirroring of the Linux Mint desktop with zoomable screenshots and refresh controls.
5. **Platform Independence (Android + iOS):** A single responsive codebase running as an installable standalone app on both Android (via Chrome WebAPK) and iPhone (via Safari Home Screen PWA), with zero app store gatekeepers or manual APK builds.
6. **Zero-Trust Private Mesh:** Secured via **Tailscale** (WireGuard mesh), requiring zero opened router ports, zero public IP exposure, and no paid cloud hosting.

---

## 2. High-Level Architecture & Network Topology

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        EIDOS REMOTE TOPOLOGY                           │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│   📱 ANDROID PHONE                     🍏 IPHONE / IPAD                 │
│   (Chrome -> WebAPK Standalone)        (Safari -> Add to Home Screen)  │
│   IP: 100.x.y.z (Tailscale)            IP: 100.a.b.c (Tailscale)       │
│        │                                    │                          │
│        └──────────────────┬─────────────────┘                          │
│                           │                                            │
│                           ▼ (Encrypted WireGuard Mesh / Tailnet)       │
│                                                                        │
│   🖥️ LINUX MINT DESKTOP (Eidos Host Machine)                           │
│   Tailscale IP: 100.85.20.14 | Hostname: eidos-box:8420                │
│   ┌────────────────────────────────────────────────────────────────┐   │
│   │ Python Gateway Server (`eidos/gateway/server.py`)              │   │
│   │                                                                │   │
│   │ • Static SPA Asset Handler: Serves `web/dist/`                 │   │
│   │ • REST API Endpoints: `/api/prompt`, `/api/screenshot`, etc.   │   │
│   │ • Real-time WebSocket: `/ws` (Tokens, Status, Confirmations)   │   │
│   └───────────────────────────────┬────────────────────────────────┘   │
│                                   │                                    │
│                                   ▼                                    │
│   ⚙️ EIDOS CORE AGENT ENGINE (`eidos/core/loop.py`)                    │
│   • Perception Engine (`mss` X11 screen capture)                       │
│   • Cognitive Router (Ollama local / Chutes AI cloud)                  │
│   • Safety Gatekeeper (`gatekeeper.py` Level 0-3 approvals)            │
│   • Kernel Sandbox (`bwrap` Bubblewrap filesystem isolation)           │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

### Key Architectural Tenets
1. **Single-Port Simplicity (8420):** Both the REST/WebSocket API and the bundled React PWA are served by Python's `aiohttp` web server on port `8420`. No reverse proxy (Nginx/Caddy) or secondary Node.js runtime is required in production.
2. **Private Network Isolation:** Access is strictly bounded to the user's private Tailscale network (Tailnet). Even if a malicious actor knows the IP address, connection packets are cryptographically rejected at the OS network interface layer.
3. **Device Parity:** Android, iPhone, iPad, and remote Mac/PC laptops share the exact same responsive UI, offline cache, and interaction model.

---

## 3. The Dual-Channel Strategy: Telegram vs. PWA

EidosBot intentionally splits remote interactions into two complementary channels:

| Capability | Telegram Gateway (`telegram_bot.py`) | Mobile PWA Gateway (`web/` + `server.py`) |
| :--- | :--- | :--- |
| **Primary Use Case** | Ambient alerts, pocket notifications, quick approvals | Active conversation, deep research review, desktop monitoring |
| **Delivery Model** | Native push notifications (via Telegram Cloud) | Full-screen web application launched from home screen |
| **Token Streaming** | Rate-limited (~1 message edit/sec max) | Real-time WebSocket streaming (60+ tokens/sec) |
| **Reasoning Display** | Monolithic text walls or chat clutter | Collapsible `<thought>` & `<recall>` visual accordions |
| **Desktop Preview** | Periodic compressed photo uploads | Live interactive viewport with manual or continuous refresh |
| **Safety Interceptions** | 2-button inline keyboard card | Rich modal with command diffs, target paths, and risk badges |
| **Duplex Audio** | Recorded `.ogg` voice memos | Low-latency Web Audio push-to-talk and Kokoro TTS playback |
| **Emergency Kill** | Text command `/kill` | Instant red tactile kill button in sticky navigation bar |

---

## 4. Frontend Application Specification (`web/`)

The mobile frontend lives directly inside the repository under `web/` and is compiled into static production assets served by the backend.

### 4.1. Technology Stack
- **Framework:** React 19 / 18 + Vite (fast build times, tiny bundle size, TypeScript safety).
- **Styling:** Tailwind CSS (configured with the Dracula dark theme to match the desktop PyQt6 HUD).
- **Icons:** Lucide React (`lucide-react`).
- **Markdown & Code:** `react-markdown`, `remark-gfm`, and `rehype-highlight` / `prismjs` for syntax highlighting.
- **PWA Tooling:** `vite-plugin-pwa` with custom Web App Manifest and Service Worker.

### 4.2. Directory Layout
```text
EidosBot/
├── web/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── tailwind.config.js
│   ├── public/
│   │   ├── manifest.json            # PWA installation manifest
│   │   ├── favicon.ico
│   │   ├── icon-192.png             # Android & iOS app icon (192x192)
│   │   ├── icon-512.png             # Android splash icon (512x512)
│   │   └── apple-touch-icon.png     # iOS Safari home screen icon (180x180)
│   ├── src/
│   │   ├── main.tsx                 # React entry point
│   │   ├── App.tsx                  # Root layout & active view router
│   │   ├── types/
│   │   │   └── gateway.ts           # TypeScript interfaces for WebSocket & API
│   │   ├── hooks/
│   │   │   ├── useEidosWS.ts        # Resilient WebSocket connection & reconnect loop
│   │   │   ├── useAuth.ts           # Token management via localStorage / URL params
│   │   │   └── useAudioPlayback.ts  # Web Audio API audio chunk streamer
│   │   ├── components/
│   │   │   ├── Header.tsx           # App header with connection badge, active task & kill button
│   │   │   ├── ChatView.tsx         # Message history, prompt input, push-to-talk mic
│   │   │   ├── MessageBubble.tsx    # Dracula bubble with Markdown & collapsible thought cards
│   │   │   ├── ThoughtAccordion.tsx # Collapsible renderer for <thought> and <recall> streams
│   │   │   ├── ScreenViewport.tsx   # Live desktop screenshot viewer with pinch-to-zoom
│   │   │   ├── SafetyModal.tsx      # Interactive Level 2/3 confirmation card
│   │   │   └── StatusBar.tsx        # System status pill (Idle, Thinking, Grounding, Executing)
│   └── dist/                        # Compiled production bundle (served by server.py)
```

### 4.3. Core Views & Components

#### 1. Header & Emergency Control Bar
- **Dracula Purple Branding:** EidosBot title with active status indicator dot:
  - 🟢 **Ready / Idle:** Normal readiness.
  - 🟡 **Thinking / Perceiving / Executing:** Pulsing gold glow with current action description.
  - 🔴 **Emergency Stop:** High-visibility crimson banner when kill switch is tripped.
- **Tactile Kill Switch:** Top-right red button `[🛑 STOP]`. One tap immediately calls `/api/kill`, instantly halting background subprocesses, sandbox commands, and the agent loop. Changes to `[🔄 Reset]` when tripped.

#### 2. Chat & Reasoning Feed
- **Typewriter Streaming:** Renders assistant response tokens as they arrive over the WebSocket.
- **Collapsible Thought Blocks:** Model reasoning enclosed in `<thought>...</thought>` or `<recall>...</recall>` is automatically parsed out of the main conversational flow into a discrete expandable box:
  - *Collapsed State:* `💭 Thought Process (4.2s • 182 tokens) ▸`
  - *Expanded State:* Complete step-by-step chain-of-thought in monospace font with dark slate background.
- **Rich Code Blocks:** Syntax-highlighted code blocks with 1-tap "Copy to Clipboard" buttons.
- **Prompt Input Field:** Expandable textarea with quick-submit button, keyboard submit (`Enter`), and push-to-talk audio input button.

#### 3. Interactive Safety Confirmation Modals (`SafetyModal.tsx`)
When the Gatekeeper intercepts a Level 2 (Shell) or Level 3 (Destructive) command, a high-priority card floats over the interface:
- **Header:** Risk badge (`Level 2: Shell Execution` or `Level 3: Destructive Operation`).
- **Context Details:**
  - Action Type: `run_shell` or `sandboxed_shell`.
  - Target Command: Syntax-highlighted bash command or script.
  - Working Directory: e.g. `~/CursorProjects/EidosBot`.
  - Risk Explanation: Reason provided by the Safety Classifier.
- **User Actions:**
  - `[ ✅ Approve Once ]` (Resolves confirmation and runs action).
  - `[ 💾 Always Allow ]` (Applies approval and persistently adds command to `~/.eidos/allowlist.json`).
  - `[ ❌ Reject ]` (Aborts action and informs agent loop to try an alternative approach).
  - `[ 🛑 Emergency Stop ]` (Trips the global kill switch).

#### 4. Live Desktop Viewport (`ScreenViewport.tsx`)
- **Visual Verification:** Allows user to verify what the computer is doing (e.g. while agent is performing DOM navigation, document editing, or browser research).
- **Controls:**
  - Manual Refresh button `[🔄 Refresh]`.
  - Auto-stream toggle (1 fps or 0.5 fps stream for low bandwidth consumption over cellular data).
  - Tap-to-zoom / pinch-to-zoom modal for inspecting tiny screen details.

---

## 5. Backend Gateway Enhancements (`eidos/gateway/server.py`)

### 5.1. Static Asset Serving & SPA Routing
`RemoteGatewayServer` in `server.py` will be enhanced to serve the compiled React bundle from `web/dist/` when present, falling back gracefully to the embedded single-file HTML dashboard if `web/dist/` has not yet been built:

```python
# In eidos/gateway/server.py
dist_dir = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
if dist_dir.is_dir() and (dist_dir / "index.html").is_file():
    # Serve static files (JS, CSS, images, manifest)
    self._app.router.add_static("/assets", dist_dir / "assets", show_index=False)
    self._app.router.add_static("/icons", dist_dir / "icons", show_index=False)
    # SPA fallback: Any unmatched route returns index.html
    self._app.router.add_get("/", self._handle_spa_index)
    self._app.router.add_get("/manifest.json", self._handle_manifest)
else:
    self._app.router.add_get("/", self._handle_dashboard) # Embedded fallback
```

### 5.2. Bi-Directional WebSocket Protocol (`/ws`)

The WebSocket connection handles real-time bidirectional messaging:

#### Client-to-Server Messages
```json
// Send user prompt
{ "type": "prompt", "prompt": "Review recent batch run results" }

// Trip or reset kill switch
{ "type": "kill" }
{ "type": "reset" }

// Resolve safety confirmation
{ "type": "resolve_confirmation", "id": "b4a18f2e", "approved": true, "allowlist": false }

// Speak aloud through host speakers
{ "type": "speak", "message": "Batch complete", "voice": "bm_george" }
```

#### Server-to-Client Broadcast Events
```json
// Agent execution state
{ "event": "status_change", "state": "thinking", "details": "Analyzing repository..." }

// Token typewriter streaming
{ "event": "token_chunk", "chunk": "Here are the ", "is_thought": false }
{ "event": "thought_chunk", "chunk": "Querying vector database for...", "is_thought": true }

// Task completion summary
{ "event": "task_finished", "summary": "Successfully updated documentation", "success": true }

// Safety confirmation request
{
  "event": "confirmation_requested",
  "id": "b4a18f2e",
  "action_type": "run_shell",
  "risk_level": 2,
  "reason": "Requires host shell execution outside sandbox",
  "details": { "command": "git commit -m 'Update docs'", "cwd": "~/CursorProjects/EidosBot" }
}

// Confirmation resolved (clears modal on all connected devices)
{ "event": "confirmation_resolved", "id": "b4a18f2e", "result": [true, false] }
```

---

## 6. Tailscale Private Networking & Multi-Device Setup

### 6.1. Why Tailscale?
1. **Zero Firewall Holes:** No port forwarding on your home router, no dynamic DNS, no static IP fees, and no NAT traversal pain.
2. **End-to-End Encryption:** Direct peer-to-peer tunnels using WireGuard cryptographic key pairs.
3. **Free Tier:** Tailscale's personal plan allows up to 100 devices and 3 users completely free.

### 6.2. Linux Mint Host Configuration
```bash
# 1. Install Tailscale on Linux Mint (if not already installed)
curl -fsSL https://tailscale.com/install.sh | sh

# 2. Authenticate machine
sudo tailscale up

# 3. Retrieve your private Tailscale IP
tailscale ip -4
# Example output: 100.85.20.14
```

### 6.3. Android Installation Walkthrough (Your Phone)
1. Install **Tailscale** from Google Play Store and sign in with the same account.
2. Open **Google Chrome** on Android and navigate to:
   `http://100.85.20.14:8420`
3. Enter your Gateway Auth Token (stored in `localStorage` once entered).
4. Tap the Chrome three-dot menu `⋮` $\rightarrow$ Tap **"Install App"** (or **"Add to Home Screen"**).
5. Android compiles and installs the **EidosBot WebAPK**. An official app icon appears on your home screen launcher that opens full-screen without a browser URL bar.

### 6.4. iPhone / iPad Installation Walkthrough (Wife's Device)
1. Install **Tailscale** from Apple App Store.
2. **Access Method:**
   - *Option A (Shared Account):* Sign into the same Tailscale account.
   - *Option B (Separate Accounts):* On your Tailscale admin console on your PC, click your desktop node $\rightarrow$ **"Share Machine"** $\rightarrow$ enter your wife's Tailscale email. She accepts the invitation on her iPhone.
3. Open **Safari** on iOS and navigate to:
   `http://100.85.20.14:8420`
4. Tap the Safari **Share** icon (square with upward arrow 📤) at the bottom.
5. Scroll down and tap **"Add to Home Screen"**.
6. iOS creates a standalone app icon. Tapping it opens EidosBot in fullscreen standalone mode (no Safari tabs or address bar).

### 6.5. Agent-assisted Tailscale on the PC (login, health, routing)

Phones and tablets **must** complete Tailscale sign-in in the **Tailscale mobile app** (or accept a **shared machine** invite). EidosBot on Linux Mint cannot log in *for* a remote device—but it is the right **control plane on the host** for everything on the PC side.

See the full design note: **[`TAILSCALE_AND_EIDOS.md`](TAILSCALE_AND_EIDOS.md)**.

**Summary**

| Approach | Role of Eidos |
|---|---|
| **Local `tailscale` CLI** (recommended) | Planned tools: status, netcheck, `up`, ping peers; Gatekeeper Level 0–2; open OAuth URL in browser when you run `tailscale up` |
| **Auth key file** (`~/.eidos/tailscale.authkey`, user-owned) | Optional unattended `tailscale up` after reboot—scheduler/PWA path; never pass keys through chat or tool args |
| **MCP (optional, later)** | Tailscale **docs** MCP for setup questions only; **Admin API** MCP (community) is high privilege—prefer CLI first; Tailscale **Aperture** tailnet MCP is for multi-node deploy, not phone OAuth |
| **Routing** | No Eidos proxy: PWA hits `100.x:8420` (or MagicDNS) over WireGuard; gateway bearer token is still required |

**Clever PC workflow Eidos should support (implementation backlog):**

1. Diagnose: `tailscale status` + `netcheck` + confirm gateway listens on `:8420`.
2. Interactive join: run `tailscale up`, surface login URL, open browser, poll until `Running`.
3. Verify remote path: `tailscale ping <phone-hostname>` and tell you the exact PWA URL to bookmark.
4. Pair with scheduled wake: **Telegram** timers work without Tailscale; **PWA** timers need the host on the tailnet (see [`SCHEDULED_WAKE_AND_TIMERS.md`](SCHEDULED_WAKE_AND_TIMERS.md)).

---

## 7. Progressive Web App (PWA) Manifest Specification

To ensure both Android and iOS recognize EidosBot as a native-grade application, `web/public/manifest.json` specifies:

```json
{
  "name": "EidosBot Mobile Companion",
  "short_name": "EidosBot",
  "description": "Remote control, token streaming, and safety confirmations for EidosBot OS agent.",
  "start_url": "/?source=pwa",
  "display": "standalone",
  "background_color": "#1e1f29",
  "theme_color": "#bd93f9",
  "orientation": "portrait-primary",
  "icons": [
    {
      "src": "/icons/icon-192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any maskable"
    },
    {
      "src": "/icons/icon-512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any maskable"
    }
  ]
}
```

### iOS-Specific HTML Headers (`index.html`)
Apple Safari requires dedicated meta tags for standalone fullscreen execution:
```html
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="EidosBot">
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
```

---

## 8. Step-by-Step Implementation Roadmap

```text
┌────────────────────────────────────────────────────────────────────────┐
│                   PWA GATEWAY IMPLEMENTATION ROADMAP                   │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  Phase 1: Backend Gateway Enhancements (`eidos/gateway/server.py`)     │
│  • Add static file serving for `web/dist/` with fallback to dashboard  │
│  • Expand WebSocket handler for token streaming & thought chunks       │
│  • Wire AgentLoop token listener into Gateway broadcaster              │
│                                                                        │
│  Phase 2: Frontend Scaffolding (`web/`)                                │
│  • Initialize Vite + React + TypeScript + Tailwind CSS project         │
│  • Configure Dracula theme tokens (#1e1f29, #282a36, #bd93f9, #50fa7b) │
│  • Generate PWA manifest, service worker config, and app icons         │
│                                                                        │
│  Phase 3: Real-Time Communication & Core UI Views                      │
│  • Build `useEidosWS` hook with auto-reconnect & state management      │
│  • Build `ChatView` with Markdown, syntax highlighting & copy buttons  │
│  • Build `ThoughtAccordion` for collapsible <thought>/<recall> streams │
│                                                                        │
│  Phase 4: Remote Safety Confirmations & Desktop Mirroring              │
│  • Build `SafetyModal` for 1-tap Level 2/3 Approve/Always/Reject/Kill  │
│  • Build `ScreenViewport` with auto-refresh and pinch-to-zoom          │
│  • Hook emergency kill switch button into persistent header            │
│                                                                        │
│  Phase 5: Production Build, Tailscale & Multi-Device Testing           │
│  • Compile bundle (`npm run build`) to generate `web/dist/`            │
│  • Verify static serving from `server.py` on Linux Mint                │
│  • Test PWA installation on Android (Chrome WebAPK) and iPhone (Safari)│
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 9. Security & Hardening Checklist

1. **Private Mesh Only:** `server.py` binds to `0.0.0.0:8420`, but your home router's firewall keeps it invisible to the public internet. Access is mediated strictly by Tailscale WireGuard keys.
2. **Bearer Token Authentication:** Every API request and WebSocket handshake validates the `auth_token` defined in `config.yaml` or generated on first boot.
3. **Local Storage Session:** Tokens are securely stored in the phone's browser `localStorage` on initial sign-in, eliminating repetitive password prompts.
4. **Physical Presence Override:** Destructive Level 3 actions (like raw partition formatting or root filesystem modifications) can still be configured in `config.yaml` to require physical presence at the desktop keyboard if maximum security is desired.
