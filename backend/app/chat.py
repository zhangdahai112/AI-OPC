"""Real multi-agent conversation over Claude (streamed to the browser as SSE).

Supports both:
- channel mode (new): channel_id + multiple projects
- ticket mode (legacy): single project, for migrated data
"""
from __future__ import annotations

import asyncio

from . import (agents, channels, db, events, explorer, gates, llm, mcp, projects,
               prompts, skill_store, tools)
from .config import LLM_ANSWER_MAX_TOKENS
from .engine import ROLE_CN, get_ticket, post as engine_post, _set_roster_state

ROLE_KEYS = list(ROLE_CN.keys())

# read-only tools whose identical repeats can be safely short-circuited within a
# turn; mutating tools invalidate that cache (files may have changed).
_CACHEABLE_TOOLS = {"read_file", "grep", "find_symbol", "list_dir", "repo_map", "explore"}
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


# ---- system prompt assembly --------------------------------------------
def assemble_system(cid_or_tid: str, role: str, manifest: dict,
                    *, is_channel: bool = False) -> str:
    """Resolve the scope (channel/ticket) into an immutable ``PromptContext`` and
    render it through the declarative section pipeline in :mod:`prompts`.

    This is the thin I/O adapter: it does the db/git lookups (members, project
    grounding, skill index) and hands pure data to the pure renderers. All prompt
    *content* and section ordering live in :mod:`prompts`.
    """
    if is_channel:
        ch = channels.get_channel(cid_or_tid)
        project_ids = [p["project_id"] for p in ch.get("projects", []) if p.get("project_id")]
        scope_name = ch.get("name", "群聊")
        members = ch.get("members", [])
    else:
        t = get_ticket(cid_or_tid)
        project_ids = [t.get("project_id")] if t.get("project_id") else []
        scope_name = t.get("title", "工单")
        members = t.get("roster", [])

    # resolve per-project grounding (docs / permanent memory / repo context)
    grounding: list[dict] = []
    for pid in project_ids:
        proj = projects.get_project(pid) if pid else None
        if not proj:
            continue
        grounding.append({
            "name": proj["name"],
            "docs": (proj.get("docs") or "")[:4000],
            "memory": projects.get_agent_memory(pid, role) or "",
            "repo_context": projects.repo_context(pid, role=role) or "",
        })

    ctx = prompts.PromptContext(
        role=role,
        manifest=manifest,
        scope_name=scope_name,
        member_roles=tuple(r["role"] for r in members if r.get("role") != role),
        project_ids=tuple(project_ids),
        tools=frozenset(manifest.get("harness", {}).get("builtinTools", [])),
        grounding=tuple(grounding),
        skills_index=skill_store.system_index(manifest.get("skills", []) or []) or "",
    )
    return prompts.build_system(ctx)


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
    # Ordered timeline of the turn: text segments interleaved with tool calls in
    # the exact order they happened, so the UI can render the model's narration
    # and its tool use nested together (think→verify→conclude) instead of stacking
    # "all tools, then all text". html/toolCalls are still kept for back-compat.
    steps: list[dict] = []
    _flushed = 0  # chars of "".join(acc) already emitted as a text step

    def _flush_text() -> None:
        """Turn text accumulated since the last flush into a timeline text step."""
        nonlocal _flushed
        joined = "".join(acc)
        seg = _sanitize_answer(joined[_flushed:])
        _flushed = len(joined)
        if seg:
            steps.append({"type": "text", "text": seg})

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

    allowed = agents.allowed_tools(manifest)
    if ctx.has_repo:
        builtin_specs = tools.tool_specs(allow=allowed)
    else:
        # no writable project yet — still expose create_project so an agent can
        # scaffold one (then write into it) instead of just describing it.
        builtin_specs = tools.tool_specs(allow=allowed & {"create_project"})
    tool_specs = builtin_specs + mcp_specs

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
                rec = {"tool": name, "text": label, "result": stub}
                _flush_text()  # keep the timeline ordered: text-so-far, then this call
                tool_calls.append(rec)
                steps.append({"type": "tool", **rec})
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
            elif name == "create_project":
                # provision a real writable project and bind it to this channel,
                # so the very next agent turn can write_file into it.
                pj = await asyncio.to_thread(
                    projects.create_local_project,
                    args.get("name", ""), args.get("description", "") or "")
                if is_channel and pj:
                    channels.add_project_to_channel(cid_or_tid, pj["id"])
                result = (
                    f"已创建项目「{pj['name']}」(id={pj['id']})，git 工作区已初始化并绑定到当前群。"
                    "现在这个群里就有可写仓库了——请立刻用 write_file 把项目骨架逐个文件落地"
                    "（或 @developer 来落地），不要只给方案文本。"
                    if pj else "（项目创建失败）")
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
            _flush_text()  # flush the narration that preceded this call, then the call
            tool_calls.append(rec)
            steps.append({"type": "tool", **rec})
            # persist incrementally so a long turn survives a reload
            db.execute(
                f"UPDATE {table} SET payload=? WHERE id=?",
                (db.dumps({"kind": "agent", "html": "".join(acc),
                           "message_id": mid, "streaming": True,
                           "toolCalls": tool_calls, "steps": steps}), mid))
            db.audit("tool", ticket_id=cid_or_tid, actor=role,
                     detail={"tool": name, "args": label})
            return result

    res = await llm.stream_reply(system, msgs, on_delta,
                                 tools=(tool_specs or None), on_tool=on_tool,
                                 max_tokens=LLM_ANSWER_MAX_TOKENS)
    full = _sanitize_answer(res.get("text") or "".join(acc))
    _flush_text()  # capture the final answer text as the trailing timeline step

    payload = {"kind": "agent", "html": full, "message_id": mid}
    if tool_calls:
        payload["toolCalls"] = tool_calls
    if steps:
        payload["steps"] = steps
    db.execute(f"UPDATE {table} SET payload=? WHERE id=?", (db.dumps(payload), mid))
    events.emit("message", ticket=cid_or_tid, agent=role,
                payload={**payload, "final": True})

    # Durability/visibility: if this turn wrote to disk, commit the agent's own
    # clone so its work has a stable HEAD to browse and gates can diff against it.
    # Also propagate changes back to the base checkout so other roles can see them.
    # Best-effort and off the loop — a commit failure must never fail the turn.
    if any(tc["tool"] in _MUTATING_TOOLS for tc in tool_calls):
        for pid in project_ids:
            try:
                await asyncio.to_thread(
                    projects.commit_agent_work, pid, role,
                    f"agent({role}): {(_sanitize_answer(full)[:60] or '本轮改动')}")
                await asyncio.to_thread(
                    projects.propagate_to_base, pid, role)
            except Exception:
                pass  # stale-but-visible working tree beats a crashed turn

    _set_state(cid_or_tid, role, "done", is_channel=is_channel)
    db.audit("tool", ticket_id=cid_or_tid, actor=role,
             detail={"real_reply": True, "tools": len(tool_calls),
                     "usage": res.get("usage"), "stop": res.get("stop_reason")})
    return full


