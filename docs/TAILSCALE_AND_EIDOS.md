# Tailscale, Remote PWA & Agent-Assisted Login

**Related:** [`MOBILE_PWA_GATEWAY.md`](MOBILE_PWA_GATEWAY.md), [`SCHEDULED_WAKE_AND_TIMERS.md`](SCHEDULED_WAKE_AND_TIMERS.md), [`PROJECT_JOBS.md`](PROJECT_JOBS.md) (Phase 14 integrations / MCP)

**Status:** Design note (no dedicated `tailscale_*` tools in repo yet)

---

## 1. What we are trying to solve

EidosBot’s **mobile PWA**, **scheduled notifications** on the private mesh, and **OptimalX sync** all assume devices can reach each other on a **Tailscale tailnet** (WireGuard `100.x` addresses or MagicDNS names like `http://eidos-box:8420`).

Tailscale **login** is a trust decision: each device must join the tailnet via **interactive OAuth** (browser / app) or an **auth key** issued from the admin console. Eidos cannot bypass that boundary—but on **your Linux Mint PC** it can act as a **control plane**: diagnose the link, run approved `tailscale` CLI steps, open the consent URL, and verify that the gateway is reachable from the phone’s perspective.

**Important limitation:** Eidos cannot complete **Tailscale sign-in on your phone** from the desktop. The phone (or iPad) still needs the Tailscale app and your account (or an accepted **machine share**). Eidos can guide you, run checks on the host, and prepare keys—but not tap “Log in” on iOS for you.

---

## 2. Recommended approach: local PC + CLI (no MCP required)

For a single-home **Eidos host**, the simplest design is **not** an Admin API MCP server. It is a small set of **Level 0–2 tools** (or shell allowlist entries) that wrap the **local** `tailscale` binary—the same commands you would run in a terminal.

| Step | Human | Eidos on PC |
|---|---|---|
| First-time host join | Click through browser after `tailscale up` | Run `tailscale up` (Level 2 confirm); optional `browser_open` on printed URL; read `tailscale ip -4` |
| Phone join | Install Tailscale app; sign in | Explain steps; optionally verify later with `tailscale ping <phone-name>` |
| PWA works | Open `http://<host-100.x>:8420` on phone | Confirm `gateway` listening; `tailscale status`; suggest MagicDNS hostname from admin console |
| After reboot | Sometimes `tailscaled` needs `tailscale up` | Scheduler or agent checks `tailscale status` and nudges user or uses auth key (below) |

**Why CLI first**

- No **OAuth admin token** or Tailscale API client secret in the agent context.
- Fits existing **Gatekeeper** and allowlist model (`tailscale status` → Level 0; `sudo tailscale up` → Level 2).
- Works offline on the LAN; tailnet only needed for **remote** PWA/Telegram-is-independent paths.

**Planned tool surface (names tentative)**

- `tailscale_status` — parse `tailscale status --json` (backend state, IPs, peers).
- `tailscale_netcheck` — `tailscale netcheck` for DERP/STUN health (debug “PWA won’t connect”).
- `tailscale_up` — `tailscale up` with flags; **never** pass raw auth keys in tool arguments from the model.
- `tailscale_ping_peer` — `tailscale ping <hostname>` to confirm phone ↔ desktop path.

Auth keys, if used, should be read from a **user-controlled file** outside prompt context, e.g. `~/.eidos/tailscale.authkey` (chmod `600`, not in git), referenced only by config `integrations.tailscale.auth_key_file` and loaded inside the tool implementation after Gatekeeper approval—not echoed in chat logs.

---

## 3. “Clever” PC patterns (unattended & scheduled wake)

These pair well with [`SCHEDULED_WAKE_AND_TIMERS.md`](SCHEDULED_WAKE_AND_TIMERS.md): a headless `eidos-scheduler` can notify you on **Telegram** without Tailscale, but **PWA / private HTTP** to `:8420` requires the host to be on the tailnet.

### 3.1. Ephemeral auth key on the host (headless re-auth)

