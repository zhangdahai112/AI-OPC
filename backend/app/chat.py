"""Real multi-agent conversation over Claude (streamed to the browser as SSE).

Supports both:
- channel mode (new): channel_id + multiple projects
- ticket mode (legacy): single project, for migrated data
"""
from __future__ import annotations

import asyncio

from . import agents, channels, db, events, llm, mcp, projects, skill_store, tools
from .engine import ROLE_CN, get_ticket, post as engine_post, _set_roster_state

ROLE_KEYS = list(ROLE_CN.keys())


def _harness_text(tools_enabled: set[str]) -> str:
    """The tool-use instructions, restricted to the tools this agent actually
    holds (least privilege). A read-only agent is told so explicitly rather than
    being handed write/run instructions it can't execute."""
    lines = ["\n【你有真实工具，必须实际使用，不要凭空臆测代码】"]
    if tools_enabled & {"list_dir", "read_file", "grep"}:
        lines.append("- list_dir / read_file / grep：查看真实仓库结构与文件内容；"
                     "回答涉及代码前，先用它们核实，不要编造文件名或实现。")
    if "write_file" in tools_enabled:
        lines.append("- write_file：按你的职责真正修改/新增文件。")
    if "run_command" in tools_enabled:
        lines.append("- run_command：在仓库目录跑命令（测试 pytest/npm test、"
                     "git status/diff/add/commit、构建等）。")
    if tools_enabled & {"write_file", "run_command"}:
        lines.append("先调研（读/搜），再动手（写/跑），最后用 git diff/status 自查并简述"
                     "你改了什么、验证结果如何。涉及上线/删除/改库等不可逆动作时先说明并"
                     "请求人类确认，不要擅自执行。")
    else:
        lines.append("你当前是只读权限：只做调研与建议，不直接改仓库；"
                     "需要改动时用「@角色key」点名有写权限的成员来执行。")
    return "\n".join(lines)


# ---- system prompt assembly --------------------------------------------
def assemble_system(cid_or_tid: str, role: str, manifest: dict,
                    *, is_channel: bool = False) -> str:
    """Build the agent's system prompt from its resolved manifest.

    Ordered cold→hot for prompt-cache stability: stable identity / guardrails /
    harness first, then the semi-stable project grounding and member list.
    """
    if is_channel:
        ch = channels.get_channel(cid_or_tid)
        project_ids = [p["project_id"] for p in ch.get("projects", []) if p.get("project_id")]
        name = ch.get("name", "群聊")
        members = ch.get("members", [])
    else:
        t = get_ticket(cid_or_tid)
        project_ids = [t.get("project_id")] if t.get("project_id") else []
        name = t.get("title", "工单")
        members = t.get("roster", [])

    ident = manifest.get("identity", {})
    parts: list[str] = []
    parts.append(
        f"你是一名 AI {ident.get('name', ROLE_CN.get(role, role))}（角色 key: {role}），"
        f"在一个多 agent 协作「群聊」里与人类操作员和其他 agent 一起工作。当前群：{name}。")
    if ident.get("focus"):
        parts.append(f"你的职责聚焦：{ident['focus']}。")

    guardrails = manifest.get("prompt", {}).get("guardrails") or []
    if guardrails:
        parts.append("\n【你的质量红线】\n" + "\n".join(f"- {g}" for g in guardrails))

    tools_enabled = set(manifest.get("harness", {}).get("builtinTools", []))
    if project_ids and tools_enabled:
        parts.append(_harness_text(tools_enabled))

    # progressive-disclosure index of installed skills (name + when_to_use)
    skill_index = skill_store.system_index(manifest.get("skills", []) or [])
    if skill_index:
        parts.append(f"\n【可用技能】\n{skill_index}")

    for pid in project_ids:
        proj = projects.get_project(pid) if pid else None
        if proj:
            parts.append(f"\n【项目】{proj['name']}")
            if proj.get("docs"):
                parts.append(f"【需求文档】\n{proj['docs'][:4000]}")
            mem = projects.get_agent_memory(pid, role)
            if mem:
                parts.append(f"\n【你的永久记忆 / 本项目专属知识】\n{mem}")
            ctx = projects.repo_context(pid)
            if ctx:
                parts.append(f"\n【代码库上下文】\n{ctx}")

    others = [(r["role"], ROLE_CN.get(r["role"], r["role"]))
              for r in members if r.get("role") != role]
    if others:
        listing = "、".join(f"{cn}（@{key}）" for key, cn in others)
        parts.append(f"\n群里的其他成员：{listing}。")

    parts.append(
        "\n协作规则：只做你这个角色该做的事。遇到不属于你职责的问题，"
        "用「@角色key」（例如 @developer、@tester）明确点名转交给对应成员，"
        "被点名的成员会自动接力回复。不要臆测，不要替别人完成工作。"
        "回答要具体、可落地、简洁。涉及上线/改库/删除/权限等不可逆动作时，"
        "提示需要人类审批。用中文回答。")
    return "\n".join(parts)


