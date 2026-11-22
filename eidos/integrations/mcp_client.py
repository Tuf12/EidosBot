"""Model Context Protocol (MCP) client manager with dedicated daemon event loop (PROJECT_JOBS.md Section 13)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Tuple
import aiohttp

from eidos.integrations.mcp_config import MCPConfigManager, MCPServerConfig

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2024-11-05"
MAX_DIRECT_TOOLS_THRESHOLD = 20


class BaseMCPSession:
    """Abstract base class for persistent MCP server sessions."""

    def __init__(self, name: str, config: MCPServerConfig):
        self.name = name
        self.config = config
        self.is_connected = False
        self._request_id = 0
        self._pending_requests: Dict[int, asyncio.Future[Dict[str, Any]]] = {}

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def connect(self) -> None:
        raise NotImplementedError

    async def list_tools(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError


class StdioMCPSession(BaseMCPSession):
    """Stdio transport session running a local MCP server subprocess via stdin/stdout JSON-RPC 2.0."""

    def __init__(self, name: str, config: MCPServerConfig):
        super().__init__(name, config)
        self.process: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        if self.is_connected:
            return

        cmd = self.config.command
        if not cmd:
            raise ValueError(f"Stdio MCP server '{self.name}' must provide 'command'")

        args = self.config.args or []
        env = os.environ.copy()
        if self.config.env:
            env.update(self.config.env)

        cwd = self.config.cwd or os.getcwd()

        try:
            self.process = await asyncio.create_subprocess_exec(
                cmd,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
            )
        except Exception as e:
            logger.error("Failed to spawn Stdio MCP server '%s' (%s): %s", self.name, cmd, e)
            raise

        self._reader_task = asyncio.create_task(self._read_stdout_loop())

        # Perform JSON-RPC 2.0 handshake: initialize
        init_req_id = self._next_id()
        init_payload = {
            "jsonrpc": "2.0",
            "id": init_req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "EidosBot",
                    "version": "1.0.0",
                },
            },
        }

        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[init_req_id] = fut
        await self._send_json(init_payload)

        try:
            init_res = await asyncio.wait_for(fut, timeout=10.0)
            if "error" in init_res:
                raise RuntimeError(f"MCP initialize rejected by '{self.name}': {init_res['error']}")
        except asyncio.TimeoutError:
            raise TimeoutError(f"Handshake timeout connecting to MCP server '{self.name}'")

        # Send notifications/initialized
        notif_payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        await self._send_json(notif_payload)
        self.is_connected = True
        logger.info("Connected Stdio MCP server '%s'", self.name)

    async def _send_json(self, payload: Dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise RuntimeError(f"MCP server '{self.name}' stdin is not available")
        line = json.dumps(payload) + "\n"
        self.process.stdin.write(line.encode("utf-8"))
        await self.process.stdin.drain()

    async def _read_stdout_loop(self) -> None:
        """Continuously read line-delimited JSON-RPC messages from server stdout."""
        if not self.process or not self.process.stdout:
            return

        while True:
            try:
                line = await self.process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue

                try:
                    msg = json.loads(text)
                except Exception:
                    logger.debug("Non-JSON output from MCP server '%s': %s", self.name, text)
                    continue

                req_id = msg.get("id")
                if req_id is not None and req_id in self._pending_requests:
                    fut = self._pending_requests.pop(req_id)
                    if not fut.done():
                        fut.set_result(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Error in MCP reader loop for '%s': %s", self.name, e)
                break

    async def list_tools(self) -> List[Dict[str, Any]]:
        if not self.is_connected:
            await self.connect()

        req_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/list",
            "params": {},
        }
        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut
        await self._send_json(payload)

        try:
            res = await asyncio.wait_for(fut, timeout=15.0)
            if "error" in res:
                logger.warning("tools/list error from '%s': %s", self.name, res["error"])
                return []
            result = res.get("result", {})
            return result.get("tools", [])
        except asyncio.TimeoutError:
            self._pending_requests.pop(req_id, None)
            logger.warning("tools/list timed out for '%s'", self.name)
            return []

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_connected:
            await self.connect()

        req_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
            },
        }
        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut
        await self._send_json(payload)

        try:
            res = await asyncio.wait_for(fut, timeout=30.0)
            if "error" in res:
                return {"isError": True, "error": res["error"]}
            result = res.get("result", {})
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(req_id, None)
            return {"isError": True, "error": f"Tool call '{name}' timed out after 30s"}

    async def close(self) -> None:
        self.is_connected = False
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None

        if self.process:
            try:
                if self.process.stdin:
                    self.process.stdin.close()
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=2.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            try:
                if hasattr(self.process, "_transport") and self.process._transport:
                    self.process._transport.close()
            except Exception:
                pass
            self.process = None
            await asyncio.sleep(0.05)

        for fut in self._pending_requests.values():
            if not fut.done():
                fut.cancel()
        self._pending_requests.clear()
        logger.info("Closed Stdio MCP server '%s'", self.name)


class HttpSseMCPSession(BaseMCPSession):
    """HTTP/SSE transport session for remote MCP endpoints (e.g. Gmail remote endpoint)."""

    def __init__(self, name: str, config: MCPServerConfig):
        super().__init__(name, config)
        self._session: Optional[aiohttp.ClientSession] = None

    async def connect(self) -> None:
        if self.is_connected:
            return

        if not self.config.url:
            raise ValueError(f"HTTP/SSE MCP server '{self.name}' must provide 'url'")

        headers = {"Content-Type": "application/json"}
        if self.config.headers:
            headers.update(self.config.headers)

        self._session = aiohttp.ClientSession(headers=headers)

        # Handshake: initialize
        init_req_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": init_req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "EidosBot",
                    "version": "1.0.0",
                },
            },
        }

        try:
            async with self._session.post(self.config.url, json=payload, timeout=10.0) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise RuntimeError(f"HTTP {resp.status} on initialize: {body}")
                data = await resp.json()
                if "error" in data:
                    raise RuntimeError(f"MCP initialize rejected by '{self.name}': {data['error']}")
        except Exception as e:
            await self.close()
            raise RuntimeError(f"Failed to connect HTTP MCP server '{self.name}': {e}")

        # Send notifications/initialized
        notif_payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        try:
            async with self._session.post(self.config.url, json=notif_payload, timeout=5.0) as _:
                pass
        except Exception as e:
            logger.debug("Failed sending notifications/initialized to '%s': %s", self.name, e)

        self.is_connected = True
        logger.info("Connected HTTP/SSE MCP server '%s' (%s)", self.name, self.config.url)

    async def list_tools(self) -> List[Dict[str, Any]]:
        if not self.is_connected or not self._session or not self.config.url:
            await self.connect()

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
            "params": {},
        }
        try:
            assert self._session is not None and self.config.url is not None
            async with self._session.post(self.config.url, json=payload, timeout=15.0) as resp:
                if resp.status >= 400:
                    return []
                data = await resp.json()
                return data.get("result", {}).get("tools", [])
        except Exception as e:
            logger.warning("tools/list error on HTTP MCP server '%s': %s", self.name, e)
            return []

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_connected or not self._session or not self.config.url:
            await self.connect()

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
            },
        }
        try:
            assert self._session is not None and self.config.url is not None
            async with self._session.post(self.config.url, json=payload, timeout=30.0) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    return {"isError": True, "error": f"HTTP {resp.status}: {text}"}
                data = await resp.json()
                if "error" in data:
                    return {"isError": True, "error": data["error"]}
                return data.get("result", {})
        except Exception as e:
            return {"isError": True, "error": f"HTTP MCP tool call failed: {e}"}

    async def close(self) -> None:
        self.is_connected = False
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
        logger.info("Closed HTTP/SSE MCP server '%s'", self.name)


class MCPClientManager:
    """Manages active MCP server sessions on a dedicated daemon thread with its own asyncio event loop."""

    def __init__(self, config_manager: Optional[MCPConfigManager] = None):
        self.config_manager = config_manager or MCPConfigManager()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready_event = threading.Event()
        self._sessions: Dict[str, BaseMCPSession] = {}
        self._tool_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._annotation_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._lock = threading.RLock()

        self._ensure_running()

    def _ensure_running(self) -> None:
        """Start the background daemon thread and event loop if not running."""
        with self._lock:
            if self._thread and self._thread.is_alive() and self._loop and self._loop.is_running():
                return

            self._ready_event.clear()
            self._thread = threading.Thread(
                target=self._run_event_loop,
                daemon=True,
                name="EidosMCPClientThread",
            )
            self._thread.start()
            if not self._ready_event.wait(timeout=5.0):
                logger.error("Timed out waiting for MCP background event loop to start")

    def _run_event_loop(self) -> None:
        """Target for background daemon thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready_event.set()
        try:
            self._loop.run_forever()
        finally:
            try:
                # Cleanup pending tasks
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
                if pending:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                self._loop.close()
            except Exception:
                pass

    def _get_or_create_session(self, server_name: str) -> BaseMCPSession:
        """Retrieve existing or construct a new session for named server."""
        with self._lock:
            if server_name in self._sessions:
                return self._sessions[server_name]

            cfg = self.config_manager.get_server_config(server_name)
            if not cfg:
                raise ValueError(
                    f"MCP server '{server_name}' not defined in {self.config_manager.servers_file}"
                )
            cfg = self.config_manager.materialize_server(cfg)

            if cfg.transport in ("sse", "http") or (cfg.url and not cfg.command):
                session: BaseMCPSession = HttpSseMCPSession(server_name, cfg)
            else:
                session = StdioMCPSession(server_name, cfg)

            self._sessions[server_name] = session
            return session

    def list_tools(self, server_name: str, refresh: bool = False) -> List[Dict[str, Any]]:
        """Synchronously query tools/list from server on the daemon event loop."""
        if not refresh and server_name in self._tool_cache:
            return self._tool_cache[server_name]

        self._ensure_running()
        if not self._loop:
            return []

        session = self._get_or_create_session(server_name)

        async def _do_list() -> List[Dict[str, Any]]:
            return await session.list_tools()

        try:
            future = asyncio.run_coroutine_threadsafe(_do_list(), self._loop)
            raw_tools = future.result(timeout=15.0)
            self._tool_cache[server_name] = raw_tools

            # Cache annotations per tool
            annot_map: Dict[str, Dict[str, Any]] = {}
            for t in raw_tools:
                t_name = str(t.get("name", ""))
                ann = t.get("annotations") or {}
                if isinstance(ann, dict):
                    annot_map[t_name] = ann
            self._annotation_cache[server_name] = annot_map

            return raw_tools
        except Exception as e:
            logger.warning("Error fetching tool list from MCP server '%s': %s", server_name, e)
            return []

    def get_scoped_tool_schemas(
        self, server_name: str
    ) -> Tuple[List[Dict[str, Any]], Optional[str], bool]:
        """Convert MCP tools to OpenAI function calling format.

        Returns:
            (tool_schemas, compact_catalog_text_if_over_threshold, has_many_tools_flag)
        """
        raw_tools = self.list_tools(server_name)
        if not raw_tools:
            return [], None, False

        count = len(raw_tools)
        if count > MAX_DIRECT_TOOLS_THRESHOLD:
            # Over 20 tools: expose catalog in prompt and provide call_mcp_tool
            catalog_lines = [f"### Available Tools for MCP Server '{server_name}':"]
            for t in raw_tools:
                t_name = t.get("name", "")
                t_desc = t.get("description", "").strip() or "No description"
                catalog_lines.append(f"- `mcp__{server_name}__{t_name}`: {t_desc}")
            catalog_text = "\n".join(catalog_lines)
            return [], catalog_text, True

        # <= 20 tools: map each tool to mcp__<server>__<tool> schema
        schemas: List[Dict[str, Any]] = []
        for t in raw_tools:
            t_name = t.get("name", "")
            t_desc = t.get("description", "").strip() or f"MCP tool {t_name} on {server_name}"
            input_schema = t.get("inputSchema") or {"type": "object", "properties": {}}

            schema = {
                "type": "function",
                "function": {
                    "name": f"mcp__{server_name}__{t_name}",
                    "description": f"[{server_name}] {t_desc}",
                    "parameters": input_schema,
                },
            }
            schemas.append(schema)

        return schemas, None, False

    def get_tool_annotation(self, server_name: str, tool_name: str) -> Dict[str, Any]:
        """Fetch cached safety annotations for a tool (readOnlyHint, destructiveHint)."""
        server_annots = self._annotation_cache.get(server_name, {})
        if tool_name in server_annots:
            return server_annots[tool_name]

        # If not cached yet, attempt lazy listing
        self.list_tools(server_name)
        return self._annotation_cache.get(server_name, {}).get(tool_name, {})

    def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, Any],
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """Synchronously execute tools/call on server through the daemon event loop."""
        self._ensure_running()
        if not self._loop:
            return {"isError": True, "error": "MCP event loop is not active"}

        session = self._get_or_create_session(server_name)

        async def _do_call() -> Dict[str, Any]:
            return await session.call_tool(tool_name, arguments)

        try:
            future = asyncio.run_coroutine_threadsafe(_do_call(), self._loop)
            result = future.result(timeout=timeout)
            return result
        except Exception as e:
            logger.error(
                "Failed invoking MCP tool '%s' on server '%s': %s", tool_name, server_name, e
            )
            return {"isError": True, "error": str(e)}

    def close_server(self, server_name: str) -> None:
        """Close an individual server session."""
        with self._lock:
            session = self._sessions.pop(server_name, None)
            self._tool_cache.pop(server_name, None)
            self._annotation_cache.pop(server_name, None)

        if session and self._loop and self._loop.is_running():
            async def _do_close() -> None:
                await session.close()

            try:
                future = asyncio.run_coroutine_threadsafe(_do_close(), self._loop)
                future.result(timeout=5.0)
            except Exception as e:
                logger.debug("Error closing session '%s': %s", server_name, e)

    def close_all(self) -> None:
        """Close all active server sessions."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._tool_cache.clear()
            self._annotation_cache.clear()

        if sessions and self._loop and self._loop.is_running():
            async def _close_many() -> None:
                await asyncio.gather(*(s.close() for s in sessions), return_exceptions=True)

            try:
                future = asyncio.run_coroutine_threadsafe(_close_many(), self._loop)
                future.result(timeout=5.0)
            except Exception as e:
                logger.debug("Error closing MCP sessions: %s", e)

    def stop(self) -> None:
        """Stop all sessions and shutdown the background event loop."""
        self.close_all()
        with self._lock:
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=3.0)
            self._loop = None
            self._thread = None
