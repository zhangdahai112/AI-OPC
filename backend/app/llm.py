"""Multi-provider LLM backend.

Provides a unified ``stream_reply()`` that dispatches to the configured provider
(Anthropic SDK or OpenAI-compatible).  Providers are defined in the platform config
(persisted in DB kv) while API keys come from environment variables so they never
touch the database.

Provider config shape (stored in ``config["llm"]``)::

    {
      "active_provider": "anthropic-1",
      "providers": [
        {
          "id": "anthropic-1",
          "name": "Anthropic Claude",
          "type": "anthropic",
          "api_key_env": "ANTHROPIC_API_KEY",
          "base_url": "",
          "model": "claude-sonnet-4-6",
          "enabled": true,
          "max_tokens": 4096,
          "effort": "medium",
        },
        {
          "id": "openai-1",
          "name": "OpenAI 兼容",
          "type": "openai",
          "api_key_env": "OPENAI_API_KEY",
          "base_url": "https://api.openai.com/v1",
          "model": "gpt-4o",
          "enabled": false,
          "max_tokens": 4096,
        },
      ]
    }
"""
from __future__ import annotations

import json
import os
from typing import Any, Awaitable, Callable

import httpx

from .config import LLM_MAX_TOKENS_CEILING, LLM_MAX_TOOL_ITERS
from .config import has_api_key as _has_key_compat


def _clamp_tokens(max_tokens: int | None, provider: dict[str, Any]) -> int:
    """Effective per-call output-token limit, hard-capped so a misconfigured
    provider (e.g. max_tokens=111111) can't let one turn stream for minutes."""
    want = max_tokens or provider.get("max_tokens") or 4096
    try:
        want = int(want)
    except (TypeError, ValueError):
        want = 4096
    return max(256, min(want, LLM_MAX_TOKENS_CEILING))

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

OnDelta = Callable[[str], Awaitable[None]]
# on_tool(name, input, tool_use_id) -> awaitable[str result]
OnTool = Callable[[str, dict, str], Awaitable[str]]

# cap the tool-use loop so an agent can't spin forever (configurable, see config)
MAX_TOOL_ITERS = LLM_MAX_TOOL_ITERS


def get_providers_config() -> dict[str, Any]:
    """Read the ``llm`` section of the platform config."""
    from . import db
    cfg = db.kv_get("config", {})
    return cfg.get("llm", {})


def get_active_provider() -> dict[str, Any] | None:
    """Return the currently-enabled provider, or ``None``."""
    llm_cfg = get_providers_config()
    providers = llm_cfg.get("providers", [])
    active_id = llm_cfg.get("active_provider", "")

    # exact match first
    for p in providers:
        if p.get("id") == active_id and p.get("enabled", False):
            return p
    # fallback: first enabled provider
    for p in providers:
        if p.get("enabled", False):
            return p
    return providers[0] if providers else None


def get_provider(provider_id: str) -> dict[str, Any] | None:
    """Look up a specific provider by id."""
    for p in get_providers_config().get("providers", []):
        if p.get("id") == provider_id:
            return p
    return None


def available() -> bool:
    """At least one *enabled* provider has a usable API key."""
    for p in get_providers_config().get("providers", []):
        if not p.get("enabled", False):
            continue
        if _resolve_api_key(p):
            return True
    # backward compat for legacy env-var-only setups
    return _has_key_compat()


def resolve_api_key(provider: dict[str, Any]) -> str | None:
    """Resolve the API key for a provider (env var -> inline fallback)."""
    return _resolve_api_key(provider)


def provider_status(provider: dict[str, Any]) -> dict[str, Any]:
    """Return a safe (key-free) status dict for the frontend."""
    return {
        "id": provider.get("id"),
        "name": provider.get("name"),
        "type": provider.get("type"),
        "model": provider.get("model"),
        "base_url": provider.get("base_url", ""),
        "api_key_env": provider.get("api_key_env", ""),
        "enabled": provider.get("enabled", False),
        "max_tokens": provider.get("max_tokens", 4096),
        "effort": provider.get("effort"),
        "key_configured": _resolve_api_key(provider) is not None,
    }


