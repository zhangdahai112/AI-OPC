"""Orchestration engine (PRD §4, arch §2-4).

The "skeleton outside the agent": durable lifecycle, contract-driven routing,
gate orchestration, human-in-the-loop suspension, and external stuck-detection.
Human steps suspend the workflow (status -> awaiting_approval/blocked) and persist
to SQLite; an API signal resumes from the stored state, which can span hours/days
(arch 3.1). Agent reasoning is not resumed — only durable facts are.

Lane decision (FR-2): small/clear tickets take the fast lane (single dev agent +
gates + human review, no channel/contract); larger ones become a war-room with a
roster and an approved collaboration contract.
"""
from __future__ import annotations

import asyncio
from typing import Any

from . import db, gates, memory
from .config import (
    DEFAULT_BUDGET,
    MAX_GATE_FAILURES,
    STUCK_NO_PROGRESS_SEC,
)
from .events import emit
from . import events
from .executors import Budget, RepoRef, TaskEnvelope, build_registry
from .config import WORKSPACES_DIR

REGISTRY = build_registry()

ROLE_CN = {
    "coordinator": "项目经理", "analyst": "需求分析", "developer": "开发",
    "tester": "测试", "devops": "运维", "reporter": "上报",
}

# ---------------------------------------------------------------------------
# config access (persisted in kv; seeded in seed.py)
# ---------------------------------------------------------------------------
def get_config() -> dict[str, Any]:
    return db.kv_get("config", {})


def template_for(ttype: str) -> dict[str, Any]:
    cfg = get_config()
    for t in cfg.get("templates", []):
        if t["type"] == ttype:
            return t
    # default fallback
    return {"type": ttype, "lane": "作战群",
            "roster": ["coordinator", "developer", "tester"]}


def enabled_gates() -> list[str]:
    cfg = get_config()
    on = {g["name"]: g for g in cfg.get("gates", [])}
    label_to_id = {g["label"]: g["id"] for g in gates.GATE_SPEC}
    ids = []
    for g in gates.GATE_SPEC:
        spec = on.get(g["label"])
        if spec is None or spec.get("on", True):
            ids.append(g["id"])
    return ids


# ---------------------------------------------------------------------------
# small helpers over the ticket record
# ---------------------------------------------------------------------------
def get_ticket(tid: str) -> dict | None:
    row = db.query_one("SELECT * FROM tickets WHERE id=?", (tid,))
    if not row:
        return None
    t = dict(row)
    for col in ("contract", "pipeline", "budget"):
        t[col] = db.loads(t[col], {})
    t["roster"] = [dict(r) for r in
                   db.query("SELECT role,state FROM roster WHERE ticket_id=?", (tid,))]
    t["stream"] = _stream(tid)
    t["gate_results"] = _gate_results(tid)
    t["needs"] = bool(t["needs"])
    t["trusted"] = bool(t["trusted"])
    return t


def list_tickets() -> list[dict]:
    rows = db.query("SELECT id FROM tickets ORDER BY created_at DESC")
    return [get_ticket(r["id"]) for r in rows]


def _stream(tid: str) -> list[dict]:
    rows = db.query(
        "SELECT kind,role,payload,created_at FROM messages "
        "WHERE ticket_id=? ORDER BY id", (tid,))
    out = []
    for r in rows:
        m = {"kind": r["kind"], "role": r["role"], "t": _clock(r["created_at"])}
        m.update(db.loads(r["payload"], {}))
        out.append(m)
    return out


def _gate_results(tid: str) -> dict[str, dict]:
    """Return the latest result for each gate, keyed by gate_id."""
    rows = db.query(
        "SELECT gate_id,status,evidence,commit_sha,owner_on_fail,created_at "
        "FROM gate_results WHERE ticket_id=? ORDER BY created_at DESC", (tid,))
    out: dict[str, dict] = {}
    for r in rows:
        gid = r["gate_id"]
        if gid in out:
            continue
        out[gid] = {
            "gate_id": gid,
            "status": r["status"],
            "evidence": db.loads(r["evidence"], {}),
            "commit_sha": r["commit_sha"],
            "owner_on_fail": r["owner_on_fail"],
            "created_at": r["created_at"],
        }
    return out


