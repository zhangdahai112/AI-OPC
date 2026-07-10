"""Real multi-agent conversation over Claude (streamed to the browser as SSE).

Supports both:
- channel mode (new): channel_id + multiple projects
- ticket mode (legacy): single project, for migrated data
"""
from __future__ import annotations

import asyncio

from . import (agents, channels, db, events, explorer, llm, mcp, projects,
               skill_store, tools)
from .config import LLM_ANSWER_MAX_TOKENS
from .engine import ROLE_CN, get_ticket, post as engine_post, _set_roster_state

ROLE_KEYS = list(ROLE_CN.keys())

# Per-role investigation lens — the shared mechanics (grep/explore/methodology) are
# role-agnostic; what differs is *what* each role investigates for. A single line
# per role, appended to the shared methodology. Overridable per (project,role) via
# manifest["prompt"]["investigationLens"] — this map is only the platform default.
_ROLE_INVESTIGATION_LENS = {
    "analyst": "你的调查侧重：摸清架构、数据流、入口与需求符合度——广度优先；"
               "遇到跨前后端/多文件的问题优先用 explore 一次铺开。",
    "developer": "你的调查侧重：精准定位要改的代码，并在动手前评估影响面"
                 "（调用方、依赖、blast radius），避免改一处漏一片。",
    "tester": "你的调查侧重：定位可测点与复现路径，核查现有测试覆盖与运行方式"
              "（怎么跑、跑哪些），再决定补什么测试。",
    "devops": "你的调查侧重：配置/部署/CI 相关——Dockerfile、nginx、.env、流水线、"
              "密钥与出网，优先搜这些配置与脚本文件。",
    "reporter": "你的调查侧重：只读地摸清高层现状与结论，做简洁汇总，"
                "不深挖实现细节、不改动仓库。",
}

# read-only tools whose identical repeats can be safely short-circuited within a
# turn; mutating tools invalidate that cache (files may have changed).
_CACHEABLE_TOOLS = {"read_file", "grep", "list_dir", "repo_map", "explore"}
_MUTATING_TOOLS = {"write_file", "run_command"}


