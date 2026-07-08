"""MCP (Model Context Protocol) client — mounts external tool servers.

An agent's manifest may declare ``mcp`` mounts (see ``agents.py`` — the field is
reserved as ``[]`` by default).  Each mount points at an MCP *server* that exposes
its own set of tools.  This module talks JSON-RPC 2.0 to those servers so their
tools can be surfaced to the model side-by-side with the built-in repo tools
(``tools.py``).

Two transports are supported, both hand-rolled (no ``mcp`` SDK dependency):

    http   → JSON-RPC over ``httpx`` (POST); tolerates both plain-JSON and
             ``text/event-stream`` (SSE) responses, which real MCP HTTP servers use.
    stdio  → JSON-RPC over a ``subprocess``'s stdin/stdout (one JSON object per line).

Tool names are namespaced ``mcp__<server>__<tool>`` so they never collide with the
built-in tools and so ``execute()`` can reverse-map a call back to its server.

Everything here is defensive: an unreachable / slow / broken server is *skipped*
during ``tool_specs()`` and yields an error string (never an exception) during
``execute()``.  A single bad mount must not take down an agent's turn.

Credentials: when a mount carries ``ref`` (``"connection:<id>"``) we resolve it via
``connections.resolve(ref)`` → ``{"env": {...}, "headers": {...}}`` and inject the
headers (http) or env (stdio).  ``connections`` is owned by another module and may
not exist yet, so the import is best-effort.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from typing import Any

import httpx

# ── protocol constants ──────────────────────────────────────────────────────
_PREFIX = "mcp__"
_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "warroom", "version": "1"}
_HTTP_TIMEOUT = 20        # seconds per http JSON-RPC round-trip
_STDIO_TIMEOUT = 20       # seconds to wait for a stdio response
_LIST_CACHE_TTL = 300     # tools/list cache lifetime (seconds)

# tools/list cache: server-key -> (expires_at_epoch, tools_list).  Guarded by a
# lock because tool_specs()/execute() can run from different async tasks.
_list_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_cache_lock = threading.Lock()


# ── credential resolution (best-effort; connections.py is owned elsewhere) ──
def _resolve_ref(ref: str | None) -> dict[str, Any]:
    """Resolve a ``connection:<id>`` ref to ``{"env":{...},"headers":{...}}``.

    Never raises: if ``connections`` is missing or the ref is unknown we return
    empty maps so the mount is simply used without injected credentials."""
    if not ref:
        return {"env": {}, "headers": {}}
    try:
        from . import connections  # imported lazily — module may not exist yet
    except Exception:
        return {"env": {}, "headers": {}}
    try:
        resolved = connections.resolve(ref)
    except Exception as exc:  # pragma: no cover — defensive
        print(f"[mcp] connections.resolve({ref!r}) failed: {exc}")
        return {"env": {}, "headers": {}}
    if not isinstance(resolved, dict):
        return {"env": {}, "headers": {}}
    return {
        "env": dict(resolved.get("env") or {}),
        "headers": dict(resolved.get("headers") or {}),
    }


# ── namespacing helpers ─────────────────────────────────────────────────────
def is_mcp_tool(name: str) -> bool:
    """True if ``name`` is a namespaced MCP tool (``mcp__server__tool``)."""
    return isinstance(name, str) and name.startswith(_PREFIX)


def _qualify(server: str, tool: str) -> str:
    return f"{_PREFIX}{server}__{tool}"


def _split(name: str) -> tuple[str, str] | None:
    """Reverse ``mcp__<server>__<tool>`` → (server, tool). ``None`` if malformed.

    The server segment cannot contain ``__`` (we control it via the mount), so we
    split on the *first* ``__`` after the prefix to recover the server, and keep the
    remainder (which may itself contain ``__``) as the tool name."""
    if not is_mcp_tool(name):
        return None
    rest = name[len(_PREFIX):]
    server, sep, tool = rest.partition("__")
    if not sep or not server or not tool:
        return None
    return server, tool


def _mount_key(mount: dict[str, Any]) -> str:
    """Stable cache key for a mount (server + endpoint identity)."""
    return "|".join([
        str(mount.get("server", "")),
        str(mount.get("transport", "")),
        str(mount.get("url", "")),
        str(mount.get("command", "")),
    ])


# ── public: aggregate tool specs across all mounts ──────────────────────────
def tool_specs(mounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic-format tool specs for every reachable mounted server's tools.

    Each server is queried (``tools/list``, cached ~5 min).  A server that is
    unreachable / times out / errors is *skipped* (a warning is printed) rather
    than aborting the whole set — one broken mount must not blind the agent to the
    rest.  Tool names are namespaced ``mcp__<server>__<tool>``.
    """
    specs: list[dict[str, Any]] = []
    for mount in mounts or []:
        server = str(mount.get("server") or "").strip()
        if not server:
            continue
        try:
            tools = _list_tools(mount)
        except Exception as exc:  # defensive: never propagate
            print(f"[mcp] skip server {server!r}: {type(exc).__name__}: {exc}")
            continue
        allowed = _tool_filter(mount)
        for tool in tools:
            tname = tool.get("name")
            if not tname:
                continue
            if allowed is not None and tname not in allowed:
                continue
            schema = tool.get("inputSchema") or tool.get("input_schema") or {}
            if not isinstance(schema, dict) or schema.get("type") != "object":
                schema = {"type": "object", "properties": {}}
            specs.append({
                "name": _qualify(server, tname),
                "description": (tool.get("description") or "")[:1000],
                "input_schema": schema,
            })
    return specs