def _clock(ts: float) -> str:
    import time
    return time.strftime("%H:%M", time.localtime(ts))


def _set_status(tid: str, status: str, needs: bool | None = None) -> None:
    if needs is None:
        db.execute("UPDATE tickets SET status=?, updated_at=? WHERE id=?",
                   (status, db.now(), tid))
    else:
        db.execute("UPDATE tickets SET status=?, needs=?, updated_at=? WHERE id=?",
                   (status, int(needs), db.now(), tid))
    emit("state", ticket=tid, payload={"ticket_status": status})


def _set_roster_state(tid: str, role: str, state: str) -> None:
    db.execute(
        "INSERT INTO roster(ticket_id,role,state) VALUES(?,?,?) "
        "ON CONFLICT(ticket_id,role) DO UPDATE SET state=excluded.state",
        (tid, role, state))
    emit("state", ticket=tid, agent=role, payload={"state": state})


def _set_pipeline(tid: str, gate_id: str, state: str) -> None:
    t = get_ticket(tid)
    pipe = t["pipeline"] or {"steps": gates.initial_pipeline()}
    for s in pipe["steps"]:
        if s["id"] == gate_id:
            s["state"] = state
    db.execute("UPDATE tickets SET pipeline=?, updated_at=? WHERE id=?",
               (db.dumps(pipe), db.now(), tid))


def post(tid: str, kind: str, *, role: str | None = None, **payload) -> None:
    db.execute(
        "INSERT INTO messages(ticket_id,kind,role,payload,created_at) VALUES(?,?,?,?,?)",
        (tid, kind, role, db.dumps(payload), db.now()))
    emit("message", ticket=tid, agent=role, payload={"kind": kind, **payload})
    _touch(tid)


# external stuck-detection bookkeeping (FR-5.2)
def _touch(tid: str) -> None:
    db.kv_set(f"lastact:{tid}", db.now())


# ---------------------------------------------------------------------------
# triage + lifecycle entry
# ---------------------------------------------------------------------------
def create_ticket(*, title: str, ttype: str, description: str = "",
                  repo: str = "", source: str = "human",
                  project_id: str = "", roster: list[str] | None = None) -> dict:
    tid = _next_id()
    tmpl = template_for(ttype)
    lane = "fast" if tmpl.get("lane") == "快车道" else "warroom"
    trusted = source != "reporter"  # reporter-sourced tickets are untrusted (FR-1.3)
    # explicit roster selection ("拉哪些 agent 进群"); fall back to the template
    roles = roster or tmpl["roster"]

    db.execute(
        "INSERT INTO tickets(id,title,type,description,repo,project_id,source,lane,"
        "status,needs,trusted,contract,pipeline,budget,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, title, ttype, description, repo, project_id, source, lane, "working",
         0, int(trusted), None, db.dumps({"steps": gates.initial_pipeline()}),
         db.dumps({**DEFAULT_BUDGET, "spent_tokens": 0, "spent_cost": 0.0, "steps": 0}),
         db.now(), db.now()))

    db.audit("decision", ticket_id=tid, actor="engine",
             detail={"triage": lane, "type": ttype, "source": source,
                     "project": project_id, "roster": roles})
    emit("system", ticket=tid, payload={"text": f"工单 {tid} 已创建"})

    for role in roles:
        _set_roster_state(tid, role, "idle")
    post(tid, "sys",
         text=f"{'监控自动' if source=='reporter' else '你'}创建工单，"
              f"拉入 {len(roles)} 个 agent：{'、'.join(ROLE_CN[r] for r in roles)}")

    # Human dispatches tasks in the channel; agents only respond to human messages.
    # (To start auto, re-add: events.spawn(chat.agent_reply(tid, first)))
    return get_ticket(tid)


def _next_id() -> str:
    n = db.kv_get("ticket_seq", 1042)
    db.kv_set("ticket_seq", n + 1)
    return f"T-{n}"