def _call_key(name: str, args: dict) -> str:
    """Stable identity for a tool call, used to dedup exact repeats in one turn."""
    import json
    try:
        return name + ":" + json.dumps(args, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return name + ":" + repr(args)


import re as _re

# Strip leaked model control-tokens / malformed tool-call markup from an answer —
# e.g. DeepSeek spilling its ｜｜DSML｜｜ function-call scaffolding as plain text.
_CTRL_GARBAGE = _re.compile(
    r"<[^>]*DSML[^>]*>|｜+DSML｜+[A-Za-z_]*|<｜[^>]*>|"
    r"</?(?:read_file|grep|list_dir|repo_map|write_file|run_command|explore)\b[^>]*>")


def _sanitize_answer(text: str) -> str:
    if not text:
        return text
    return _CTRL_GARBAGE.sub("", text).strip()


def _harness_text(tools_enabled: set[str]) -> str:
    """The tool-use instructions, restricted to the tools this agent actually
    holds (least privilege). A read-only agent is told so explicitly rather than
    being handed write/run instructions it can't execute."""
    # coordinator has zero tools — it only routes & manages
    if not tools_enabled:
        return (
            "\n【你是纯管控角色，没有操作工具】\n"
            "- 你无权访问代码仓库、不能读文件、不能搜索代码、不能写文件、不能跑命令。\n"
            "- 你的职责是分析需求、拆解任务、按角色分派给群里其他成员来执行。\n"
            "- 需要具体技术动作时，用「@角色key」（如 @developer、@tester、@devops）明确点名转交。\n"
            "- 汇总各成员的产出，向人类汇报进度与决策点，但不要替他们干活。")
    lines = ["\n【你有真实工具，必须实际使用，不要凭空臆测代码】"]
    if tools_enabled & {"list_dir", "read_file", "grep", "repo_map"}:
        lines.append("- repo_map：生成仓库结构地图（目录树、入口、依赖、符号），"
                     "在首次接触一个仓库时优先调用；不要一上来就凭文件名猜测。")
        lines.append("- list_dir / read_file / grep：查看具体目录/文件/搜索；"
                     "回答涉及代码前，先用它们核实，不要编造文件名或实现。"
                     "read_file 过长会截断，用 offset 继续读完，别只看开头就下结论。")
        lines.append(
            "\n【调查纪律 — 避免「搜不到 = 不存在」的误判】\n"
            "- grep 是正则、忽略大小写：判断「有没有某能力」时，一次搜一族同义词，"
            "例如加解密可搜 "
            "`encrypt|decrypt|加密|解密|AES|cipher|secret|salt|sign|hmac|hashlib|"
            "Fernet|Crypto|xor|scramble|watermark|secure_link|防盗链`。\n"
            "- 数据要双向追踪：既看写入/上传/落盘路径，也看读取/下发/下载/serve 路径——"
            "「解密」通常发生在读取侧，只看上传路径会漏。\n"
            "- 工具回传的「未命中 / 已截断」只代表在你已扫描的范围内，绝不等于「不存在」。"
            "要下「没有 X」这种结论前，必须正向定位到具体代码，或如实说明你到底搜了哪些"
            "目录/文件、还有哪些没覆盖。绝不能把「我没搜到」偷换成「系统里没有」。")
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
    if project_ids:
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
            ctx = projects.repo_context(pid, role=role)
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

    if tools_enabled:
        parts.append(
            "\n⚠️ 去锚定：上文中其他成员（含项目经理）的结论只是线索，不是已证实的事实。"
            "涉及事实判断（有没有、是不是、在哪里），必须自己用工具复核到具体代码再表态；"
            "不要因为别人先说了某个结论就顺着确认。你可以、且应该推翻错误的转述。")
        parts.append(
            "\n【调查方法论 — 先规划再动手，别只追字面路径】\n"
            "1. 先复述用户的真实意图，并主动放宽范围：把「X上传怎么加密」理解成"
            "「这个系统用密码学怎么处理 X」——能力常常不在名字最直白的那条路径上。\n"
            "2. 动手搜之前，先列出跨层候选位置逐一排查：前端(src/utils、components)、"
            "后端接口(api/openapi)、service、utils、模型、docs 设计文档、配置(.env/nginx)；"
            "别只盯一个目录。\n"
            "3. 按「能力的词汇」搜，而不是问题里的名词：查加解密就搜 "
            "encrypt|decrypt|加密|解密|AES|cipher|bng|防盗链|sign|secure_link|crypto，"
            "而不是只搜 upload/图片上传。\n"
            "4. 数据要覆盖两个方向：输入/上传 与 输出/下发/serve/前端渲染——"
            "很多加密/解密只在输出侧或前端。\n"
            "5. 下关键结论前先扫 docs/README/设计文档，那里常直接写着方案入口。\n"
            "6. 一条路径没有 X ≠ 整个系统没有 X：证明「上传路径传明文」不等于「系统不加密」。"
            "下否定结论前，必须已覆盖上面所有层，否则只能说「在我查过的 X 范围内没有」。\n"
            "7. 省 token：工具调用要吝啬——先 grep 定位、再只读必要文件一次，"
            "不要重复读同一个文件或重复搜同一个词（重复调用会被系统拦截并提示）；"
            "遇到需要横扫多文件/跨前后端的问题，优先用 explore 交给探查子代理一次搞定，"
            "**并信任它返回的结论，不要自己再手动重扫一遍**；拿到足够信息就停手给结论。")
        # role-specific investigation lens (manifest override → platform default)
        lens = (manifest.get("prompt", {}).get("investigationLens")
                or _ROLE_INVESTIGATION_LENS.get(role))
        if lens:
            parts.append("\n" + lens)

    # coordinator-specific routing mandate
    if role == "coordinator":
        parts.append(
            "\n⚠️ 你是项目经理，不是执行者。你的工作方式：\n"
            "1. 收到人类指令后，先拆解成可分配给不同角色的子任务。\n"
            "2. 用「@角色key」将每个子任务点名指派给对应的专业 agent。\n"
            "3. 等他们各自回复后，汇总结果并向人类汇报。\n"
            "4. 遇到阻塞或需要决策时，升级给人类操作员而不是自己动手。\n"
            "❌ 禁止：亲自读代码、亲自写代码、亲自跑命令、亲自做测试——"
            "这些全是其他成员的工作，你做就是越权。\n"
            "\n"
            "📋 收到任务后的标准探索流程：\n"
            "1. 先用 @analyst 调用 repo_map 了解仓库全局结构\n"
            "2. 让 @analyst 读入口文件理解主流程\n"
            "3. 让 @analyst 用 grep 搜关键概念定位相关模块\n"
            "4. 形成方案后，@对应角色执行具体任务")
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
    # Each agent is confined to its own per-role independent clone (isolation);
    # the first turn clones via git, so run it off the event loop.
    ctx = await asyncio.to_thread(tools.ToolContext.for_agent, project_ids, role)
    mcp_mounts = manifest.get("mcp", []) or []
    mcp_specs = mcp.tool_specs(mcp_mounts)

    builtin_specs = tools.tool_specs(allow=agents.allowed_tools(manifest)) if ctx.has_repo else None
    tool_specs = (builtin_specs or []) + mcp_specs

    tool_calls: list[dict] = []
    # within-turn dedup: identical read-only calls (re-reading the same file,
    # re-running the same grep) return a short stub instead of re-executing and
    # re-bloating the transcript — the #1 source of wasted tokens in long turns.
    seen_calls: dict[str, bool] = {}
    on_tool = None
    if tool_specs:
        async def on_tool(name: str, args: dict, _uid: str) -> str:
            is_mcp = mcp.is_mcp_tool(name)
            label = name if is_mcp else tools.summarize(name, args)
            events.emit("tool_call", ticket=cid_or_tid, agent=role,
                        payload={"message_id": mid, "tool": name, "text": label})

            ck = _call_key(name, args)
            if name in _CACHEABLE_TOOLS and ck in seen_calls:
                stub = (f"（重复调用：你已经用相同参数执行过 {label or name}，"
                        "为省 token 未再执行。请基于已获得的信息推进，不要用相同参数重复调用。）")
                tool_calls.append({"tool": name, "text": label, "result": stub})
                return stub

            if is_mcp:
                # MCP calls are async (network/subprocess JSON-RPC), never raise
                result = await mcp.execute(name, args, mcp_mounts)
            elif name == "explore":
                # read-only fan-out sub-agent; surface its internal sweeps live so
                # the operator sees the investigation, nested under this message.
                async def _sub_emit(sname: str, sargs: dict, _suid: str) -> None:
                    events.emit("tool_call", ticket=cid_or_tid, agent=role,
                                payload={"message_id": mid, "tool": sname,
                                         "text": "↳ " + tools.summarize(sname, sargs)})
                result = await explorer.explore(
                    args.get("question", ""), ctx, project_ids,
                    role=role, on_subtool=_sub_emit)
            else:
                # built-in tools are blocking (subprocess/fs) — run off the loop
                result = await asyncio.to_thread(tools.execute, name, args, ctx)
            # remember read-only calls so exact repeats short-circuit; a mutating
            # tool (write/run) invalidates the cache since files may have changed.
            if name in _CACHEABLE_TOOLS:
                seen_calls[ck] = True
            elif name in _MUTATING_TOOLS:
                seen_calls.clear()
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
                                 tools=(tool_specs or None), on_tool=on_tool,
                                 max_tokens=LLM_ANSWER_MAX_TOKENS)
    full = _sanitize_answer(res.get("text") or "".join(acc))

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
