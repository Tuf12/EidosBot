"""Proxy OpenAI-compatible and Ollama metadata routes through the mobile gateway.

Lets companion apps (e.g. Stockzilla) use the EidosBot gateway on :8420 instead of
exposing Ollama on the tailnet directly. Upstream is always the agent's configured
local Ollama base URL (typically http://localhost:11434).
"""

import logging
from typing import Awaitable, Callable, Optional

import aiohttp
from aiohttp import web

logger = logging.getLogger(__name__)

AuthCheck = Callable[[web.Request], bool]


async def _forward(
    request: web.Request,
    upstream_base: str,
    upstream_path: str,
    *,
    auth_check: Optional[AuthCheck] = None,
    timeout_seconds: float = 300.0,
) -> web.StreamResponse:
    if auth_check is not None and not auth_check(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    upstream = upstream_base.rstrip("/") + upstream_path
    headers = {}
    content_type = request.headers.get("Content-Type")
    if content_type:
        headers["Content-Type"] = content_type
    body = await request.read()

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                request.method,
                upstream,
                data=body if body else None,
                headers=headers,
            ) as upstream_resp:
                resp = web.StreamResponse(
                    status=upstream_resp.status,
                    reason=upstream_resp.reason,
                )
                for key, value in upstream_resp.headers.items():
                    if key.lower() in ("content-length", "transfer-encoding", "connection"):
                        continue
                    resp.headers[key] = value
                await resp.prepare(request)
                async for chunk in upstream_resp.content.iter_chunked(65536):
                    await resp.write(chunk)
                return resp
    except aiohttp.ClientConnectorError:
        return web.json_response(
            {
                "error": "ollama_unreachable",
                "message": "Could not reach local Ollama. Start Ollama on this machine.",
                "upstream": upstream_base,
            },
            status=503,
        )
    except Exception as e:
        logger.error("LLM proxy error %s -> %s: %s", request.path, upstream, e)
        return web.json_response({"error": str(e)}, status=502)


async def handle_llm_health(request: web.Request, upstream_base: str) -> web.Response:
    """Public on the tailnet: gateway is up; reports whether upstream Ollama answers."""
    reachable = False
    try:
        timeout = aiohttp.ClientTimeout(total=3.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(upstream_base.rstrip("/") + "/api/tags") as resp:
                reachable = resp.status == 200
    except Exception:
        reachable = False
    return web.json_response(
        {
            "ok": True,
            "service": "eidosbot-gateway",
            "ollama_upstream": upstream_base.rstrip("/"),
            "ollama_reachable": reachable,
            "openai_chat_path": "/v1/chat/completions",
        }
    )


async def handle_proxy_tags(
    request: web.Request,
    upstream_base: str,
    auth_check: AuthCheck,
) -> web.StreamResponse:
    return await _forward(request, upstream_base, "/api/tags", auth_check=auth_check, timeout_seconds=30.0)


async def handle_proxy_openai_chat(
    request: web.Request,
    upstream_base: str,
    auth_check: AuthCheck,
) -> web.StreamResponse:
    if request.method != "POST":
        return web.json_response({"error": "Method not allowed"}, status=405)
    return await _forward(
        request,
        upstream_base,
        "/v1/chat/completions",
        auth_check=auth_check,
        timeout_seconds=600.0,
    )