def _transcript(cid_or_tid: str, *, is_channel: bool = False, limit: int = 30) -> str:
    if is_channel:
        rows = db.query(
            "SELECT kind,role,payload FROM channel_messages WHERE channel_id=? "
            "AND kind IN ('human','agent') ORDER BY id DESC LIMIT ?", (cid_or_tid, limit))
    else:
        rows = db.query(
            "SELECT kind,role,payload FROM messages WHERE ticket_id=? "
            "AND kind IN ('human','agent') ORDER BY id DESC LIMIT ?", (cid_or_tid, limit))
    lines = []
    for r in reversed(rows):
        p = db.loads(r["payload"], {})
        txt = (p.get("html") or "").strip()
        if not txt:
            continue
        who = "人类操作员" if r["kind"] == "human" else ROLE_CN.get(r["role"], r["role"])
        lines.append(f"【{who}】{txt}")
    return "\n".join(lines)


# ---- routing ------------------------------------------------------------
async def pick_responder(cid_or_tid: str, *, is_channel: bool = False) -> str:
    if is_channel:
        ch = channels.get_channel(cid_or_tid)
        candidates = [r["role"] for r in ch.get("members", []) if r["role"] != "reporter"]
    else:
        t = get_ticket(cid_or_tid)
        candidates = [r["role"] for r in t.get("roster", []) if r["role"] != "reporter"]

    if not candidates:
        return "coordinator"
    if len(candidates) == 1:
        return candidates[0]
    if not llm.available():
        for pref in ("coordinator", "developer", "analyst", "tester", "devops"):
            if pref in candidates:
                return pref
        return candidates[0]

    sys = ("你是作战群的调度器。根据最新对话，从候选角色里挑一个最适合回答的，"
           f"候选：{candidates}。只输出一个角色英文 key，不要其它文字。")
    msgs = [{"role": "user", "content": _transcript(cid_or_tid, is_channel=is_channel) +
             "\n\n谁来回答最新这条消息？只回角色 key。"}]
    swallowed: list[str] = []
    res = await llm.stream_reply(sys, msgs, lambda d: _noop(swallowed, d),
                                 effort="low", max_tokens=16)
    pick = (res.get("text") or "").strip().lower()
    for c in candidates:
        if c in pick:
            return c
    return candidates[0]


async def _noop(buf, d):
    buf.append(d)


# ---- @mention detection -------------------------------------------------
# Build name -> role-key maps so "@开发" or "@developer" both resolve.
_NAME_TO_ROLE = {cn: key for key, cn in ROLE_CN.items()}
_NAME_TO_ROLE.update({key: key for key in ROLE_CN})  # english keys too


def detect_mentions(text: str, candidates: list[str]) -> list[str]:
    """Return role keys @-mentioned in text, restricted to channel members."""
    if not text:
        return []
    found: list[str] = []
    for name, role in _NAME_TO_ROLE.items():
        if role in candidates and role not in found:
            if f"@{name}" in text:
                found.append(role)
    return found


def _members_of(cid_or_tid: str, *, is_channel: bool) -> list[str]:
    if is_channel:
        ch = channels.get_channel(cid_or_tid)
        return [r["role"] for r in (ch.get("members", []) if ch else [])]
    t = get_ticket(cid_or_tid)
    return [r["role"] for r in (t.get("roster", []) if t else [])]


def _project_ids(cid_or_tid: str, *, is_channel: bool) -> list[str]:
    if is_channel:
        ch = channels.get_channel(cid_or_tid)
        return [p["project_id"] for p in (ch.get("projects", []) if ch else [])
                if p.get("project_id")]
    t = get_ticket(cid_or_tid)
    return [t["project_id"]] if t and t.get("project_id") else []


def _set_state(cid_or_tid: str, role: str, state: str, *, is_channel: bool) -> None:
    """Update a member's state — channels and tickets live in different tables."""
    if is_channel:
        channels.set_member_state(cid_or_tid, role, state)
        events.emit("state", ticket=cid_or_tid, agent=role, payload={"state": state})
    else:
        _set_roster_state(cid_or_tid, role, state)