def provider_status_all() -> list[dict[str, Any]]:
    """Status for every configured provider (key-free, suitable for the API)."""
    llm_cfg = get_providers_config()
    active_id = llm_cfg.get("active_provider", "")
    out = []
    for p in llm_cfg.get("providers", []):
        s = provider_status(p)
        s["active"] = p.get("id") == active_id
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# Unified stream entry-point
# ---------------------------------------------------------------------------


async def stream_reply(
    system: str,
    messages: list[dict[str, Any]],
    on_delta: OnDelta,
    *,
    provider_id: str | None = None,
    effort: str | None = None,
    max_tokens: int | None = None,
    tools: list[dict[str, Any]] | None = None,
    on_tool: OnTool | None = None,
    max_iters: int | None = None,
) -> dict[str, Any]:
    """Stream one assistant turn through the configured (or specified) provider.

    Returns ``{"text": …, "usage": {…}, "stop_reason": …}``.
    When no usable provider is available a placeholder message is streamed so the
    UI keeps working in demo mode.

    When ``tools`` and ``on_tool`` are supplied, this runs a full tool-use loop
    (supported for both Anthropic and OpenAI-compatible providers): the model may
    call tools, each call is dispatched through ``on_tool``, and the result is fed
    back until the model stops calling tools.
    """
    provider = _resolve_provider(provider_id)
    if not provider:
        msg = (
            "（未配置 LLM 供应商 —— 请在「配置 → LLM 供应商」中"
            "添加并启用一个供应商，再填入 API Key。）"
        )
        await on_delta(msg)
        return {"text": msg, "usage": {}, "stop_reason": "no_provider"}

    ptype = provider.get("type", "anthropic")
    try:
        if ptype == "anthropic":
            return await _anthropic_stream(system, messages, on_delta, provider,
                                           effort, max_tokens, tools, on_tool, max_iters)
        elif ptype == "openai":
            return await _openai_stream(system, messages, on_delta, provider,
                                        effort, max_tokens, tools, on_tool, max_iters)
        else:
            msg = f"（不支持的供应商类型：{ptype}）"
            await on_delta(msg)
            return {"text": msg, "usage": {}, "stop_reason": "unsupported"}
    except Exception as exc:
        err = f"（{provider.get('name', '?')} 调用失败：{type(exc).__name__}: {exc}）"
        await on_delta("\n" + err)
        return {"text": err, "usage": {}, "stop_reason": "error"}


def _resolve_provider(provider_id: str | None) -> dict[str, Any] | None:
    if provider_id:
        return get_provider(provider_id)
    return get_active_provider()


def _resolve_api_key(provider: dict[str, Any]) -> str | None:
    """Resolve API key: ① inline from config DB ② env var fallback."""
    key = provider.get("api_key", "") or ""
    if key:
        return key
    # For seed/default providers, check the legacy env-var name as fallback.
    env_var = provider.get("api_key_env", "") or ""
    if env_var:
        return os.environ.get(env_var)
    return None


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------


def _content_to_dicts(content: list[Any]) -> list[dict[str, Any]]:
    """Serialize SDK content blocks back into request-shaped dicts so an
    assistant turn (incl. tool_use blocks) can be replayed in the next call."""
    out: list[dict[str, Any]] = []
    for b in content:
        btype = getattr(b, "type", None)
        if btype == "text":
            out.append({"type": "text", "text": b.text})
        elif btype == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name,
                        "input": b.input})
        # thinking / other block types are dropped from the replay transcript
    return out