MAX_HANDOFF_DEPTH = 4  # cap relay chain so it can't loop forever
_MAX_SAME_ROLE_TURNS = 3  # multi-turn: max consecutive turns for the same role


def _relay_mode(cid_or_tid: str, *, is_channel: bool) -> str:
    """'manual' gates every agent→agent handoff behind a human confirm; 'auto' lets
    agents relay freely. Manual mode is a channel feature — tickets are always auto."""
    return channels.get_mode(cid_or_tid) if is_channel else "auto"


async def human_turn(cid_or_tid: str, text: str, *, is_channel: bool = False) -> None:
    """Human posted `text`. Route to an agent (honoring @mentions). In auto mode a
    reply may relay to agents it @-mentions, forming a collaboration chain; in manual
    mode each such handoff first waits for a human confirm (see :func:`resume_handoff`)."""
    members = _members_of(cid_or_tid, is_channel=is_channel)

    # explicit @mentions in the human message take priority over auto-routing
    mentioned = detect_mentions(text, members)
    responders = mentioned or [await pick_responder(cid_or_tid, is_channel=is_channel)]

    # Relay (auto-handoff to whoever a reply @-mentions) is orchestration. If the
    # human directly @-mentioned a NON-coordinator agent, respect exactly who they
    # called: only those reply, nobody chimes in uninvited. @-ing only the
    # coordinator (or naming no one) keeps the relay chain on.
    allow_relay = not mentioned or set(mentioned) == {"coordinator"}
    manual = _relay_mode(cid_or_tid, is_channel=is_channel) == "manual"

    await _relay_walk(cid_or_tid, is_channel=is_channel, members=members,
                      queue=list(responders), seen=set(),
                      manual=manual, allow_relay=allow_relay)


async def _relay_walk(cid_or_tid: str, *, is_channel: bool, members: list[str],
                      queue: list[str], seen: set[str], manual: bool,
                      allow_relay: bool = True) -> None:
    """Run each queued agent, then collect whoever its reply @-mentions. In auto mode
    those get appended and run in the same pass; in manual mode the walk stops and
    posts a confirm card, resumed by the human via :func:`resume_handoff`.

    Supports **multi-turn**: if an agent's reply contains "继续" or "未完" (more work),
    it is re-queued (up to ``_MAX_SAME_ROLE_TURNS`` per walk). This lets the same
    agent continue coding across multiple LLM calls when a single turn's token budget
    isn't enough."""
    # auto relays are capped to catch runaway loops; manual is human-gated each hop.
    bound = len(members) * _MAX_SAME_ROLE_TURNS if not manual else len(members) + 1
    turn_counts: dict[str, int] = {}
    depth = 0
    while queue and depth < bound:
        role = queue.pop(0)
        if role not in members:
            continue
        turn_counts[role] = turn_counts.get(role, 0) + 1
        if turn_counts[role] > _MAX_SAME_ROLE_TURNS:
            continue
        seen.add(role)
        reply = await agent_reply(cid_or_tid, role, is_channel=is_channel)
        depth += 1
        if not allow_relay:
            continue
        nxts = [r for r in detect_mentions(reply, members)
                if r not in queue]
        # Multi-turn: if agent signals "not done yet", re-queue itself
        if "继续" in reply or "未完" in reply or "next turn" in reply.lower():
            if turn_counts.get(role, 0) < _MAX_SAME_ROLE_TURNS:
                nxts.append(role)
        for n in nxts:
            if n not in queue:
                queue.append(n)
        if not nxts:
            continue
        if manual:
            # gate: don't trigger the next agent(s) until the human approves
            remaining = [r for r in queue if r not in seen]
            _request_handoff_confirm(cid_or_tid, from_role=role,
                                     options=list(dict.fromkeys(nxts + remaining)),
                                     seen=seen)
            return
        queue.extend(nxts)


