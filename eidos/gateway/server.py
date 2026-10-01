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
import secrets
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import uuid

from aiohttp import web, WSMsgType

from eidos.perception.screen import ScreenCapture
from eidos.safety.risk_classifier import RiskLevel

logger = logging.getLogger(__name__)

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
      <button id="btn-kill" class="btn btn-red" onclick="toggleKillSwitch()">🛑 Stop</button>
    </div>
    <div id="status-details" style="font-size: 0.85rem; color: var(--text-dim); margin-top: 6px;">Idle</div>
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


class RemoteGatewayServer:
    """HTTP & WebSocket Remote Gateway allowing full mobile management of EidosBot."""

    def __init__(
        self,
        agent_loop: Any,
        host: str = "0.0.0.0",
        port: int = 8420,
        auth_token: Optional[str] = None,
        screen_capture: Optional[ScreenCapture] = None,
    ):
        self.agent_loop = agent_loop
        self.host = host
        self.port = port
        self.auth_token = auth_token or secrets.token_hex(16)
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
        """Serve the mobile-friendly web dashboard."""
        return web.Response(text=HTML_DASHBOARD, content_type="text/html")

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
            if not prompt:
                return web.json_response({"ok": False, "error": "Empty prompt"}, status=400)

            self.current_task = prompt
            self.broadcast_event("status_change", {"state": "thinking", "details": f"Task: {prompt}"})
            self.broadcast_event("log", {"message": f"Remote task requested: '{prompt}'"})

            # Execute task in background thread
            def _run():
                try:
                    res = self.agent_loop.run_task(prompt)
                    summary = res.get("summary", "Done")
                    self.current_state = "idle"
                    self.broadcast_event("status_change", {"state": "idle", "details": summary})
                    self.broadcast_event("log", {"message": f"Task completed: {summary}"})
                except Exception as e:
                    self.current_state = "error"
                    self.broadcast_event("status_change", {"state": "error", "details": str(e)})
                    self.broadcast_event("log", {"message": f"Task error: {e}"})

            threading.Thread(target=_run, daemon=True).start()
            return web.json_response({"ok": True, "message": "Task dispatched"})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_kill(self, request: web.Request) -> web.Response:
        """Trigger emergency stop."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        if hasattr(self.agent_loop, "kill_switch"):
            self.agent_loop.kill_switch.trigger()

        self.current_state = "killed"
        self.broadcast_event("status_change", {"state": "killed", "details": "Emergency stop tripped remotely!"})
        return web.json_response({"ok": True, "message": "Kill switch triggered"})

    async def _handle_reset(self, request: web.Request) -> web.Response:
        """Reset emergency stop."""
        if not self._verify_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        if hasattr(self.agent_loop, "kill_switch"):
            self.agent_loop.kill_switch.reset()

        self.current_state = "idle"
        self.broadcast_event("status_change", {"state": "idle", "details": "Emergency stop reset"})
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

            if hasattr(self.agent_loop, "voice") and self.agent_loop.voice:
                self.agent_loop.voice.speak(message, voice=voice, blocking=False)
                return web.json_response({"ok": True, "spoken": message})
            else:
                return web.json_response({"ok": False, "error": "Voice subsystem unavailable"}, status=400)
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

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

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket connection handler for live event streaming."""
        if not self._verify_auth(request):
            return web.Response(text="Unauthorized", status=401)

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        self._ws_clients.add(ws)
        logger.info("Remote gateway client connected to WebSocket (%d active)", len(self._ws_clients))

        # Send initial status
        await ws.send_json({
            "event": "status_change",
            "state": self.current_state,
            "details": self.current_task,
        })

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    pass
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

        def _run_server():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            self._app = web.Application()
            self._app.router.add_get("/", self._handle_dashboard)
            self._app.router.add_get("/api/status", self._handle_status)
            self._app.router.add_get("/api/screenshot", self._handle_screenshot)
            self._app.router.add_post("/api/prompt", self._handle_prompt)
            self._app.router.add_post("/api/kill", self._handle_kill)
            self._app.router.add_post("/api/reset", self._handle_reset)
            self._app.router.add_post("/api/speak", self._handle_speak)
            self._app.router.add_get("/api/confirmations", self._handle_get_confirmations)
            self._app.router.add_post("/api/confirmations/{id}", self._handle_post_confirmation)
            self._app.router.add_get("/ws", self._handle_ws)

            self._runner = web.AppRunner(self._app)
            self._loop.run_until_complete(self._runner.setup())
            self._site = web.TCPSite(self._runner, self.host, self.port)
            self._loop.run_until_complete(self._site.start())

            logger.info("🚀 EidosBot Remote Gateway running at http://%s:%d (Token: %s)", self.host, self.port, self.auth_token)
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
