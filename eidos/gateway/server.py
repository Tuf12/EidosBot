"""Remote Mobile Gateway for EidosBot.

Provides a secured REST and WebSocket server with a responsive mobile web dashboard.
Enables monitoring agent state, live desktop screenshots, sending prompts,
and remotely approving/denying Level 2 and Level 3 safety confirmations from a phone.
"""

import asyncio
import io
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import uuid

from aiohttp import web, WSMsgType

from eidos.perception.screen import ScreenCapture
from eidos.safety.risk_classifier import RiskLevel
from eidos.voice.voices import DEFAULT_VOICE_ID, list_voices

logger = logging.getLogger(__name__)

WEB_DIST_DIR = Path(__file__).resolve().parent.parent.parent / "web" / "dist"


def resolve_gateway_token(
    token: Optional[str] = None,
    token_env_var: str = "EIDOS_GATEWAY_AUTH_TOKEN",
    token_file: str = "~/.eidos/gateway.token",
) -> str:
    """Discover gateway bearer token from parameter, env var, or local file."""
    if token and str(token).strip():
        return str(token).strip()
    if token_env_var and os.environ.get(token_env_var, "").strip():
        return os.environ.get(token_env_var, "").strip()
    p = Path(os.path.abspath(os.path.expanduser(token_file)))
    if p.is_file():
        try:
            val = p.read_text(encoding="utf-8").strip()
            if val:
                return val
        except Exception:
            pass
    return secrets.token_hex(16)


HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>EidosBot Remote Gateway</title>
  <style>
    :root {
      --bg: #1e1f29;
      --card: #282a36;
      --border: #44475a;
      --text: #f8f8f2;
      --text-dim: #6272a4;
      --green: #50fa7b;
      --yellow: #f1fa8c;
      --orange: #ffb86c;
      --purple: #bd93f9;
      --red: #ff5555;
      --cyan: #8be9fd;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    body { background-color: var(--bg); color: var(--text); padding: 12px; max-width: 680px; margin: 0 auto; }
    header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 12px; border-bottom: 1px solid var(--border); margin-bottom: 14px; }
    h1 { font-size: 1.25rem; color: var(--purple); display: flex; align-items: center; gap: 8px; }
    .badge { font-size: 0.75rem; padding: 3px 8px; border-radius: 12px; font-weight: bold; background: var(--border); }
    .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px; margin-bottom: 14px; }
    .status-row { display: flex; justify-content: space-between; align-items: center; }
    .status-pill { display: inline-flex; align-items: center; gap: 6px; font-weight: bold; font-size: 0.95rem; }
    .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
    .dot-ready { background: var(--green); box-shadow: 0 0 8px var(--green); }
    .dot-busy { background: var(--yellow); box-shadow: 0 0 8px var(--yellow); animation: pulse 1s infinite; }
    .dot-killed { background: var(--red); box-shadow: 0 0 8px var(--red); }
    @keyframes pulse { 0% { opacity: 0.5; } 50% { opacity: 1; } 100% { opacity: 0.5; } }
    .btn { background: var(--purple); color: var(--bg); font-weight: bold; border: none; border-radius: 6px; padding: 10px 14px; cursor: pointer; font-size: 0.95rem; transition: opacity 0.2s; }
    .btn:active { opacity: 0.8; }
    .btn-red { background: var(--red); color: white; }
    .btn-green { background: var(--green); color: var(--bg); }
    .btn-outline { background: transparent; border: 1px solid var(--border); color: var(--text); }
    .controls { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 10px; }
    input, textarea { width: 100%; background: #181920; border: 1px solid var(--border); color: var(--text); padding: 10px; border-radius: 6px; font-size: 0.95rem; margin-top: 6px; }
    input:focus, textarea:focus { outline: none; border-color: var(--purple); }
    #screenshot-img { width: 100%; border-radius: 6px; margin-top: 8px; border: 1px solid var(--border); display: block; }
    .confirm-card { border: 2px solid var(--orange); background: #2f251e; animation: pulse 2s infinite; }
    .confirm-title { color: var(--orange); font-weight: bold; font-size: 1.1rem; margin-bottom: 6px; display: flex; align-items: center; gap: 6px; }
    .confirm-detail { background: #1a1a24; padding: 8px; border-radius: 6px; font-family: monospace; font-size: 0.85rem; margin: 8px 0; word-break: break-all; }
    .checkbox-label { display: flex; align-items: center; gap: 8px; font-size: 0.85rem; color: var(--text-dim); margin-top: 8px; }
    .confirm-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 10px; }
    .log-list { max-height: 140px; overflow-y: auto; font-family: monospace; font-size: 0.8rem; color: var(--text-dim); display: flex; flex-direction: column-reverse; }
    .log-item { padding: 3px 0; border-bottom: 1px solid #333544; }
  </style>
</head>
<body>
  <header>
    <h1><span>🤖</span> EidosBot Gateway</h1>
    <div id="conn-badge" class="badge" style="color: var(--text-dim)">Connecting...</div>
  </header>

  <!-- Safety / State Alert -->
  <div id="confirm-container"></div>

  <!-- Status Card -->
  <div class="card">
    <div class="status-row">
      <div class="status-pill">
        <span id="status-dot" class="dot dot-ready"></span>
        <span id="status-text">Ready</span>
      </div>
      <div style="display: flex; gap: 6px;">
        <button id="btn-sync" class="btn btn-outline" style="padding: 6px 10px; font-size: 0.8rem;" onclick="refreshHistory()">🔄 Sync</button>
        <button id="btn-new-chat" class="btn btn-green" style="padding: 6px 10px; font-size: 0.8rem;" onclick="startNewChat()">➕ New Chat</button>
        <button id="btn-kill" class="btn btn-red" style="padding: 6px 10px; font-size: 0.8rem;" onclick="toggleKillSwitch()">🛑 Stop</button>
      </div>
    </div>
    <div id="status-details" style="font-size: 0.85rem; color: var(--text-dim); margin-top: 6px;">Idle</div>
    <div id="conv-title" style="font-size: 0.8rem; color: var(--purple); margin-top: 4px; font-weight: bold;">Thread: New Chat</div>
  </div>

  <!-- Task Prompt -->
  <div class="card">
    <label style="font-size: 0.85rem; font-weight: bold; color: var(--cyan);">Remote Task Command</label>
    <textarea id="task-prompt" rows="2" placeholder="Send prompt to EidosBot..."></textarea>
    <div class="controls">
      <button class="btn btn-green" onclick="sendPrompt()">▶ Execute Task</button>
      <button class="btn btn-outline" onclick="speakAloud()">🔊 Speak Aloud</button>
    </div>
  </div>

  <!-- Live Screen Preview -->
  <div class="card">
    <div style="display: flex; justify-content: space-between; align-items: center;">
      <label style="font-size: 0.85rem; font-weight: bold; color: var(--purple);">Desktop Screen</label>
      <button class="btn btn-outline" style="padding: 4px 10px; font-size: 0.8rem;" onclick="refreshScreenshot()">🔄 Refresh</button>
    </div>
    <img id="screenshot-img" src="/api/screenshot" alt="Screen preview" onclick="refreshScreenshot()">
  </div>

  <!-- Activity Logs -->
  <div class="card">
    <label style="font-size: 0.85rem; font-weight: bold; color: var(--text-dim);">Live Activity Log</label>
    <div id="log-list" class="log-list"></div>
  </div>

  <script>
    let token = localStorage.getItem('eidos_token') || new URLSearchParams(window.location.search).get('token') || '';
    if (!token) {
      token = prompt('Enter Eidos Gateway Auth Token:') || '';
      if (token) localStorage.setItem('eidos_token', token);
    }

    let isKilled = false;
    let ws = null;

    function addLog(msg) {
      const list = document.getElementById('log-list');
      const item = document.createElement('div');
      item.className = 'log-item';
      item.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
      list.prepend(item);
    }

    function authHeaders() {
      return { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' };
    }

    function updateStatus(state, details) {
      const dot = document.getElementById('status-dot');
      const text = document.getElementById('status-text');
      const desc = document.getElementById('status-details');
      const killBtn = document.getElementById('btn-kill');

      text.textContent = state.toUpperCase();
      desc.textContent = details || '';

      if (state.toLowerCase() === 'killed') {
        dot.className = 'dot dot-killed';
        killBtn.textContent = '🔄 Reset';
        killBtn.className = 'btn btn-outline';
        isKilled = true;
      } else if (['thinking', 'executing', 'perceiving'].includes(state.toLowerCase())) {
        dot.className = 'dot dot-busy';
        killBtn.textContent = '🛑 Stop';
        killBtn.className = 'btn btn-red';
        isKilled = false;
      } else {
        dot.className = 'dot dot-ready';
        killBtn.textContent = '🛑 Stop';
        killBtn.className = 'btn btn-red';
        isKilled = false;
      }
    }

    function refreshScreenshot() {
      const img = document.getElementById('screenshot-img');
      img.src = `/api/screenshot?token=${encodeURIComponent(token)}&t=${Date.now()}`;
    }

    async function sendPrompt() {
      const promptInput = document.getElementById('task-prompt');
      const promptText = promptInput.value.trim();
      if (!promptText) return;

      addLog(`Sending task: "${promptText}"`);
      promptInput.value = '';

      try {
        const res = await fetch('/api/prompt', {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({ prompt: promptText })
        });
        const data = await res.json();
        if (data.ok) {
          addLog("Task started.");
        } else {
          alert("Error: " + data.error);
        }
      } catch (err) {
        alert("Network error sending task: " + err);
      }
    }

    async function refreshHistory() {
      try {
        const res = await fetch('/api/history', { headers: authHeaders() });
        const data = await res.json();
        if (data.ok) {
          const titleEl = document.getElementById('conv-title');
          if (titleEl) titleEl.textContent = `Thread: ${data.title || 'New Chat'}`;
          addLog(`Synced with desktop app (${(data.messages || []).length} turns).`);
        }
      } catch (e) {
        addLog(`Sync error: ${e}`);
      }
    }

    async function startNewChat() {
      if (!confirm("Start a new conversation thread?")) return;
      try {
        const res = await fetch('/api/conversations/new', {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({ title: "New Chat" })
        });
        const data = await res.json();
        if (data.ok) {
          const titleEl = document.getElementById('conv-title');
          if (titleEl) titleEl.textContent = `Thread: ${data.title || 'New Chat'}`;
          addLog("Started new conversation thread.");
        }
      } catch (e) {
        alert("Failed to start new chat: " + e);
      }
    }

    async function speakAloud() {
      const msg = prompt("Text to speak aloud through PC speakers:");
      if (!msg) return;
      try {
        await fetch('/api/speak', {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({ message: msg })
        });
        addLog(`Vocalized: "${msg}"`);
      } catch (e) {
        alert("Failed to speak: " + e);
      }
    }

    async function toggleKillSwitch() {
      const endpoint = isKilled ? '/api/reset' : '/api/kill';
      try {
        const res = await fetch(endpoint, { method: 'POST', headers: authHeaders() });
        const data = await res.json();
        addLog(isKilled ? "Reset emergency stop." : "TRIPPED EMERGENCY KILL SWITCH!");
      } catch (e) {
        alert("Failed to toggle kill switch: " + e);
      }
    }

    function renderConfirmations(items) {
      const container = document.getElementById('confirm-container');
      container.innerHTML = '';
      if (!items || items.length === 0) return;

      items.forEach(c => {
        const card = document.createElement('div');
        card.className = 'card confirm-card';
        card.innerHTML = `
          <div class="confirm-title">⚠️ Confirmation Required (Level ${c.risk_level})</div>
          <div style="font-size: 0.9rem;"><strong>Action:</strong> ${c.action_type}</div>
          <div style="font-size: 0.85rem; color: var(--yellow); margin-top: 4px;">${c.reason}</div>
          <div class="confirm-detail">${JSON.stringify(c.details, null, 2)}</div>
          <label class="checkbox-label">
            <input type="checkbox" id="remember-${c.id}">
            Always allow this exact command in allowlist
          </label>
          <div class="confirm-actions">
            <button class="btn btn-green" onclick="respondConfirm('${c.id}', true)">✓ Approve</button>
            <button class="btn btn-red" onclick="respondConfirm('${c.id}', false)">✗ Reject</button>
          </div>
        `;
        container.appendChild(card);
      });
    }

    async function respondConfirm(id, approved) {
      const chk = document.getElementById(`remember-${id}`);
      const allowlist = chk ? chk.checked : false;

      try {
        await fetch(`/api/confirmations/${id}`, {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({ approved, allowlist })
        });
        addLog(`Confirmation ${id}: ${approved ? 'APPROVED' : 'DENIED'}`);
        pollConfirmations();
      } catch (e) {
        alert("Failed to respond to confirmation: " + e);
      }
    }

    async function pollConfirmations() {
      try {
        const res = await fetch('/api/confirmations', { headers: authHeaders() });
        if (res.ok) {
          const data = await res.json();
          renderConfirmations(data.confirmations || []);
        }
      } catch (e) {}
    }

    function initWebSocket() {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${proto}//${window.location.host}/ws?token=${encodeURIComponent(token)}`;
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        const b = document.getElementById('conn-badge');
        b.textContent = '● Live';
        b.style.color = 'var(--green)';
        addLog('Connected to gateway WebSocket.');
      };

      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.event === 'status_change') {
            updateStatus(msg.state, msg.details);
          } else if (msg.event === 'log') {
            addLog(msg.message);
          } else if (msg.event === 'confirmation_requested') {
            pollConfirmations();
            refreshScreenshot();
          } else if (msg.event === 'confirmation_resolved') {
            pollConfirmations();
          } else if (msg.event === 'conversation_changed') {
            const titleEl = document.getElementById('conv-title');
            if (titleEl) titleEl.textContent = `Thread: ${msg.title || 'New Chat'}`;
            addLog(`Conversation changed: ${msg.title || 'New Chat'}`);
          }
        } catch (e) {}
      };

      ws.onclose = () => {
        const b = document.getElementById('conn-badge');
        b.textContent = '○ Disconnected';
        b.style.color = 'var(--red)';
        setTimeout(initWebSocket, 3000);
      };
    }

    // Initial load
    initWebSocket();
    pollConfirmations();
    setInterval(pollConfirmations, 5000);
  </script>
