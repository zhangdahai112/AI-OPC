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

from .config import has_api_key as _has_key_compat

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

OnDelta = Callable[[str], Awaitable[None]]
# on_tool(name, input, tool_use_id) -> awaitable[str result]
OnTool = Callable[[str, dict, str], Awaitable[str]]

MAX_TOOL_ITERS = 12  # cap the tool-use loop so an agent can't spin forever


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
) -> dict[str, Any]:
    """Stream one assistant turn through the configured (or specified) provider.

    Returns ``{"text": …, "usage": {…}, "stop_reason": …}``.
    When no usable provider is available a placeholder message is streamed so the
    UI keeps working in demo mode.

    When ``tools`` and ``on_tool`` are supplied (Anthropic only), this runs a full
    tool-use loop: the model may call tools, each call is dispatched through
    ``on_tool``, and the result is fed back until the model stops calling tools.
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
                                           effort, max_tokens, tools, on_tool)
        elif ptype == "openai":
            return await _openai_stream(system, messages, on_delta, provider,
                                        effort, max_tokens)
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
    max_tok = max_tokens or provider.get("max_tokens", 4096)

    use_tools = bool(tools and on_tool)
    convo: list[dict[str, Any]] = list(messages)
    text_parts: list[str] = []
    in_tok = out_tok = 0
    final = None
    try:
        for _ in range(MAX_TOOL_ITERS if use_tools else 1):
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tok,
                "system": system,
                "messages": convo,
            }
            if use_tools:
                kwargs["tools"] = tools
            async with client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    text_parts.append(text)
                    await on_delta(text)
                final = await stream.get_final_message()
            in_tok += getattr(final.usage, "input_tokens", 0) or 0
            out_tok += getattr(final.usage, "output_tokens", 0) or 0

            if not use_tools or final.stop_reason != "tool_use":
                break

            tool_uses = [b for b in final.content
                         if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
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


async def _openai_stream(
    system: str,
    messages: list[dict[str, Any]],
    on_delta: OnDelta,
    provider: dict[str, Any],
    effort: str | None,
    max_tokens: int | None,
) -> dict[str, Any]:
    key = _resolve_api_key(provider)
    if not key:
        msg = f"（{provider.get('name')} API Key 未配置）"
        await on_delta(msg)
        return {"text": msg, "usage": {}, "stop_reason": "no_key"}

    model = provider.get("model", "gpt-4o")
    base_url = provider.get("base_url", "https://api.openai.com/v1").rstrip("/")
    max_tok = max_tokens or provider.get("max_tokens", 4096)

    text_parts: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            body: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tok,
                "stream": True,
                "messages": [{"role": "system", "content": system}] + messages,
            }
            async with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                json=body,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        choices = chunk.get("choices", [])
                        if not choices:
                            # usage metadata chunk with no delta
                            continue
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            text_parts.append(content)
                            await on_delta(content)
                    except json.JSONDecodeError:
                        continue
        return {"text": "".join(text_parts), "usage": {}, "stop_reason": "completed"}
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