# ---------------------------------------------------------------------------
# war-room: collaboration contract (FR-3)
# ---------------------------------------------------------------------------
def _propose_contract(tid: str, tmpl: dict) -> None:
    t = get_ticket(tid)
    roster = tmpl["roster"]
    contract = {
        "ticket_id": tid, "type": t["type"], "channel_id": f"ch-{tid}",
        "roster": [{"agent": r, "owns": _owns(r)} for r in roster],
        "routing": {
            "requirement_ambiguity": "human:PM",
            "scope_change": "human:PM",
            "credential_needed": "human:owner",
            "root_cause": "developer",
            "build_failure": "developer",
            "*": "coordinator",
        },
        "escalation": {"max_self_retries": 3, "on_exhausted": "coordinator",
                       "coordinator_stuck": "human:on_call", "human_ack_sla_min": 30},
        "gates": [g for g in ["deploy_prod", "schema_migration", "delete", "perms_change"]],
    }
    db.execute("UPDATE tickets SET contract=?, status=?, needs=?, updated_at=? WHERE id=?",
               (db.dumps(contract), "planning", 1, db.now(), tid))
    if "coordinator" in roster:
        _set_roster_state(tid, "coordinator", "working")
    post(tid, "agent", role="coordinator",
         html="我草拟了这个群的协作契约：谁负责什么、问题给谁、上线前等你点头。请你过目批准。")
    post(tid, "card", card="contract", needs_approval=True)
    db.audit("decision", ticket_id=tid, actor="coordinator",
             detail={"contract_proposed": contract})
    _set_status(tid, "planning", needs=True)


def _owns(role: str) -> list[str]:
    return {
        "coordinator": ["*"], "analyst": ["requirement", "spec"],
        "developer": ["code", "build_failure", "root_cause"],
        "tester": ["tests", "coverage"],
        "devops": ["deploy", "rollback", "env_issue"],
        "reporter": ["monitor"],
    }.get(role, [])


def approve_contract(tid: str) -> dict:
    t = get_ticket(tid)
    db.audit("approval", ticket_id=tid, actor="human",
             detail={"contract_approved": True})
    post(tid, "sys", text="你已批准协作契约，开始按契约协作")
    _set_status(tid, "working", needs=False)
    events.spawn(_run_warroom(tid))
    return get_ticket(tid)


# ---------------------------------------------------------------------------
# fast lane (FR-2.2 / 4.2)
# ---------------------------------------------------------------------------
async def _run_fastlane(tid: str) -> None:
    _set_status(tid, "working", needs=False)
    await _dispatch(tid, "developer",
                    instruction="按快车道直接修复该工单并提交。",
                    done_hint="已修复并提交，申请验收。")
    await _run_gate_pipeline(tid)


# ---------------------------------------------------------------------------
# war-room execution: analyst -> developer -> gates (FR-4)
# ---------------------------------------------------------------------------
async def _run_warroom(tid: str) -> None:
    t = get_ticket(tid)
    roles = {r["role"] for r in t["roster"]}

    if "analyst" in roles:
        # ambiguous feature requirements escalate to a human (FR-4.3 / 5.1)
        if t["type"] == "feature" and _is_ambiguous(t):
            await _dispatch(tid, "analyst",
                            instruction="梳理需求，确认验收标准。",
                            done_hint="需求存在歧义，需要人来定方向。")
            return _escalate(tid, "analyst", "requirement_ambiguity",
                             "导出格式用 CSV 还是 Excel？要不要支持自己选导出哪些列？这会影响工作量。")
        if "analyst" in roles:
            await _dispatch(tid, "analyst",
                            instruction="梳理需求并产出规格。",
                            done_hint="规格已定，交给开发。")
            post(tid, "card", card="handoff", **{"from": "analyst", "to": "developer",
                 "pt": "spec_ready", "note": "规格已确认，开始开发"})

    await _dispatch(tid, "developer",
                    instruction="按规格实现，本地验证后提交。",
                    done_hint="已提交代码，申请验收。")
    await _run_gate_pipeline(tid)


def _is_ambiguous(t: dict) -> bool:
    d = (t.get("description") or "").strip()
    return len(d) < 12  # short/empty description => unclear spec