</body>
</html>
"""


@web.middleware
async def cors_middleware(request: web.Request, handler):
    """Allow local dev tools (like Vite dev server) to access endpoints."""
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        try:
            resp = await handler(request)
        except web.HTTPException as ex:
            resp = ex
        except Exception as e:
            logger.error("Unhandled error in gateway request %s: %s", request.path, e)
            resp = web.json_response({"ok": False, "error": str(e)}, status=500)

    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Accept"
    return resp


class RemoteGatewayServer:
    """HTTP & WebSocket Remote Gateway allowing full mobile management of EidosBot."""

    def __init__(
        self,
        agent_loop: Any,
        host: str = "0.0.0.0",
        port: int = 8420,
        auth_token: Optional[str] = None,
        token_env_var: str = "EIDOS_GATEWAY_AUTH_TOKEN",
        token_file: str = "~/.eidos/gateway.token",
        screen_capture: Optional[ScreenCapture] = None,
    ):
        self.agent_loop = agent_loop
        self.host = host
        self.port = port
        self.auth_token = resolve_gateway_token(auth_token, token_env_var=token_env_var, token_file=token_file)
        self.screen = screen_capture or ScreenCapture()

        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

        self._ws_clients: Set[web.WebSocketResponse] = set()
        self._pending_confirmations: Dict[str, Dict[str, Any]] = {}
        self._confirm_lock = threading.Lock()

        # Track agent state
        self.current_state = "idle"
        self.current_task = ""

    def _verify_auth(self, request: web.Request) -> bool:
        """Validate Bearer token or URL token query parameter."""
        # 1. Check Authorization header
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if token == self.auth_token:
                return True

        # 2. Check query parameter
        token = request.query.get("token", "").strip()
        if token == self.auth_token:
            return True

        return False

    async def _handle_dashboard(self, request: web.Request) -> web.Response:
        """Serve the mobile-friendly web dashboard or compiled SPA bundle."""
        index_file = WEB_DIST_DIR / "index.html"
        if index_file.is_file():
            headers = {
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            }
            return web.FileResponse(index_file, headers=headers)
        return web.Response(text=HTML_DASHBOARD, content_type="text/html")

    async def _handle_spa_fallback(self, request: web.Request) -> web.Response:
        """Fallback handler for SPA routing and static assets."""
        path = request.path
        if path.startswith("/api/") or path == "/ws":
            return web.json_response({"ok": False, "error": "Endpoint not found"}, status=404)

        # Check if the requested file directly exists in WEB_DIST_DIR
        clean_path = path.lstrip("/")
        if clean_path:
            target = (WEB_DIST_DIR / clean_path).resolve()
            try:
                if target.is_file() and str(target).startswith(str(WEB_DIST_DIR)):
                    return web.FileResponse(target)
            except Exception:
                pass

        # Fallback to SPA index.html or embedded dashboard
        index_file = WEB_DIST_DIR / "index.html"
        if index_file.is_file():
            headers = {
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            }
            return web.FileResponse(index_file, headers=headers)
        return web.Response(text=HTML_DASHBOARD, content_type="text/html")

    async def _handle_service_worker(self, request: web.Request) -> web.Response:
        """Serve the Service Worker script with proper caching & PWA headers."""
        sw_file = WEB_DIST_DIR / "sw.js"
        if sw_file.is_file():
            headers = {
                "Content-Type": "application/javascript",
                "Service-Worker-Allowed": "/",
                "Cache-Control": "no-cache, no-store, must-revalidate",
            }
            return web.FileResponse(sw_file, headers=headers)
        return web.Response(text="// Service worker not found", content_type="application/javascript", status=404)

    async def _handle_manifest(self, request: web.Request) -> web.Response:
        """Serve the Web App Manifest for Android and iOS PWA installation."""
        manifest_file = WEB_DIST_DIR / "manifest.json"
        if manifest_file.is_file():
            return web.FileResponse(manifest_file)
        # Default dynamic manifest fallback
        default_manifest = {
            "name": "EidosBot Gateway",
            "short_name": "EidosBot",
            "description": "Remote control and safety monitoring for EidosBot OS agent.",
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
                    "purpose": "any maskable",
                },
                {
                    "src": "/icons/icon-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
            ],
        }
        return web.json_response(default_manifest, content_type="application/manifest+json")

    async def _handle_status(self, request: web.Request) -> web.Response:
        """Return current agent status."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        is_killed = False
        if hasattr(self.agent_loop, "kill_switch"):
            is_killed = self.agent_loop.kill_switch.is_triggered()

        return web.json_response({
            "ok": True,
            "state": "killed" if is_killed else self.current_state,
            "task": self.current_task,
            "killed": is_killed,
            "pending_confirmations": len(self._pending_confirmations),
        })

    def get_active_conversation_info(self) -> Tuple[Optional[str], str, List[Dict[str, Any]]]:
        """Return (conversation_id, title, messages_list) for current active conversation."""
        raw_conv_id = getattr(self.agent_loop, "active_conversation_id", None)
        conv_id: Optional[str] = str(raw_conv_id) if isinstance(raw_conv_id, (str, int)) else None
        title = "New Chat"
        messages: List[Dict[str, Any]] = []
        store = getattr(self.agent_loop, "conversation_store", None)

        if store and conv_id and hasattr(store, "get_conversation") and hasattr(store, "get_messages"):
            try:
                conv = store.get_conversation(conv_id)
                if isinstance(conv, dict) and "title" in conv:
                    title = str(conv.get("title", "New Chat"))
                raw_msgs = store.get_messages(conv_id)
                if isinstance(raw_msgs, list):
                    for rm in raw_msgs:
                        if isinstance(rm, dict):
                            role = str(rm.get("role") or "").lower()
                            if role in ("user", "assistant"):
                                messages.append({
                                    "id": str(rm.get("id", f"msg_{len(messages)}")),
                                    "role": role,
                                    "content": str(rm.get("content", "")),
                                    "timestamp": int(rm.get("createdAt") or time.time() * 1000),
                                })
            except Exception as e:
                logger.debug("Error getting messages from conversation_store: %s", e)

        # Fallback to in-memory conversation_history if store didn't return messages
        if not messages and hasattr(self.agent_loop, "conversation_history"):
            try:
                raw_hist = self.agent_loop.conversation_history
                if isinstance(raw_hist, list):
                    for i, m in enumerate(raw_hist):
                        if isinstance(m, dict):
                            role = str(m.get("role") or "assistant").lower()
                            if role in ("user", "assistant"):
                                messages.append({
                                    "id": str(m.get("id") or f"hist_{i}_{role}"),
                                    "role": role,
                                    "content": str(m.get("content", "")),
                                    "timestamp": int(m.get("timestamp") or time.time() * 1000),
                                })
            except Exception as e:
                logger.debug("Error getting in-memory conversation_history: %s", e)

        return conv_id, title, messages

    async def _handle_history(self, request: web.Request) -> web.Response:
        """Return conversational history and active messages."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        conv_id, title, history = self.get_active_conversation_info()
        return web.json_response({
            "ok": True,
            "conversation_id": conv_id,
            "title": title,
            "messages": history,
        })

    async def _handle_new_chat(self, request: web.Request) -> web.Response:
        """Start a fresh conversation thread and inform all clients."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        title = "New Chat"
        if request.can_read_body and request.content_length:
            try:
                data = await request.json()
                if "title" in data and str(data["title"]).strip():
                    title = str(data["title"]).strip()
            except Exception:
                pass

        new_id = self.start_new_chat(title=title)
        return web.json_response({
            "ok": True,
            "conversation_id": new_id,
            "title": title,
            "messages": [],
        })

    async def _handle_list_conversations(self, request: web.Request) -> web.Response:
        """List past conversations."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        store = getattr(self.agent_loop, "conversation_store", None)
        raw_active_id = getattr(self.agent_loop, "active_conversation_id", None)
        active_id = str(raw_active_id) if isinstance(raw_active_id, (str, int)) else None
        conversations: List[Dict[str, Any]] = []

        if store and hasattr(store, "list_conversations"):
            try:
                search = request.query.get("search", "").strip() or None
                raw_list = store.list_conversations(limit=100, search_query=search)
                if isinstance(raw_list, list):
                    for c in raw_list:
                        if isinstance(c, dict):
                            cid = str(c.get("id", ""))
                            conversations.append({
                                "id": cid,
                                "title": str(c.get("title", "New Chat")),
                                "created_at": c.get("createdAt"),
                                "updated_at": c.get("updatedAt"),
                                "message_count": c.get("messageCount", 0),
                                "is_active": cid == str(active_id) if active_id else False,
                            })
            except Exception as e:
                logger.debug("Error listing conversations: %s", e)

        return web.json_response({
            "ok": True,
            "active_id": active_id,
            "conversations": conversations,
        })

    async def _handle_switch_conversation(self, request: web.Request) -> web.Response:
        """Switch active conversation thread."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
            conv_id = str(data.get("conversation_id", "")).strip()
            if not conv_id:
                return web.json_response({"ok": False, "error": "Missing conversation_id"}, status=400)

            ok = self.switch_conversation(conv_id)
            if not ok:
                return web.json_response({"ok": False, "error": f"Failed to switch to conversation {conv_id}"}, status=400)

            cid, title, msgs = self.get_active_conversation_info()
            return web.json_response({
                "ok": True,
                "conversation_id": cid,
                "title": title,
                "messages": msgs,
            })
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_delete_conversation(self, request: web.Request) -> web.Response:
        """Delete a conversation thread."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        conv_id = request.match_info.get("id", "").strip()
        if not conv_id:
            return web.json_response({"ok": False, "error": "Missing conversation id"}, status=400)

        ok = False
        if hasattr(self.agent_loop, "delete_conversation"):
            ok = self.agent_loop.delete_conversation(conv_id)
        elif hasattr(self.agent_loop, "conversation_store") and self.agent_loop.conversation_store:
            ok = self.agent_loop.conversation_store.delete_conversation(conv_id)

        return web.json_response({"ok": ok})

    async def _handle_clear(self, request: web.Request) -> web.Response:
        """Clear active conversation turns."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        self.clear_conversation()
        return web.json_response({"ok": True, "message": "Conversation cleared"})

    async def _handle_click(self, request: web.Request) -> web.Response:
        """Inject a mouse click onto host desktop."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
            x = int(data.get("x", 0))
            y = int(data.get("y", 0))
            button = str(data.get("button", "left"))

            if hasattr(self.agent_loop, "inputs") and self.agent_loop.inputs:
                res = self.agent_loop.inputs.click(x=x, y=y, button=button)
                return web.json_response({"ok": res.get("success", True), "result": res})
            return web.json_response({"ok": False, "error": "Input simulator unavailable"}, status=400)
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_screenshot(self, request: web.Request) -> web.Response:
        """Capture current screen and stream as JPEG."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        try:
            img = self.screen.capture()
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=75)
            return web.Response(body=buf.getvalue(), content_type="image/jpeg")
        except Exception as e:
            logger.error("Error capturing screenshot: %s", e)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_prompt(self, request: web.Request) -> web.Response:
        """Accept a remote task prompt to run."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
            prompt = str(data.get("prompt", "")).strip()
            speak_host = bool(data.get("speak_on_host", False))
            if not prompt:
                return web.json_response({"ok": False, "error": "Empty prompt"}, status=400)

            self.dispatch_task(prompt, speak_on_host=speak_host)
            return web.json_response({"ok": True, "message": "Task dispatched"})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_kill(self, request: web.Request) -> web.Response:
        """Trigger emergency stop."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        self.trigger_kill()
        return web.json_response({"ok": True, "message": "Kill switch triggered"})

    async def _handle_reset(self, request: web.Request) -> web.Response:
        """Reset emergency stop."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        self.reset_kill()
        return web.json_response({"ok": True, "message": "Kill switch reset"})

    async def _handle_speak(self, request: web.Request) -> web.Response:
        """Vocalize message through Kokoro TTS."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
            message = str(data.get("message", "")).strip()
            voice = data.get("voice")
            if not message:
                return web.json_response({"ok": False, "error": "Empty message"}, status=400)

            ok = self.speak_message(message, voice=voice)
            if ok:
                return web.json_response({"ok": True, "spoken": message})
            return web.json_response({"ok": False, "error": "Voice subsystem unavailable"}, status=400)
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_tts(self, request: web.Request) -> web.Response:
        """Synthesize text into a WAV stream using Kokoro-82M (Approach A)."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        text = ""
        voice = None
        speed = None

        if request.method == "GET":
            text = request.query.get("text", "").strip()
            voice = request.query.get("voice")
            speed_val = request.query.get("speed")
            if speed_val:
                try:
                    speed = float(speed_val)
                except ValueError:
                    pass
        elif request.method == "POST":
            try:
                data = await request.json()
                text = str(data.get("text", "")).strip()
                voice = data.get("voice")
                if "speed" in data and data["speed"] is not None:
                    speed = float(data["speed"])
            except Exception:
                return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

        if not text:
            return web.json_response({"ok": False, "error": "Missing or empty 'text' parameter"}, status=400)

        voice_mgr = getattr(self.agent_loop, "voice", None)
        tts = getattr(voice_mgr, "tts", None) if voice_mgr else None
        if not tts or not getattr(tts, "is_available", False):
            return web.json_response({"ok": False, "error": "Kokoro TTS is not available on host"}, status=503)

        loop = asyncio.get_running_loop()
        wav_path = None
        try:
            wav_path = await loop.run_in_executor(
                None, lambda: tts.synthesize(text, voice=voice, speed=speed)
            )
            with open(wav_path, "rb") as f:
                audio_bytes = f.read()
        except ValueError as ve:
            return web.json_response({"ok": False, "error": str(ve)}, status=400)
        except Exception as exc:
            logger.error("TTS synthesis error: %s", exc)
            return web.json_response({"ok": False, "error": f"Synthesis failed: {exc}"}, status=500)
        finally:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except Exception:
                    pass

        return web.Response(
            body=audio_bytes,
            content_type="audio/wav",
            headers={
                "Content-Disposition": 'inline; filename="speech.wav"',
                "Cache-Control": "no-cache",
            },
        )

    async def _handle_tts_split(self, request: web.Request) -> web.Response:
        """Split text into progressive TTS playback chunks using OptimalX chunker."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        text = ""
        if request.method == "GET":
            text = request.query.get("text", "").strip()
        elif request.method == "POST":
            try:
                data = await request.json()
                text = str(data.get("text", "")).strip()
            except Exception:
                return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

        from eidos.voice.chunker import split_for_tts_playback, text_for_speech
        cleaned = text_for_speech(text)
        pieces = split_for_tts_playback(cleaned)
        return web.json_response({"ok": True, "chunks": pieces, "total": len(pieces)})

    async def _handle_voices(self, request: web.Request) -> web.Response:
        """Return available Kokoro TTS voices."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        voice_mgr = getattr(self.agent_loop, "voice", None)
        current_default = getattr(getattr(voice_mgr, "tts", None), "default_voice", DEFAULT_VOICE_ID)
        return web.json_response({
            "ok": True,
            "default_voice": current_default,
            "voices": list_voices(),
        })

    async def _handle_stt(self, request: web.Request) -> web.Response:
        """Transcribe uploaded audio (WAV, WEBM, OGG) using local Parakeet STT."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        voice_mgr = getattr(self.agent_loop, "voice", None)
        stt = getattr(voice_mgr, "stt", None) if voice_mgr else None
        if not stt or not getattr(stt, "is_available", False):
            return web.json_response({"ok": False, "error": "Parakeet STT is not available on host"}, status=503)

        audio_bytes = b""
        content_type = request.content_type or ""

        if "multipart/form-data" in content_type:
            reader = await request.multipart()
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.name in ("audio", "file"):
                    audio_bytes = await part.read()
                    break
        else:
            audio_bytes = await request.read()

        if not audio_bytes:
            return web.json_response({"ok": False, "error": "Empty audio data received"}, status=400)

        in_path = None
        wav_path = None
        loop = asyncio.get_running_loop()

        try:
            with tempfile.NamedTemporaryFile(suffix=".tmp", delete=False) as tf:
                tf.write(audio_bytes)
                in_path = tf.name

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                wav_path = tf.name

            def _convert_and_transcribe():
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-i", in_path,
                    "-ar", "16000",
                    "-ac", "1",
                    "-c:a", "pcm_s16le",
                    wav_path,
                ]
                proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15)
                if proc.returncode != 0:
                    err_msg = proc.stderr.decode("utf-8", errors="replace")[:300]
                    raise RuntimeError(f"Audio conversion failed: {err_msg}")
                return stt.transcribe(wav_path)

            transcribed = await loop.run_in_executor(None, _convert_and_transcribe)
            return web.json_response({"ok": True, "text": transcribed})
        except Exception as exc:
            logger.error("STT transcription error: %s", exc)
            return web.json_response({"ok": False, "error": f"Transcription failed: {exc}"}, status=500)
        finally:
            for p in (in_path, wav_path):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

    async def _handle_get_confirmations(self, request: web.Request) -> web.Response:
        """Return all active confirmation requests."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        with self._confirm_lock:
            items = []
            for cid, c in self._pending_confirmations.items():
                items.append({
                    "id": cid,
                    "action_type": c["action_type"],
                    "details": c["details"],
                    "risk_level": c["risk_level"],
                    "reason": c["reason"],
                    "timestamp": c["timestamp"],
                })
            return web.json_response({"ok": True, "confirmations": items})

    async def _handle_post_confirmation(self, request: web.Request) -> web.Response:
        """Approve or deny a pending confirmation."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        cid = request.match_info.get("id", "")
        data = await request.json()
        approved = bool(data.get("approved", False))
        allowlist = bool(data.get("allowlist", False))

        resolved = self.resolve_confirmation(cid, approved=approved, allowlist=allowlist)
        if not resolved:
            return web.json_response({"ok": False, "error": f"Confirmation '{cid}' not found or already resolved"}, status=404)

        return web.json_response({"ok": True, "approved": approved, "allowlist": allowlist})

    # =========================================================================
    # Task Dispatch & Agent Controls
    # =========================================================================

    def dispatch_task(self, prompt: str, speak_on_host: bool = False):
        """Execute a user prompt asynchronously in the agent loop."""
        self.current_task = prompt
        self.current_state = "thinking"
        self.broadcast_event("status_change", {"state": "thinking", "details": f"Task: {prompt}"})
        self.broadcast_event("log", {"message": f"Remote task requested: '{prompt}'"})

        def _run():
            try:
                # If agent_loop supports speak_summary, pass speak_on_host so remote tasks don't blare on host PC
                run_task_fn = getattr(self.agent_loop, "run_task", None)
                if callable(run_task_fn):
                    import inspect
                    try:
                        sig = inspect.signature(run_task_fn)
                        has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                        if "speak_summary" in sig.parameters or has_kwargs:
                            res = run_task_fn(prompt, speak_summary=speak_on_host)
                        else:
                            res = run_task_fn(prompt)
                    except (ValueError, TypeError):
                        try:
                            res = run_task_fn(prompt, speak_summary=speak_on_host)
                        except TypeError:
                            res = run_task_fn(prompt)
                else:
                    res = {"success": False, "summary": "No agent loop available"}

                summary = res.get("summary", "Done") if isinstance(res, dict) else str(res)
                success = res.get("success", True) if isinstance(res, dict) else True
                self.current_state = "idle"
                self.broadcast_event("status_change", {"state": "idle", "details": summary})
                self.broadcast_event("log", {"message": f"Task completed: {summary}"})
                self.broadcast_event("task_finished", {"summary": summary, "success": success})
            except Exception as e:
                self.current_state = "error"
                self.broadcast_event("status_change", {"state": "error", "details": str(e)})
                self.broadcast_event("log", {"message": f"Task error: {e}"})
                self.broadcast_event("task_finished", {"summary": str(e), "success": False})

        threading.Thread(target=_run, daemon=True, name="GatewayTaskWorker").start()

    def trigger_kill(self):
        """Trigger emergency stop."""
        if hasattr(self.agent_loop, "kill_switch"):
            self.agent_loop.kill_switch.trigger()
        self.current_state = "killed"
        self.broadcast_event("status_change", {"state": "killed", "details": "Emergency stop tripped remotely!"})
        self.broadcast_event("log", {"message": "EMERGENCY KILL SWITCH TRIPPED!"})

    def reset_kill(self):
        """Reset emergency stop."""
        if hasattr(self.agent_loop, "kill_switch"):
            self.agent_loop.kill_switch.reset()
        self.current_state = "idle"
        self.broadcast_event("status_change", {"state": "idle", "details": "Emergency stop reset"})
        self.broadcast_event("log", {"message": "Emergency stop reset."})

    def speak_message(self, message: str, voice: Optional[str] = None) -> bool:
        """Vocalize a message via Kokoro TTS."""
        if hasattr(self.agent_loop, "voice") and self.agent_loop.voice:
            try:
                self.agent_loop.voice.speak(message, voice=voice, blocking=False)
                return True
            except Exception as e:
                logger.error("Error vocalizing message: %s", e)
        return False

    def start_new_chat(self, title: str = "New Chat") -> str:
        """Create a fresh conversation thread in agent loop and inform all clients."""
        conv_id = ""
        if hasattr(self.agent_loop, "start_new_conversation") and callable(self.agent_loop.start_new_conversation):
            try:
                conv_id = self.agent_loop.start_new_conversation(title=title)
            except Exception as e:
                logger.error("Error in agent_loop.start_new_conversation: %s", e)
        elif hasattr(self.agent_loop, "clear_history") and callable(self.agent_loop.clear_history):
            self.agent_loop.clear_history()

        if hasattr(self.agent_loop, "conversation_history") and isinstance(self.agent_loop.conversation_history, list):
            self.agent_loop.conversation_history.clear()

        self.broadcast_event("conversation_changed", {
            "conversation_id": conv_id,
            "title": title,
            "messages": [],
        })
        self.broadcast_event("log", {"message": f"Started new conversation: '{title}'"})
        return conv_id

    def switch_conversation(self, conv_id: str) -> bool:
        """Switch active conversation in agent loop and broadcast to clients."""
        if hasattr(self.agent_loop, "switch_conversation") and callable(self.agent_loop.switch_conversation):
            try:
                ok = self.agent_loop.switch_conversation(conv_id)
                if ok:
                    cid, title, msgs = self.get_active_conversation_info()
                    self.broadcast_event("conversation_changed", {
                        "conversation_id": cid,
                        "title": title,
                        "messages": msgs,
                    })
                    self.broadcast_event("log", {"message": f"Switched conversation to: '{title}'"})
                    return True
            except Exception as e:
                logger.error("Error in agent_loop.switch_conversation: %s", e)
        return False

    def clear_conversation(self):
        """Clear conversation history and inform clients."""
        if hasattr(self.agent_loop, "clear_history") and callable(self.agent_loop.clear_history):
            try:
                self.agent_loop.clear_history()
            except Exception as e:
                logger.debug("Error in agent_loop.clear_history: %s", e)
        if hasattr(self.agent_loop, "conversation_history") and isinstance(self.agent_loop.conversation_history, list):
            self.agent_loop.conversation_history.clear()

        self.broadcast_event("conversation_cleared", {})
        self.broadcast_event("log", {"message": "Active conversation history cleared."})

    # =========================================================================
    # Agent Observer Callbacks
    # =========================================================================

    def _on_agent_thought(self, thought: str):
        self.broadcast_event("thought_chunk", {"chunk": thought, "is_thought": True})

    def _on_agent_response(self, response: str):
        self.broadcast_event("token_chunk", {"chunk": response, "is_thought": False})

    def _on_agent_tool_start(self, tool_name: str, arguments: Any):
        self.broadcast_event("tool_start", {"tool": tool_name, "arguments": arguments})

    def _on_agent_tool_end(self, tool_name: str, result: Any):
        self.broadcast_event("tool_end", {"tool": tool_name, "result": str(result)[:500]})

    def _on_agent_task_start(self, goal: str):
        self.broadcast_event("task_start", {"goal": goal})

    def _on_agent_task_finish(self, summary: str, success: bool):
        self.broadcast_event("task_finished", {"summary": summary, "success": success})

    def _on_conversation_title_changed(self, title: str):
        self.broadcast_event("conversation_title_changed", {"title": title})

    def _on_conversation_changed(self, conv_id: Optional[str] = None, title: str = "New Chat"):
        cid, cur_title, msgs = self.get_active_conversation_info()
        self.broadcast_event("conversation_changed", {
            "conversation_id": cid or conv_id,
            "title": cur_title or title,
            "messages": msgs,
        })

    # =========================================================================
    # WebSocket Event Stream & Bidirectional Dispatch
    # =========================================================================

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket connection handler for live event streaming and remote commands."""
        if not self._verify_auth(request):
            return web.Response(text="Unauthorized", status=401)

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        self._ws_clients.add(ws)
        logger.info("Remote gateway client connected to WebSocket (%d active)", len(self._ws_clients))

        is_killed = False
        if hasattr(self.agent_loop, "kill_switch"):
            is_killed = self.agent_loop.kill_switch.is_triggered()

        # Send initial status
        active_cid, active_title, _ = self.get_active_conversation_info()
        await ws.send_json({
            "event": "status_change",
            "state": "killed" if is_killed else self.current_state,
            "details": self.current_task,
            "killed": is_killed,
            "conversation_id": active_cid,
            "conversation_title": active_title,
        })

        # Send active confirmations if any
        with self._confirm_lock:
            if self._pending_confirmations:
                items = [{
                    "id": cid,
                    "action_type": c["action_type"],
                    "details": c["details"],
                    "risk_level": c["risk_level"],
                    "reason": c["reason"],
                    "timestamp": c["timestamp"],
                } for cid, c in self._pending_confirmations.items()]
                await ws.send_json({"event": "confirmations_list", "confirmations": items})

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        msg_type = data.get("type", "")
                        if msg_type == "ping":
                            await ws.send_json({"event": "pong", "time": time.time()})
                        elif msg_type == "prompt":
                            prompt = str(data.get("prompt", "")).strip()
                            speak_host = bool(data.get("speak_on_host", False))
                            if prompt:
                                self.dispatch_task(prompt, speak_on_host=speak_host)
                        elif msg_type == "kill":
                            self.trigger_kill()
                        elif msg_type == "reset":
                            self.reset_kill()
                        elif msg_type == "resolve_confirmation":
                            cid = str(data.get("id", ""))
                            approved = bool(data.get("approved", False))
                            allowlist = bool(data.get("allowlist", False))
                            self.resolve_confirmation(cid, approved=approved, allowlist=allowlist)
                        elif msg_type == "speak":
                            msg_text = str(data.get("message", "")).strip()
                            voice = data.get("voice")
                            if msg_text:
                                self.speak_message(msg_text, voice=voice)
                        elif msg_type == "clear":
                            self.clear_conversation()
                        elif msg_type == "new_chat":
                            title = str(data.get("title", "New Chat")).strip() or "New Chat"
                            self.start_new_chat(title=title)
                        elif msg_type == "switch_conversation":
                            conv_id = str(data.get("conversation_id", "")).strip()
                            if conv_id:
                                self.switch_conversation(conv_id)
                        elif msg_type == "get_conversations":
                            store = getattr(self.agent_loop, "conversation_store", None)
                            active_id = getattr(self.agent_loop, "active_conversation_id", None)
                            convs = []
                            if store and hasattr(store, "list_conversations"):
                                try:
                                    search = data.get("search")
                                    raw_list = store.list_conversations(limit=100, search_query=search)
                                    convs = [{
                                        "id": str(c.get("id")),
                                        "title": c.get("title", "New Chat"),
                                        "created_at": c.get("createdAt"),
                                        "updated_at": c.get("updatedAt"),
                                        "message_count": c.get("messageCount", 0),
                                        "is_active": str(c.get("id")) == str(active_id),
                                    } for c in raw_list]
                                except Exception:
                                    pass
                            await ws.send_json({
                                "event": "conversations_list",
                                "active_id": active_id,
                                "conversations": convs,
                            })
                        elif msg_type in ("get_history", "sync"):
                            cid, title, msgs = self.get_active_conversation_info()
                            await ws.send_json({
                                "event": "sync_history",
                                "conversation_id": cid,
                                "title": title,
                                "messages": msgs,
                            })
                        elif msg_type == "get_status":
                            await ws.send_json({
                                "event": "status_change",
                                "state": "killed" if is_killed else self.current_state,
                                "details": self.current_task,
                                "killed": is_killed,
                            })
                        elif msg_type == "get_confirmations":
                            with self._confirm_lock:
                                items = [{
                                    "id": cid,
                                    "action_type": c["action_type"],
                                    "details": c["details"],
                                    "risk_level": c["risk_level"],
                                    "reason": c["reason"],
                                    "timestamp": c["timestamp"],
                                } for cid, c in self._pending_confirmations.items()]
                            await ws.send_json({"event": "confirmations_list", "confirmations": items})
                    except Exception as e:
                        logger.debug("Error processing client WebSocket message: %s", e)
                elif msg.type == WSMsgType.ERROR:
                    logger.debug("WebSocket error: %s", ws.exception())
        finally:
            self._ws_clients.discard(ws)
            logger.info("Remote gateway client disconnected (%d remaining)", len(self._ws_clients))

        return ws

    def broadcast_event(self, event: str, data: Dict[str, Any]):
        """Broadcast an event to all connected WebSocket clients (thread-safe)."""
        if not self._loop or not self._ws_clients:
            return

        payload = {"event": event, **data}
        async def _send():
            dead = set()
            for ws in list(self._ws_clients):
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead.add(ws)
            self._ws_clients.difference_update(dead)

        asyncio.run_coroutine_threadsafe(_send(), self._loop)

    def request_confirmation(
        self,
        action_type: str,
        details: Dict[str, Any],
        risk_level: int,
        reason: str,
        timeout_seconds: float = 120.0,
        cancel_event: Optional[threading.Event] = None,
    ) -> Optional[Tuple[bool, bool]]:
        """
        Register a confirmation request and wait for remote resolution.
        Returns (approved, remember) or None if timed out.
        """
        cid = str(uuid.uuid4())[:8]
        event = threading.Event()
        record = {
            "id": cid,
            "action_type": action_type,
            "details": details,
            "risk_level": risk_level,
            "reason": reason,
            "timestamp": time.time(),
            "event": event,
            "result": None,
        }

        with self._confirm_lock:
            self._pending_confirmations[cid] = record

        self.broadcast_event("confirmation_requested", {
            "id": cid,
            "action_type": action_type,
            "details": details,
            "risk_level": risk_level,
            "reason": reason,
        })

        logger.info("Registered remote confirmation request %s (waiting up to %.1fs)", cid, timeout_seconds)
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            if event.is_set() or (cancel_event and cancel_event.is_set()):
                break
            time.sleep(0.05)

        with self._confirm_lock:
            res = record.get("result")
            self._pending_confirmations.pop(cid, None)

        self.broadcast_event("confirmation_resolved", {"id": cid, "result": res})
        return res if event.is_set() and res is not None else None

    def resolve_confirmation(self, request_id: str, approved: bool, allowlist: bool = False) -> bool:
        """Resolve a pending confirmation request by id."""
        with self._confirm_lock:
            record = self._pending_confirmations.get(request_id)
            if not record:
                return False

            record["result"] = (approved, allowlist)
            record["event"].set()
            return True

    def is_running(self) -> bool:
        """Return True if background server thread is active."""
        return self._thread is not None and self._thread.is_alive()

    def create_confirmation_callback(self) -> Optional[Callable[..., Optional[Tuple[bool, bool]]]]:
        """Create callback suitable for Gatekeeper.custom_prompt_callback."""
        if not self.is_running():
            return None

        def web_gateway_callback(
            action_type: str, details: Dict[str, Any], risk_level: RiskLevel, reason: str
        ) -> Optional[Tuple[bool, bool]]:
            return self.request_confirmation(action_type, details, int(risk_level), reason)

        return web_gateway_callback

    def create_composite_confirmation_callback(
        self, desktop_callback: Optional[Callable[..., Tuple[bool, bool]]] = None
    ) -> Callable[..., Tuple[bool, bool]]:
        """
        Creates a dual-channel confirmation callback:
        Presents the confirmation on both Desktop GUI modal AND Remote Mobile Gateway.
        Whichever responds first decides the outcome!
        """
        def dual_callback(
            action_type: str, details: Dict[str, Any], risk_level: RiskLevel, reason: str
        ) -> Tuple[bool, bool]:
            outcome_queue: List[Tuple[bool, bool]] = []
            done_event = threading.Event()

            # 1. Spawn Desktop Prompt in background thread
            def _desktop():
                if desktop_callback:
                    try:
                        approved, remember = desktop_callback(action_type, details, risk_level, reason)
                        if not done_event.is_set():
                            outcome_queue.append((approved, remember))
                            done_event.set()
                    except Exception as e:
                        logger.error("Error in desktop confirmation: %s", e)

            # 2. Spawn Remote Gateway Prompt in background thread
            def _remote():
                try:
                    res = self.request_confirmation(
                        action_type, details, int(risk_level), reason, timeout_seconds=180.0, cancel_event=done_event
                    )
                    if res is not None and not done_event.is_set():
                        outcome_queue.append(res)
                        done_event.set()
                except Exception as e:
                    logger.error("Error in remote confirmation: %s", e)

            t_desk = threading.Thread(target=_desktop, daemon=True)
            t_rem = threading.Thread(target=_remote, daemon=True)
            t_desk.start()
            t_rem.start()

            done_event.wait(timeout=180.0)

            if outcome_queue:
                return outcome_queue[0]

            logger.warning("Dual confirmation timed out without approval.")
            return False, False

        return dual_callback

    def start(self):
        """Start the remote gateway server in a background daemon thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Remote Gateway server already running.")
            return

        # Hook agent observer callbacks if supported
        if hasattr(self.agent_loop, "register_callback"):
            try:
                self.agent_loop.register_callback("on_thought", self._on_agent_thought)
                self.agent_loop.register_callback("on_response", self._on_agent_response)
                self.agent_loop.register_callback("on_tool_start", self._on_agent_tool_start)
                self.agent_loop.register_callback("on_tool_end", self._on_agent_tool_end)
                self.agent_loop.register_callback("on_task_start", self._on_agent_task_start)
                self.agent_loop.register_callback("on_task_finish", self._on_agent_task_finish)
                self.agent_loop.register_callback("on_conversation_title_changed", self._on_conversation_title_changed)
                self.agent_loop.register_callback("on_conversation_changed", self._on_conversation_changed)
            except Exception as e:
                logger.debug("Could not register all agent callbacks: %s", e)

        def _run_server():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            self._app = web.Application(middlewares=[cors_middleware])
            self._app.router.add_get("/api/status", self._handle_status)
            self._app.router.add_get("/api/history", self._handle_history)
            self._app.router.add_post("/api/conversations/new", self._handle_new_chat)
            self._app.router.add_post("/api/new_chat", self._handle_new_chat)
            self._app.router.add_get("/api/conversations", self._handle_list_conversations)
            self._app.router.add_post("/api/conversations/switch", self._handle_switch_conversation)
            self._app.router.add_delete("/api/conversations/{id}", self._handle_delete_conversation)
            self._app.router.add_post("/api/conversations/{id}/delete", self._handle_delete_conversation)
            self._app.router.add_post("/api/clear", self._handle_clear)
            self._app.router.add_get("/api/screenshot", self._handle_screenshot)
            self._app.router.add_post("/api/click", self._handle_click)
            self._app.router.add_post("/api/prompt", self._handle_prompt)
            self._app.router.add_post("/api/kill", self._handle_kill)
            self._app.router.add_post("/api/reset", self._handle_reset)
            self._app.router.add_post("/api/speak", self._handle_speak)
            self._app.router.add_get("/api/tts", self._handle_tts)
            self._app.router.add_post("/api/tts", self._handle_tts)
            self._app.router.add_get("/api/tts/split", self._handle_tts_split)
            self._app.router.add_post("/api/tts/split", self._handle_tts_split)
            self._app.router.add_get("/api/voices", self._handle_voices)
            self._app.router.add_post("/api/stt", self._handle_stt)
            self._app.router.add_get("/api/confirmations", self._handle_get_confirmations)
            self._app.router.add_post("/api/confirmations/{id}", self._handle_post_confirmation)
            self._app.router.add_get("/ws", self._handle_ws)

            # Static assets and SPA routes
            if WEB_DIST_DIR.is_dir() and (WEB_DIST_DIR / "assets").is_dir():
                self._app.router.add_static("/assets", WEB_DIST_DIR / "assets", show_index=False)
            if WEB_DIST_DIR.is_dir() and (WEB_DIST_DIR / "icons").is_dir():
                self._app.router.add_static("/icons", WEB_DIST_DIR / "icons", show_index=False)

            self._app.router.add_get("/sw.js", self._handle_service_worker)
            self._app.router.add_get("/manifest.json", self._handle_manifest)
            self._app.router.add_get("/", self._handle_dashboard)
            # Catch-all for SPA client routing
            self._app.router.add_get("/{tail:.*}", self._handle_spa_fallback)

            # Silence aiohttp access logger to prevent leaking tokens or flooding terminal
            logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

            self._runner = web.AppRunner(self._app, access_log=None)
            self._loop.run_until_complete(self._runner.setup())
            self._site = web.TCPSite(self._runner, self.host, self.port)
            self._loop.run_until_complete(self._site.start())

            logger.info("🚀 EidosBot Remote Gateway running at http://%s:%d (Auth Enabled)", self.host, self.port)
            self._loop.run_forever()

        self._thread = threading.Thread(target=_run_server, daemon=True)
        self._thread.start()

    def stop(self):
        """Gracefully stop server and close WebSockets."""
        if self._loop and self._runner:
            async def _cleanup():
                for ws in list(self._ws_clients):
                    await ws.close()
                await self._runner.cleanup()

            asyncio.run_coroutine_threadsafe(_cleanup(), self._loop)
            self._loop.call_soon_threadsafe(self._loop.stop)
            logger.info("Remote Gateway stopped.")