# ---- streamed agent reply ----------------------------------------------
async def agent_reply(cid_or_tid: str, role: str, *, is_channel: bool = False) -> str:
    """Stream one agent reply into the channel. When the channel is bound to a
    project checkout, the agent gets real tools (read/write/run) and every tool
    call is surfaced live and persisted onto the message."""
    table = "channel_messages" if is_channel else "messages"
    id_col = "channel_id" if is_channel else "ticket_id"

    _set_state(cid_or_tid, role, "working", is_channel=is_channel)

    cur = db.execute(
        f"INSERT INTO {table}({id_col},kind,role,payload,created_at) "
        f"VALUES(?,?,?,?,?)",
        (cid_or_tid, "agent", role, db.dumps({"kind": "agent", "html": "", "streaming": True}),
         db.now()))
    mid = cur.lastrowid
    events.emit("message", ticket=cid_or_tid, agent=role,
                payload={"kind": "agent", "html": "", "message_id": mid,
                         "streaming": True})

    project_ids = _project_ids(cid_or_tid, is_channel=is_channel)
    primary_pid = project_ids[0] if project_ids else None
    manifest = agents.resolve(primary_pid, role)

    system = assemble_system(cid_or_tid, role, manifest, is_channel=is_channel)
    msgs = [{"role": "user", "content":
             _transcript(cid_or_tid, is_channel=is_channel) +
             f"\n\n请以「{ROLE_CN[role]}」身份回复最新消息。"}]

    acc: list[str] = []

    async def on_delta(text: str):
        acc.append(text)
        events.emit_delta(cid_or_tid, role, str(mid), text)

    # real tools — built-in repo tools (only with a checked-out repo, least
    # privilege per manifest) plus any MCP-mounted tools from the manifest.
    ctx = tools.ToolContext.for_projects(project_ids)
    mcp_mounts = manifest.get("mcp", []) or []
    mcp_specs = mcp.tool_specs(mcp_mounts)

    builtin_specs = tools.tool_specs(allow=agents.allowed_tools(manifest)) if ctx.has_repo else None
    tool_specs = (builtin_specs or []) + mcp_specs

    tool_calls: list[dict] = []
    on_tool = None
    if tool_specs:
        async def on_tool(name: str, args: dict, _uid: str) -> str:
            is_mcp = mcp.is_mcp_tool(name)
            label = name if is_mcp else tools.summarize(name, args)
            events.emit("tool_call", ticket=cid_or_tid, agent=role,
                        payload={"message_id": mid, "tool": name, "text": label})
            if is_mcp:
                # MCP calls are async (network/subprocess JSON-RPC), never raise
                result = await mcp.execute(name, args, mcp_mounts)
            else:
                # built-in tools are blocking (subprocess/fs) — run off the loop
                result = await asyncio.to_thread(tools.execute, name, args, ctx)
            rec = {"tool": name, "text": label, "result": result[:1500]}
            tool_calls.append(rec)
            # persist incrementally so a long turn survives a reload
            db.execute(
                f"UPDATE {table} SET payload=? WHERE id=?",
                (db.dumps({"kind": "agent", "html": "".join(acc),
                           "message_id": mid, "streaming": True,
                           "toolCalls": tool_calls}), mid))
            db.audit("tool", ticket_id=cid_or_tid, actor=role,
                     detail={"tool": name, "args": label})
            return result

    res = await llm.stream_reply(system, msgs, on_delta,
                                 tools=(tool_specs or None), on_tool=on_tool)
    full = res.get("text") or "".join(acc)

    payload = {"kind": "agent", "html": full, "message_id": mid}
    if tool_calls:
        payload["toolCalls"] = tool_calls
    db.execute(f"UPDATE {table} SET payload=? WHERE id=?", (db.dumps(payload), mid))
    events.emit("message", ticket=cid_or_tid, agent=role,
                payload={**payload, "final": True})
    _set_state(cid_or_tid, role, "done", is_channel=is_channel)
    db.audit("tool", ticket_id=cid_or_tid, actor=role,
             detail={"real_reply": True, "tools": len(tool_calls),
                     "usage": res.get("usage"), "stop": res.get("stop_reason")})
    return full


MAX_HANDOFF_DEPTH = 4  # cap relay chain so it can't loop forever


async def human_turn(cid_or_tid: str, text: str, *, is_channel: bool = False) -> None:
    """Human posted `text`. Route to an agent (honoring @mentions) and let the
    reply relay to any agents it @-mentions, forming a collaboration chain."""
    members = _members_of(cid_or_tid, is_channel=is_channel)

    # 1) explicit @mentions in the human message take priority
    mentioned = detect_mentions(text, members)
    if mentioned:
        responders = mentioned
    else:
        responders = [await pick_responder(cid_or_tid, is_channel=is_channel)]

    # 2) each responder replies; if its reply @mentions others, relay to them
    seen: set[str] = set()
    queue = list(responders)
    depth = 0
    while queue and depth < MAX_HANDOFF_DEPTH:
        role = queue.pop(0)
        if role in seen or role not in members:
            continue
        seen.add(role)
        reply = await agent_reply(cid_or_tid, role, is_channel=is_channel)
        # relay: hand off to anyone this agent @mentioned (not already answered)
        for nxt in detect_mentions(reply, members):
            if nxt not in seen and nxt not in queue:
                queue.append(nxt)
        depth += 1
