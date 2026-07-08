"""Normalized event bus.

Every executor (mock, Claude Code, OpenCode, generic) is funnelled into one
AgentEvent schema so the frontend renders identically regardless of backend
(PRD FR-12.4, arch 3.3):

    {type: tool_call|message|progress|escalation|result|state|gate|system,
     ticket, agent, ts, payload}

Subscribers are asyncio.Queues (one per WebSocket client). The bus is async and
fan-outs to all live clients; slow/dead clients are dropped, never block others.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Coroutine

_subscribers: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def spawn(coro: Coroutine) -> None:
    """Schedule a coroutine on the main event loop from any thread.

    Sync FastAPI endpoints run in a threadpool with no running loop, so engine
    workflows started from a request must be marshalled back onto the main loop.
    """
    if _loop and _loop.is_running():
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is _loop:
            _loop.create_task(coro)
        else:
            asyncio.run_coroutine_threadsafe(coro, _loop)
    else:
        # no loop yet (e.g. tests) — run to completion synchronously
        asyncio.get_event_loop().run_until_complete(coro)


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def _fanout(event: dict[str, Any]) -> None:
    dead = []
    for q in _subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _subscribers.discard(q)


def emit(type: str, *, ticket: str | None = None, agent: str | None = None,
         payload: Any = None) -> None:
    """Emit a normalized AgentEvent. Safe to call from any thread."""
    event = {
        "type": type,
        "ticket": ticket,
        "agent": agent,
        "ts": time.time(),
        "payload": payload or {},
    }
    if _loop and _loop.is_running():
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is _loop:
            _fanout(event)
        else:
            _loop.call_soon_threadsafe(_fanout, event)
    else:
        _fanout(event)


def emit_delta(ticket: str, agent: str, message_id: str, text: str) -> None:
    """Token delta for a streaming agent reply (relayed to the browser as SSE)."""
    emit("delta", ticket=ticket, agent=agent,
         payload={"message_id": message_id, "text": text})