def _request_handoff_confirm(cid_or_tid: str, *, from_role: str,
                             options: list[str], seen: set[str]) -> None:
    """Persist the pending handoff and post a confirm card for the human to pick from."""
    db.kv_set(f"handoff:{cid_or_tid}",
              {"seen": list(seen), "options": options, "from": from_role})
    names = "、".join(ROLE_CN.get(r, r) for r in options)
    from_cn = ROLE_CN.get(from_role, from_role)
    _post_channel(cid_or_tid, "card", role=from_role, card="confirm",
                  from_role=from_role,
                  options=[{"role": r, "name": ROLE_CN.get(r, r)} for r in options],
                  note=f"「{from_cn}」想请 {names} 接力。手动模式下需你确认后才会触发。")


def _resolve_last_confirm(cid: str, choice: str) -> None:
    """Mark the most recent confirm card as resolved, so the UI stops offering it."""
    row = db.query_one(
        "SELECT id,payload FROM channel_messages WHERE channel_id=? AND kind='card' "
        "ORDER BY id DESC", (cid,))
    if not row:
        return
    payload = db.loads(row["payload"], {})
    if payload.get("card") == "confirm":
        payload["done"] = choice
        db.execute("UPDATE channel_messages SET payload=? WHERE id=?",
                   (db.dumps(payload), row["id"]))


async def resume_handoff(cid: str, choice: str) -> None:
    """Human answered a manual-mode confirm card. `choice` is a role key (run only
    that agent), 'all' (run every pending option), or 'none' (stop the chain)."""
    pending = db.kv_get(f"handoff:{cid}", None)
    if not pending:
        return
    db.execute("DELETE FROM kv WHERE key=?", (f"handoff:{cid}",))
    _resolve_last_confirm(cid, choice)

    members = _members_of(cid, is_channel=True)
    seen = set(pending.get("seen", []))
    options = [o for o in pending.get("options", []) if o in members]

    if choice == "none":
        _post_channel(cid, "sys", text="你选择不再触发后续成员，本轮到此为止。")
        return

    run = options if choice == "all" else [choice]
    queue = [r for r in run if r in members and r not in seen]
    await _relay_walk(cid, is_channel=True, members=members, queue=queue,
                      seen=seen, manual=True, allow_relay=True)


# ---- acceptance review (real gates on the real agent clone) -------------
def _post_channel(cid: str, kind: str, *, role: str | None = None, **payload) -> None:
    """Persist a channel message and stream it — the channel twin of engine.post."""
    cur = db.execute(
        "INSERT INTO channel_messages(channel_id,kind,role,payload,created_at) "
        "VALUES(?,?,?,?,?)",
        (cid, kind, role, db.dumps({"kind": kind, **payload}), db.now()))
    events.emit("message", ticket=cid, agent=role,
                payload={"kind": kind, "message_id": cur.lastrowid, **payload})


async def request_review(cid: str, role: str = "developer") -> None:
    """Run the real acceptance gates against `role`'s current work in this channel:
    commit the per-role clone → resolve its checkout → run the gate pipeline on it
    → post a gate-evidence card back to the channel. This is the manual "申请验收"
    entry point; auto-triggering after a turn comes later (A2/B)."""
    ch = channels.get_channel(cid)
    if not ch:
        return
    project_ids = _project_ids(cid, is_channel=True)
    if not project_ids:
        _post_channel(cid, "sys", text="该作战群未绑定项目，无法验收。")
        return

    pid = project_ids[0]
    sha = await asyncio.to_thread(projects.commit_agent_work, pid, role, "gate: 申请验收")
    root = await asyncio.to_thread(projects.agent_root, pid, role)
    if not root:
        _post_channel(cid, "sys", text=f"「{ROLE_CN.get(role, role)}」暂无可门禁的工作副本。")
        return
    db.kv_set(f"head:{cid}", sha or "")

    def _gemit(type, **kw):
        events.emit(type, ticket=cid, payload=kw.get("payload", {}))

    results = await gates.run_pipeline(cid, str(root), sha or "", _gemit)

    summary = [{"gate": r.gate_id, "status": r.status, "evidence": r.evidence}
               for r in results]
    failed = any(r.status == "fail" for r in results)
    ok = not failed and any(r.status == "pass" for r in results)
    _post_channel(cid, "card", role=role, card="gate",
                  title="验收结果", sha=sha or "", ok=ok, results=summary)