def _tool_filter(mount: dict[str, Any]) -> set[str] | None:
    """A mount's ``tools`` list is an allow-list; ``["*"]`` / empty = all tools."""
    raw = mount.get("tools")
    if not raw or "*" in raw:
        return None
    return {str(t) for t in raw}


# ── public: dispatch one namespaced tool call ───────────────────────────────
async def execute(name: str, args: dict[str, Any], mounts: list[dict[str, Any]]) -> str:
    """Invoke ``mcp__server__tool`` on its owning mount; return a result string.

    Reverse-maps ``name`` to a server, finds the matching mount, calls
    ``tools/call``, and flattens the MCP content result to text.  Any failure —
    unknown tool, no such mount, transport error, server-side ``isError`` — comes
    back as a human-readable error string.  Never raises."""
    parts = _split(name)
    if parts is None:
        return f"（非法 MCP 工具名：{name}）"
    server, tool = parts
    mount = _find_mount(server, mounts)
    if mount is None:
        return f"（未找到 MCP server：{server}）"
    try:
        result = _call_tool(mount, tool, args or {})
    except Exception as exc:  # defensive: turn every failure into a string
        return f"（MCP 工具 {name} 调用失败：{type(exc).__name__}: {exc}）"
    return _flatten_result(result, name)


def _find_mount(server: str, mounts: list[dict[str, Any]]) -> dict[str, Any] | None:
    for mount in mounts or []:
        if str(mount.get("server") or "") == server:
            return mount
    return None