1. In [Tailscale admin](https://login.tailscale.com/admin/settings/keys), create an **ephemeral** or reusable **auth key** (tagged ACLs if you use tags).
2. Store path in config; Eidos runs `sudo tailscale up --auth-key-file=...` only after **explicit user approval** or one-time allowlist.
3. After success, delete or rotate the key in admin.

Use sparingly: keys are secrets. Prefer **`tailscaled` enabled at boot** (`systemctl enable tailscaled`) so interactive re-login is rare.

### 3.2. Browser-assisted interactive login

`tailscale up` without a key prints a URL. Eidos:

1. Captures subprocess output.
2. Opens the URL via headless browser or `xdg-open` (Level 1 UI).
3. Waits and polls `tailscale status` until `Running`.

This is the right path when you are **at the PC** and only need the agent to remove friction.

### 3.3. Machine sharing (second user, no shared password)

Already documented in [`MOBILE_PWA_GATEWAY.md`](MOBILE_PWA_GATEWAY.md) §6.4: share the **desktop node** to another Tailscale identity. Eidos can walk through the admin console steps in chat; no API integration required.

### 3.4. Gateway binding vs Tailscale routing

- Eidos **does not terminate Tailscale** and **does not proxy** the tailnet.
- `gateway.host: 0.0.0.0` + firewall on the LAN + **Tailscale interface** = phone connects directly to `100.x:8420`.
- **Bearer token** on `/api/*` and `/ws` remains mandatory; Tailscale only provides private **routing**, not app auth.

### 3.5. Split paths for scheduled wake

| Channel when GUI closed | Needs Tailscale on host? |
|---|---|
| Telegram | No (outbound to Telegram API) |
| PWA / `scheduled_notification` over WS | Yes (phone must reach gateway) |
| Desktop pill | No (local UI) |

Document this in timer **channel** selection: remote mesh delivery implies a **tailnet health** check in the scheduler (future: soft dependency “warn if `tailscale status` not Running”).

---

## 4. MCP and third-party routing (optional, Phase 14+)

There is **no requirement** to use MCP for home login. If you later attach Tailscale as a **project integration**, consider these layers separately:

| Option | What it gives Eidos | Good for login? |
|---|---|---|
| **Local CLI tools** (§2) | Status, up, ping, netcheck on **this** machine | **Yes** — primary recommendation |
| **Tailscale docs MCP** (`https://tailscale.mcp.kapa.ai/`) | Documentation search | **Indirect** — setup help only |
| **Tailscale Aperture built-ins** (alpha): Tailnet / TailnetSSH | Provision nodes, SSH to tailnet machines via their gateway product | **Partial** — multi-node homelab / deploy, not phone OAuth |
| **Community Admin API MCP** (e.g. jaxxstorm/tailscale-mcp) | Full tailnet admin via OAuth | **Power user only** — needs admin OAuth, broad blast radius; lock down with tags + grants |

**Eidos integration policy (recommended)**

1. Ship **CLI wrapper tools** in-repo under `eidos/integrations/tailscale.py` (mirroring OptimalX bridge style).
2. Defer **Admin API MCP** until [PROJECT_JOBS.md](PROJECT_JOBS.md) Phase 14: attach as named integration with Gatekeeper per tool, **read-only profile** by default (`TAILSCALE_READONLY` equivalent).
3. Never put **auth keys or API OAuth secrets** in `mcp.json` editable by the model; integration records stay user-owned under `~/.eidos/integrations/`.

**Routing note:** MCP does not replace WireGuard. An MCP server running **on the same PC** still administers Tailscale; your phone still uses the **Tailscale app** to join the tailnet. An MCP server running **on a VPS** could manage ACLs or auth keys in the cloud—that is a different threat model and usually unnecessary for a single Mint box.

---

## 5. Security & Gatekeeper

| Action | Suggested level |
|---|---|
| `tailscale status`, `ip -4`, `netcheck`, `ping` | Level 0 |
| `tailscale up` (no sudo, if permitted) | Level 2 |
| `sudo tailscale up` with auth key file | Level 2 + optional Level 3 if key is reusable |
| Opening admin OAuth URL | Level 1 (browser) |
| Installing packages (`curl install.sh \| sh`) | Level 2/3 — **human only**, agent documents steps |

Audit all tailscale-related executions in `~/.eidos/audit.log`.

---

## 6. User-facing prompt examples

- *“Is my tailnet up? Can my phone reach the Eidos PWA?”* → `tailscale_status`, check gateway port, suggest URL with current `100.x` or MagicDNS.
- *“Help me add my wife’s iPhone.”* → Machine share checklist (MOBILE_PWA §6.4), no MCP.
- *“After reboot Tailscale is down—fix it.”* → `tailscale up` flow or auth-key path if pre-configured.
- *“When the scheduler fires, will my phone get the notification?”* → Clarify Telegram vs PWA; if PWA, verify tailnet.

---

## 7. Implementation backlog (cross-links)

| Item | Target doc |
|---|---|
| `eidos/integrations/tailscale.py` + tools | This note → future `SPEC_TAILSCALE_INTEGRATION.md` |
| Scheduler tailnet health warning | [`plans/PLAN_SCHEDULED_WAKE.md`](plans/PLAN_SCHEDULED_WAKE.md) Phase 4 |
| Settings: “Copy PWA URL” with live `tailscale ip -4` | [`MOBILE_PWA_GATEWAY.md`](MOBILE_PWA_GATEWAY.md) |
| Optional MCP attachment | [`ROADMAP.md`](ROADMAP.md) Phase 14 |