# ---------------------------------------------------------------------------
# dispatch one agent through an executor (arch 3.3)
# ---------------------------------------------------------------------------
async def _dispatch(tid: str, role: str, *, instruction: str, done_hint: str) -> None:
    t = get_ticket(tid)
    cfg = get_config()
    exec_name = next((a["exec"] for a in cfg.get("agents", []) if a["role"] == role),
                     "内置 Agent SDK")
    executor = REGISTRY.get(exec_name, REGISTRY["内置 Agent SDK"])

    _set_roster_state(tid, role, "working")
    db.audit("route", ticket_id=tid, actor="coordinator",
             detail={"dispatch": role, "executor": exec_name})

    wt = str(WORKSPACES_DIR / tid)
    env = TaskEnvelope(
        task_id=f"{tid}:{role}", ticket_id=tid, channel_id=f"ch-{tid}", role=role,
        instruction=f"[{ROLE_CN[role]}] {instruction}",
        repo=RepoRef(name=t.get("repo") or tid, worktree=wt, branch="main"),
        allowed_tools=["read", "grep", "edit", "bash"],
        budget=Budget(**{k: DEFAULT_BUDGET[k] for k in
                         ("max_tokens", "max_steps", "timeout_sec", "max_cost_usd")}),
        done_hint=done_hint, meta={"title": t["title"]},
    )

    def _emit(type, **kw):
        # bridge executor events to the channel stream + ws
        payload = kw.get("payload", {})
        if type in ("message", "result"):
            post(tid, "agent", role=role, html=payload.get("text", ""))
        elif type == "tool_call":
            emit("tool_call", ticket=tid, agent=role, payload=payload)
        else:
            emit(type, ticket=tid, agent=role, payload=payload)
        _touch(tid)

    res = await executor.run(env, _emit)
    _bill(tid, res.usage)

    if res.status == "completed":
        _set_roster_state(tid, role, "done" if role != "coordinator" else "working")
        if res.head_sha:
            db.kv_set(f"head:{tid}", res.head_sha)
            db.audit("tool", ticket_id=tid, actor=role,
                     detail={"commit": res.head_sha, "diff": res.diff_ref})
    elif res.status == "needs_input":
        _escalate(tid, role, "blocked", res.summary or "需要人来确认。")
    db.audit("decision", ticket_id=tid, actor=role,
             detail={"exec_status": res.status, "usage": res.usage})


def _bill(tid: str, usage: dict) -> None:
    t = get_ticket(tid)
    b = t["budget"]
    b["spent_tokens"] = b.get("spent_tokens", 0) + usage.get("tokens", 0)
    b["spent_cost"] = round(b.get("spent_cost", 0.0) + usage.get("cost_usd", 0.0), 4)
    b["steps"] = b.get("steps", 0) + usage.get("steps", 0)
    db.execute("UPDATE tickets SET budget=? WHERE id=?", (db.dumps(b), tid))
    # circuit-breaker on hard budget (NFR-3)
    if b["spent_tokens"] > b.get("max_tokens", 1e12) or b["spent_cost"] > b.get("max_cost_usd", 1e9):
        _escalate(tid, "coordinator", "budget_exhausted",
                  "本工单预算已耗尽，暂停并升级给你决定是否追加预算。")


# ---------------------------------------------------------------------------
# gate pipeline orchestration (FR-6) with layered short-circuit
# ---------------------------------------------------------------------------
async def _run_gate_pipeline(tid: str) -> None:
    head = db.kv_get(f"head:{tid}", "")
    wt = str(WORKSPACES_DIR / tid)
    ids = [g for g in enabled_gates() if g != "human"]

    def _gemit(type, **kw):
        payload = kw.get("payload", {})
        if type == "gate":
            _set_pipeline(tid, payload["gate"], payload["state"])
        emit(type, ticket=tid, payload=payload)
        _touch(tid)

    # storage-agnostic driver runs the layered gates + streams events; a hard
    # `fail` short-circuits it. Ticket-side fail routing (re-dispatch/escalate)
    # stays here since it's coupled to ticket state.
    results = await gates.run_pipeline(tid, wt, head, _gemit, enabled=ids)
    failed = next((r for r in results if r.status == "fail"), None)
    if failed:
        fail_count = db.kv_get(f"gatefail:{tid}", 0) + 1
        db.kv_set(f"gatefail:{tid}", fail_count)
        post(tid, "agent", role="developer",
             html=f"{failed.gate_id} 门未通过，按路由回到开发修复。")
        if fail_count >= MAX_GATE_FAILURES:
            return _escalate(tid, "coordinator", "gate_stuck",
                             f"连续 {fail_count} 次验收门失败，升级给你。")
        # fix-task back to owner, then re-run (FR-6.6)
        await _dispatch(tid, failed.owner_on_fail if failed.owner_on_fail in
                        {"developer"} else "developer",
                        instruction=f"修复 {failed.gate_id} 门失败的问题。",
                        done_hint="已修复，重新申请验收。")
        return await _run_gate_pipeline(tid)  # re-run on new HEAD

    # all automated gates green -> human gate if a sensitive action is gated
    db.kv_set(f"gatefail:{tid}", 0)
    await _to_human_gate(tid)