async def _anthropic_stream(
    system: str,
    messages: list[dict[str, Any]],
    on_delta: OnDelta,
    provider: dict[str, Any],
    effort: str | None,
    max_tokens: int | None,
    tools: list[dict[str, Any]] | None = None,
    on_tool: OnTool | None = None,
    max_iters: int | None = None,
) -> dict[str, Any]:
    import anthropic

    key = _resolve_api_key(provider)
    if not key:
        msg = f"（{provider.get('name')} API Key 未配置）"
        await on_delta(msg)
        return {"text": msg, "usage": {}, "stop_reason": "no_key"}

    client = anthropic.AsyncAnthropic(api_key=key,
                                      base_url=provider.get("base_url") or None)
    model = provider.get("model", "claude-sonnet-4-6")
    max_tok = _clamp_tokens(max_tokens, provider)

    use_tools = bool(tools and on_tool)
    iters = max_iters or MAX_TOOL_ITERS
    convo: list[dict[str, Any]] = list(messages)
    text_parts: list[str] = []
    in_tok = out_tok = 0
    final = None
    concluded = not use_tools

    # ── prompt caching (KV cost optimization) ──────────────────────────────
    # Cache the stable prefix — the tool schema and the system prompt — so a
    # multi-turn conversation (and the tool-use loop within one turn, which
    # replays the same system+tools every iteration) reads it at ~0.1x instead
    # of paying full input price each time. Only worth a breakpoint when the
    # block clears Anthropic's ~1024-token minimum, so we gate on length.
    sys_param: Any = system
    if isinstance(system, str) and len(system) >= 4000:
        sys_param = [{"type": "text", "text": system,
                      "cache_control": {"type": "ephemeral"}}]
    cached_tools = tools
    if use_tools and tools:
        cached_tools = [dict(t) for t in tools]
        cached_tools[-1] = {**cached_tools[-1],
                            "cache_control": {"type": "ephemeral"}}

    async def _run(kwargs: dict[str, Any]):
        nonlocal final, in_tok, out_tok
        async with client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                text_parts.append(text)
                await on_delta(text)
            final = await stream.get_final_message()
        in_tok += getattr(final.usage, "input_tokens", 0) or 0
        out_tok += getattr(final.usage, "output_tokens", 0) or 0

    try:
        for _ in range(iters if use_tools else 1):
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tok,
                "system": sys_param,
                "messages": convo,
            }
            if use_tools:
                kwargs["tools"] = cached_tools
            await _run(kwargs)

            if not use_tools or final.stop_reason != "tool_use":
                concluded = True
                break

            tool_uses = [b for b in final.content
                         if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                concluded = True
                break
            # replay the assistant turn, then answer every tool call
            convo.append({"role": "assistant",
                          "content": _content_to_dicts(final.content)})
            results: list[dict[str, Any]] = []
            for b in tool_uses:
                result = await on_tool(b.name, dict(b.input or {}), b.id)
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": result})
            convo.append({"role": "user", "content": results})

        # hit the tool-call budget without concluding → force a final tool-free
        # answer so the turn returns a real synthesis, not an empty string.
        if use_tools and not concluded:
            convo.append({"role": "user", "content":
                          "（已达工具调用上限）请立即基于你已查到的信息给出最终结论，不要再调用工具。"})
            await _run({"model": model, "max_tokens": max_tok,
                        "system": sys_param, "messages": convo})

        usage = {"input_tokens": in_tok, "output_tokens": out_tok}
        return {"text": "".join(text_parts), "usage": usage,
                "stop_reason": final.stop_reason if final else "end"}
    except Exception as e:
        err = f"（调用 {model} 失败：{type(e).__name__}: {e}）"
        await on_delta("\n" + err)
        return {"text": "".join(text_parts) + "\n" + err, "usage": {},
                "stop_reason": "error"}


# ---------------------------------------------------------------------------
# OpenAI-compatible provider (OpenAI, DeepSeek, Ollama, …)
# ---------------------------------------------------------------------------


def _trim_tool_history(convo: list[dict[str, Any]], keep_last: int = 4,
                       cap: int = 900) -> None:
    """Cap the cost of the OpenAI tool-loop. Each iteration re-sends the whole
    transcript; a single ``repo_map`` result is ~35KB, so without trimming a
    12-round loop balloons to hundreds of thousands of input tokens. Truncate the
    *content* (never drop the message — OpenAI requires every tool_call to have a
    matching tool result) of all but the most recent ``keep_last`` tool outputs."""
    tool_idxs = [i for i, m in enumerate(convo) if m.get("role") == "tool"]
    for i in tool_idxs[:-keep_last] if keep_last else tool_idxs:
        c = convo[i].get("content") or ""
        if len(c) > cap:
            convo[i] = {**convo[i],
                        "content": c[:cap] + "\n…（早期工具输出已截断以节省上下文）"}


def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Anthropic-shaped tool specs ({name, description, input_schema})
    into OpenAI function-calling specs ({type:function, function:{...}})."""
    out: list[dict[str, Any]] = []
    for t in tools:
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            },
        })
    return out


async def _openai_stream(
    system: str,
    messages: list[dict[str, Any]],
    on_delta: OnDelta,
    provider: dict[str, Any],
    effort: str | None,
    max_tokens: int | None,
    tools: list[dict[str, Any]] | None = None,
    on_tool: OnTool | None = None,
    max_iters: int | None = None,
) -> dict[str, Any]:
    key = _resolve_api_key(provider)
    if not key:
        msg = f"（{provider.get('name')} API Key 未配置）"
        await on_delta(msg)
        return {"text": msg, "usage": {}, "stop_reason": "no_key"}

    model = provider.get("model", "gpt-4o")
    base_url = provider.get("base_url", "https://api.openai.com/v1").rstrip("/")
    max_tok = _clamp_tokens(max_tokens, provider)
    iters = max_iters or MAX_TOOL_ITERS

    use_tools = bool(tools and on_tool)
    oa_tools = _to_openai_tools(tools) if use_tools else None
    # the running OpenAI-format transcript; assistant/tool turns get appended as
    # the tool-use loop progresses so the model sees each tool's real output.
    convo: list[dict[str, Any]] = [{"role": "system", "content": system}] + list(messages)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    text_parts: list[str] = []
    usage = {"in": 0, "out": 0}

    async def _once(client: "httpx.AsyncClient", include_tools: bool):
        """Stream one chat.completions call. Returns (finish_reason, tool frags)."""
        body: dict[str, Any] = {"model": model, "max_tokens": max_tok,
                                "stream": True, "messages": convo}
        if include_tools:
            body["tools"] = oa_tools
            body["tool_choice"] = "auto"
            body["stream_options"] = {"include_usage": True}
        iter_text: list[str] = []
        frags: dict[int, dict[str, str]] = {}
        finish: str | None = None
        async with client.stream("POST", f"{base_url}/chat/completions",
                                 json=body, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                u = chunk.get("usage")
                if u:
                    usage["in"] += u.get("prompt_tokens", 0) or 0
                    usage["out"] += u.get("completion_tokens", 0) or 0
                choices = chunk.get("choices", [])
                if not choices:
                    continue
                ch0 = choices[0]
                if ch0.get("finish_reason"):
                    finish = ch0["finish_reason"]
                delta = ch0.get("delta", {}) or {}
                content = delta.get("content")
                if content:
                    iter_text.append(content)
                    text_parts.append(content)
                    await on_delta(content)
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = frags.setdefault(idx, {"id": "", "name": "", "args": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]
        return finish, frags, "".join(iter_text)

    stop = "completed"
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            concluded = not use_tools
            for _ in range(iters if use_tools else 1):
                if use_tools:
                    _trim_tool_history(convo)
                finish, frags, iter_text = await _once(client, use_tools)
                if not use_tools or finish != "tool_calls" or not frags:
                    stop = finish or "completed"
                    concluded = True
                    break
                # Guard against malformed/leaked tool calls (e.g. DeepSeek spilling
                # its ｜｜DSML｜｜ function-call markup as text → empty tool name).
                # Executing those and looping is how the turn "hangs"; conclude instead.
                ordered = [(i, frags[i]) for i in sorted(frags)
                           if (frags[i].get("name") or "").strip()]
                if not ordered:
                    stop = "malformed_tool_call"
                    concluded = True
                    break
                tool_calls = [{
                    "id": slot["id"] or f"call_{i}",
                    "type": "function",
                    "function": {"name": slot["name"], "arguments": slot["args"] or "{}"},
                } for i, slot in ordered]
                convo.append({"role": "assistant",
                              "content": iter_text or None, "tool_calls": tool_calls})
                for (i, slot), call in zip(ordered, tool_calls):
                    try:
                        args = json.loads(slot["args"]) if slot["args"].strip() else {}
                    except json.JSONDecodeError:
                        args = {}
                    if not isinstance(args, dict):
                        args = {}
                    result = await on_tool(slot["name"], args, call["id"])
                    convo.append({"role": "tool", "tool_call_id": call["id"],
                                  "content": result})

            # hit the tool-call budget without concluding → force ONE final answer
            # with tools disabled, so the turn returns a real synthesis instead of
            # an empty string (which makes the caller wastefully redo the work).
            if use_tools and not concluded:
                _trim_tool_history(convo, keep_last=6)
                convo.append({"role": "user", "content":
                              "（已达工具调用上限）请立即基于你已经查到的信息给出最终结论，"
                              "不要再调用任何工具。"})
                await _once(client, False)
                stop = "concluded_after_cap"

        return {"text": "".join(text_parts),
                "usage": {"input_tokens": usage["in"], "output_tokens": usage["out"]},
                "stop_reason": stop}
    except Exception as e:
        err = f"（调用 {model} 失败：{type(e).__name__}: {e}）"
        await on_delta("\n" + err)
        return {"text": "".join(text_parts) + "\n" + err, "usage": {},
                "stop_reason": "error"}


# ---------------------------------------------------------------------------
# Test helper  (used by POST /api/llm/test)
# ---------------------------------------------------------------------------


async def test_provider(provider_id: str) -> dict[str, Any]:
    """Send a short ping to the provider and return connectivity info."""
    provider = get_provider(provider_id)
    if not provider:
        return {"ok": False, "error": "provider not found"}

    key = _resolve_api_key(provider)
    if not key:
        return {"ok": False, "error": f"API Key 未配置（env: {provider.get('api_key_env', '?')}）"}

    ptype = provider.get("type", "anthropic")
    try:
        if ptype == "anthropic":
            return await _test_anthropic(provider)
        elif ptype == "openai":
            return await _test_openai(provider)
        else:
            return {"ok": False, "error": f"unsupported type: {ptype}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def _test_anthropic(provider: dict[str, Any]) -> dict[str, Any]:
    import anthropic

    key = _resolve_api_key(provider)
    client = anthropic.AsyncAnthropic(api_key=key,
                                      base_url=provider.get("base_url") or None)
    msg = await client.messages.create(
        model=provider.get("model", "claude-sonnet-4-6"),
        max_tokens=16,
        messages=[{"role": "user", "content": "ping"}],
    )
    return {"ok": True, "model": msg.model, "latency": "ok"}


async def _test_openai(provider: dict[str, Any]) -> dict[str, Any]:
    key = _resolve_api_key(provider)
    base_url = provider.get("base_url", "https://api.openai.com/v1").rstrip("/")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            json={
                "model": provider.get("model", "gpt-4o"),
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "ping"}],
            },
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    return {"ok": True, "model": data.get("model", "?"), "latency": "ok"}