def _flatten_result(result: Any, name: str) -> str:
    """Turn an MCP ``tools/call`` result into a plain string for the model."""
    if not isinstance(result, dict):
        return str(result)
    is_error = bool(result.get("isError"))
    content = result.get("content")
    chunks: list[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                chunks.append(str(block))
                continue
            btype = block.get("type")
            if btype == "text":
                chunks.append(str(block.get("text", "")))
            elif btype in ("resource", "resource_link"):
                res = block.get("resource") or block
                chunks.append(str(res.get("text") or res.get("uri") or res))
            else:
                chunks.append(json.dumps(block, ensure_ascii=False))
    elif content is not None:
        chunks.append(str(content))
    text = "\n".join(c for c in chunks if c) or "（无输出）"
    if is_error:
        return f"（MCP 工具 {name} 返回错误）\n{text}"
    return text


# ── per-mount tools/list (with cache) and tools/call ────────────────────────
def _list_tools(mount: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch (and cache ~5 min) the raw tool descriptors for one mount."""
    key = _mount_key(mount)
    now = time.time()
    with _cache_lock:
        hit = _list_cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    tools = _rpc_list_tools(mount)
    with _cache_lock:
        _list_cache[key] = (now + _LIST_CACHE_TTL, tools)
    return tools


def _call_tool(mount: dict[str, Any], tool: str, args: dict[str, Any]) -> Any:
    """Run ``initialize`` (if needed) then ``tools/call`` for one mount."""
    transport = str(mount.get("transport") or "http").lower()
    if transport == "stdio":
        return _stdio_session(mount, [("tools/call", {"name": tool, "arguments": args})])[0]
    return _http_call(mount, "tools/call", {"name": tool, "arguments": args})


def _rpc_list_tools(mount: dict[str, Any]) -> list[dict[str, Any]]:
    transport = str(mount.get("transport") or "http").lower()
    if transport == "stdio":
        result = _stdio_session(mount, [("tools/list", {})])[0]
    else:
        result = _http_call(mount, "tools/list", {})
    if isinstance(result, dict):
        tools = result.get("tools")
        if isinstance(tools, list):
            return tools
    return []


# ── HTTP transport (JSON-RPC 2.0, SSE- and JSON-tolerant) ───────────────────
def _http_call(mount: dict[str, Any], method: str, params: dict[str, Any]) -> Any:
    """One stateless http JSON-RPC round-trip, preceded by ``initialize``.

    Streamable-HTTP MCP servers are stateless per POST from our side: we send
    ``initialize`` first (so the server is happy) and then the real method.  Some
    servers keep session state via the ``Mcp-Session-Id`` response header, which we
    echo back on the follow-up request."""
    url = str(mount.get("url") or "").strip()
    if not url:
        raise ValueError("http mount 缺少 url")
    creds = _resolve_ref(mount.get("ref"))
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    headers.update(creds["headers"])

    with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        # initialize handshake — capture any session id the server hands back.
        init_resp = _http_post(client, url, headers, "initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": _CLIENT_INFO,
        })
        session_id = init_resp.headers.get("mcp-session-id")
        call_headers = dict(headers)
        if session_id:
            call_headers["Mcp-Session-Id"] = session_id
        resp = _http_post(client, url, call_headers, method, params)
        return _parse_rpc_response(resp, method)


def _http_post(client: httpx.Client, url: str, headers: dict[str, str],
               method: str, params: dict[str, Any]) -> httpx.Response:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    resp = client.post(url, headers=headers, json=payload)
    resp.raise_for_status()
    return resp


def _parse_rpc_response(resp: httpx.Response, method: str) -> Any:
    """Extract the JSON-RPC ``result`` from a plain-JSON or SSE http response."""
    ctype = resp.headers.get("content-type", "")
    body = resp.text or ""
    if "text/event-stream" in ctype or body.lstrip().startswith("event:") \
            or body.lstrip().startswith("data:"):
        message = _parse_sse(body)
    else:
        try:
            message = json.loads(body) if body else {}
        except json.JSONDecodeError:
            # last-ditch: maybe it's SSE without the right content-type
            message = _parse_sse(body)
    return _unwrap_rpc(message, method)


def _parse_sse(body: str) -> dict[str, Any]:
    """Pull the last JSON ``data:`` payload out of an SSE stream body."""
    last: dict[str, Any] = {}
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if not data or data == "[DONE]":
            continue
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            last = parsed
    return last


def _unwrap_rpc(message: Any, method: str) -> Any:
    if not isinstance(message, dict):
        raise ValueError(f"{method} 返回非 JSON-RPC 响应")
    if "error" in message and message["error"]:
        err = message["error"]
        detail = err.get("message", err) if isinstance(err, dict) else err
        raise RuntimeError(f"{method} JSON-RPC error: {detail}")
    return message.get("result", {})


# ── stdio transport (JSON-RPC 2.0 over a subprocess) ────────────────────────
def _stdio_session(mount: dict[str, Any], calls: list[tuple[str, dict[str, Any]]]) -> list[Any]:
    """Spawn the mount's command, do the ``initialize`` handshake, then run each
    (method, params) call in order, returning each ``result``.  The process is
    torn down when done — one short-lived session per invocation."""
    command = str(mount.get("command") or "").strip()
    if not command:
        raise ValueError("stdio mount 缺少 command")
    creds = _resolve_ref(mount.get("ref"))
    env = dict(os.environ)
    env.update(creds["env"])

    proc = subprocess.Popen(
        command, shell=True, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    try:
        # initialize + notifications/initialized handshake
        _stdio_rpc(proc, 1, "initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": _CLIENT_INFO,
        })
        _stdio_notify(proc, "notifications/initialized")
        results: list[Any] = []
        for i, (method, params) in enumerate(calls, start=2):
            results.append(_stdio_rpc(proc, i, method, params))
        return results
    finally:
        _kill(proc)


def _stdio_rpc(proc: "subprocess.Popen[str]", rpc_id: int, method: str,
               params: dict[str, Any]) -> Any:
    """Send one request and block (bounded) for its matching-id response."""
    if proc.stdin is None or proc.stdout is None:
        raise RuntimeError("stdio 子进程管道不可用")
    request = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "method": method,
                          "params": params}, ensure_ascii=False)
    proc.stdin.write(request + "\n")
    proc.stdin.flush()

    deadline = time.time() + _STDIO_TIMEOUT
    while time.time() < deadline:
        line = _read_line(proc, deadline)
        if line is None:
            break
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue  # ignore server log noise on stdout
        if not isinstance(message, dict):
            continue
        # skip notifications / responses that aren't ours
        if message.get("id") != rpc_id:
            continue
        return _unwrap_rpc(message, method)
    raise TimeoutError(f"stdio {method} 超时（{_STDIO_TIMEOUT}s）")


def _stdio_notify(proc: "subprocess.Popen[str]", method: str) -> None:
    """Fire-and-forget JSON-RPC notification (no id, no response expected)."""
    if proc.stdin is None:
        return
    note = json.dumps({"jsonrpc": "2.0", "method": method}, ensure_ascii=False)
    try:
        proc.stdin.write(note + "\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError):
        pass


def _read_line(proc: "subprocess.Popen[str]", deadline: float) -> str | None:
    """Read one stdout line with a bounded wait via a helper thread.

    ``readline()`` blocks, so we run it off-thread and join with a timeout — this
    keeps the whole session honoring ``_STDIO_TIMEOUT`` even if the child goes
    silent or dies mid-stream."""
    if proc.stdout is None:
        return None
    box: list[str] = []

    def _pull() -> None:
        try:
            box.append(proc.stdout.readline())  # type: ignore[union-attr]
        except Exception:
            box.append("")

    t = threading.Thread(target=_pull, daemon=True)
    t.start()
    t.join(max(0.05, deadline - time.time()))
    if t.is_alive():
        return None  # timed out waiting for this line
    line = box[0] if box else ""
    if line == "":  # EOF — process closed stdout
        return None
    return line


def _kill(proc: "subprocess.Popen[str]") -> None:
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if stream is not None:
                stream.close()
        except Exception:
            pass
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