async def _to_human_gate(tid: str) -> None:
    t = get_ticket(tid)
    # human gate only fires when a sensitive/irreversible action is in play (FR-7.2)
    needs_human = "human" in enabled_gates()
    if not needs_human:
        return await _complete(tid, deployed=False)
    _set_pipeline(tid, "human", "pending")
    post(tid, "card", card="gate")           # green evidence card
    post(tid, "card", card="approval")        # the decision card
    _set_status(tid, "awaiting_approval", needs=True)
    db.audit("gate", ticket_id=tid, detail={"gate": "human", "status": "pending"})


# ---------------------------------------------------------------------------
# human approval (FR-7)
# ---------------------------------------------------------------------------
def approve_deploy(tid: str) -> dict:
    db.audit("approval", ticket_id=tid, actor="human", detail={"deploy": "approved"})
    _resolve_last_card(tid, "approval", "approved")
    _set_pipeline(tid, "human", "running")
    _set_roster_state(tid, "devops", "working")
    _set_status(tid, "working", needs=False)
    events.spawn(_deploy(tid))
    return get_ticket(tid)


async def _deploy(tid: str) -> None:
    from .config import SIM_TICK_SEC
    _set_roster_state(tid, "devops", "working")
    post(tid, "agent", role="devops", html="灰度 10% 流量正常，继续放量…")
    await asyncio.sleep(SIM_TICK_SEC)
    _set_pipeline(tid, "human", "pass")
    post(tid, "agent", role="devops", html="已全量上线，问题解决。")
    await _complete(tid, deployed=True)


def reject_deploy(tid: str, reason: str = "先别上，补一份高并发压测再说。") -> dict:
    db.audit("approval", ticket_id=tid, actor="human", detail={"deploy": "rejected"})
    _resolve_last_card(tid, "approval", "rejected")
    _set_pipeline(tid, "human", "pending")
    post(tid, "human", html=reason)
    post(tid, "card", card="handoff", **{"from": "coordinator", "to": "developer",
         "note": "你要求补充后再申请上线"})
    _set_roster_state(tid, "developer", "working")
    _set_status(tid, "working", needs=False)
    return get_ticket(tid)


# ---------------------------------------------------------------------------
# escalation + resume (FR-5)
# ---------------------------------------------------------------------------
def _escalate(tid: str, role: str, problem_type: str, question: str) -> None:
    _set_roster_state(tid, role, "escalated")
    post(tid, "card", card="escalation", **{"from": role, "pt": problem_type,
         "q": question})
    _set_status(tid, "blocked", needs=True)
    db.audit("escalation", ticket_id=tid, actor=role,
             detail={"problem_type": problem_type, "to": "human"})
    emit("escalation", ticket=tid, agent=role, payload={"q": question, "pt": problem_type})


def answer_escalation(tid: str, answer: str) -> dict:
    db.audit("escalation", ticket_id=tid, actor="human", detail={"answer": answer})
    _resolve_last_card(tid, "escalation", "answered")
    post(tid, "human", html=answer)
    _set_status(tid, "working", needs=False)
    # resume: analyst confirms spec, hand to developer, run to gates
    t = get_ticket(tid)
    if any(r["role"] == "analyst" for r in t["roster"]):
        _set_roster_state(tid, "analyst", "done")
    events.spawn(_resume_after_answer(tid))
    return get_ticket(tid)


async def _resume_after_answer(tid: str) -> None:
    t = get_ticket(tid)
    if any(r["role"] == "analyst" for r in t["roster"]):
        post(tid, "agent", role="analyst", html="收到，规格已定，交给开发。")
        post(tid, "card", card="handoff", **{"from": "analyst", "to": "developer",
             "pt": "spec_ready", "note": "已按你的答复确定规格"})
    await _dispatch(tid, "developer",
                    instruction="按确认的规格实现并提交。",
                    done_hint="已提交代码，申请验收。")
    await _run_gate_pipeline(tid)


# ---------------------------------------------------------------------------
# completion + memory distillation (FR-8.6)
# ---------------------------------------------------------------------------
async def _complete(tid: str, *, deployed: bool) -> None:
    t = get_ticket(tid)
    for r in t["roster"]:
        _set_roster_state(tid, r["role"], "done")
    if any(r["role"] == "reporter" for r in t["roster"]):
        post(tid, "agent", role="reporter", html="监控已恢复正常，工单关闭。")
    post(tid, "card", card="closed")
    _set_status(tid, "done", needs=False)
    db.audit("decision", ticket_id=tid, actor="engine", detail={"closed": True})

    # distill a lesson into project memory (proposal -> human review)
    lesson = (f"工单 {tid}（{t['type']}）：{t['title']}。"
              f"处理结论已闭环{'并上线' if deployed else ''}。")
    memory.remember("projects", f"lesson-{tid}", lesson, ticket_id=tid, actor="agent")
    emit("system", ticket=tid, payload={"text": "工单已闭环，经验已提炼入记忆"})


def _resolve_last_card(tid: str, card: str, done: str) -> None:
    row = db.query_one(
        "SELECT id,payload FROM messages WHERE ticket_id=? AND kind='card' "
        "ORDER BY id DESC", (tid,))
    if not row:
        return
    payload = db.loads(row["payload"], {})
    if payload.get("card") == card:
        payload["done"] = done
        db.execute("UPDATE messages SET payload=? WHERE id=?",
                   (db.dumps(payload), row["id"]))


# ---------------------------------------------------------------------------
# delete ticket / message
# ---------------------------------------------------------------------------
def delete_ticket(tid: str) -> None:
    db.execute("DELETE FROM roster WHERE ticket_id=?", (tid,))
    db.execute("DELETE FROM messages WHERE ticket_id=?", (tid,))
    db.execute("DELETE FROM gate_results WHERE ticket_id=?", (tid,))
    db.execute("DELETE FROM tickets WHERE id=?", (tid,))
    db.audit("decision", actor="human", detail={"ticket_deleted": tid})
    for prefix in ("head:", "gatefail:", "stuck_flagged:", "cov_floor:", "lastact:"):
        db.execute("DELETE FROM kv WHERE key=?", (prefix + tid,))


def delete_message(tid: str, mid: int) -> None:
    msg = db.query_one("SELECT id FROM messages WHERE id=? AND ticket_id=?",
                       (mid, tid))
    if not msg:
        return
    db.execute("DELETE FROM messages WHERE id=?", (mid,))
    db.audit("decision", actor="human",
             detail={"message_deleted": mid, "ticket": tid})


# ---------------------------------------------------------------------------
# external stuck-detection (FR-5.2) — runs as a background loop
# ---------------------------------------------------------------------------
async def stuck_monitor() -> None:
    while True:
        await asyncio.sleep(20)
        try:
            for t in db.query("SELECT id,status FROM tickets WHERE status='working'"):
                last = db.kv_get(f"lastact:{t['id']}", 0)
                if last and (db.now() - last) > STUCK_NO_PROGRESS_SEC:
                    if db.kv_get(f"stuck_flagged:{t['id']}", False):
                        continue
                    db.kv_set(f"stuck_flagged:{t['id']}", True)
                    _escalate(t["id"], "coordinator", "no_progress",
                              f"该工单超过 {STUCK_NO_PROGRESS_SEC} 秒无进展，引擎判定卡死，升级给你。")
        except Exception as e:  # never let the monitor die
            db.audit("decision", actor="stuck_monitor", detail={"error": str(e)})
