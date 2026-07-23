"""FastAPI surface: REST + WebSocket + static console.

The frontend renders entirely from these endpoints and the normalized event
stream over /ws, so the same UI works regardless of which executor ran (FR-12.4).
"""
from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (agents, channels, chat, connections, db, engine, events, llm,
               marketplace, memory, projects, seed, skill_store, skills)
from .config import WEB_DIR

app = FastAPI(title="Agent 作战群 · 控制台", version="0.1.0")


# ---- lifecycle ----------------------------------------------------------
@app.on_event("startup")
async def _startup() -> None:
    db.init_db()
    seed.seed_if_empty()
    channels.migrate_from_tickets()
    events.set_loop(asyncio.get_running_loop())
    asyncio.create_task(engine.stuck_monitor())


# ---- request models -----------------------------------------------------
class NewTicket(BaseModel):
    title: str
    type: str = "bug"
    description: str = ""
    repo: str = ""
    source: str = "human"
    project_id: str = ""
    roster: list[str] | None = None


class NewProject(BaseModel):
    name: str
    repo_url: str = ""
    branch: str = "main"
    docs: str = ""


class DocsUpdate(BaseModel):
    docs: str


class AgentMemory(BaseModel):
    role: str
    text: str


class ChatMsg(BaseModel):
    text: str


class ReviewReq(BaseModel):
    role: str = "developer"


class AnswerMsg(BaseModel):
    answer: str


class RejectMsg(BaseModel):
    reason: str = "先别上，补一份高并发压测再说。"


class RememberMsg(BaseModel):
    scope: str
    title: str
    body: str
    ticket_id: str | None = None


class AlertMsg(BaseModel):
    title: str
    fingerprint: str = ""
    type: str = "incident"


class TestProviderMsg(BaseModel):
    provider_id: str


class SkillGenMsg(BaseModel):
    skill_id: str
    project_id: str = ""
    role: str = ""
    brief: str = ""


class SkillInstallMsg(BaseModel):
    id: str
    source: str = ""


# ---- tickets ------------------------------------------------------------
@app.get("/api/tickets")
def api_tickets():
    return engine.list_tickets()


@app.get("/api/tickets/{tid}")
def api_ticket(tid: str):
    t = engine.get_ticket(tid)
    if not t:
        raise HTTPException(404, "ticket not found")
    return t


@app.post("/api/tickets")
def api_create(body: NewTicket):
    if body.type not in ("bug", "feature", "incident"):
        raise HTTPException(400, "type must be bug|feature|incident")
    return engine.create_ticket(title=body.title, ttype=body.type,
                                description=body.description, repo=body.repo,
                                source=body.source, project_id=body.project_id,
                                roster=body.roster)


@app.delete("/api/tickets/{tid}")
def api_delete_ticket(tid: str):
    if not engine.get_ticket(tid):
        raise HTTPException(404, "ticket not found")
    engine.delete_ticket(tid)
    return {"ok": True}


@app.delete("/api/tickets/{tid}/messages/{mid}")
def api_delete_message(tid: str, mid: int):
    engine.delete_message(tid, mid)
    return {"ok": True}


@app.post("/api/tickets/{tid}/messages")
def api_message(tid: str, body: ChatMsg):
    t = engine.get_ticket(tid)
    if not t:
        raise HTTPException(404, "ticket not found")
    # if blocked on an escalation, a human message answers it (FR-5.3)
    if t["status"] == "blocked":
        return engine.answer_escalation(tid, body.text)
    engine.post(tid, "human", html=body.text)
    # real agent reply, routed + streamed over SSE
    events.spawn(chat.human_turn(tid, body.text))
    return engine.get_ticket(tid)


@app.post("/api/tickets/{tid}/approve-contract")
def api_approve_contract(tid: str):
    if not engine.get_ticket(tid):
        raise HTTPException(404, "ticket not found")
    return engine.approve_contract(tid)


@app.post("/api/tickets/{tid}/approve")
def api_approve(tid: str):
    if not engine.get_ticket(tid):
        raise HTTPException(404, "ticket not found")
    return engine.approve_deploy(tid)


@app.post("/api/tickets/{tid}/reject")
def api_reject(tid: str, body: RejectMsg):
    if not engine.get_ticket(tid):
        raise HTTPException(404, "ticket not found")
    return engine.reject_deploy(tid, body.reason)


@app.post("/api/tickets/{tid}/answer")
def api_answer(tid: str, body: AnswerMsg):
    if not engine.get_ticket(tid):
        raise HTTPException(404, "ticket not found")
    return engine.answer_escalation(tid, body.answer)


# ---- alert webhook (FR-1.2 / FR-1.4 dedup) ------------------------------
@app.post("/api/webhook/alert")
def api_alert(body: AlertMsg):
    fp = body.fingerprint or body.title
    key = f"alert_fp:{fp}"
    existing = db.kv_get(key)
    if existing:
        db.audit("decision", actor="reporter",
                 detail={"alert_deduped": fp, "ticket": existing})
        return {"deduped": True, "ticket": existing}
    t = engine.create_ticket(title=body.title, ttype=body.type, source="reporter")
    db.kv_set(key, t["id"])
    return {"deduped": False, "ticket": t["id"]}


# ---- config -------------------------------------------------------------
def _sanitize_config(cfg: dict) -> dict:
    """Strip sensitive fields before returning config to the frontend."""
    import copy
    safe = copy.deepcopy(cfg)
    providers = safe.get("llm", {}).get("providers", [])
    for p in providers:
        p.pop("api_key", None)
    return safe


@app.get("/api/config")
def api_config():
    return _sanitize_config(db.kv_get("config", {}))


@app.put("/api/config")
def api_save_config(body: dict):
    existing = db.kv_get("config", {})
    # Preserve secrets the frontend never sees (e.g. LLM api_key)
    body = _merge_config(existing, body)
    db.kv_set("config", body)
    db.audit("decision", actor="human", detail={"config_saved": True})
    return {"ok": True}


def _merge_config(existing: dict, incoming: dict) -> dict:
    """Merge incoming config over existing, preserving secrets in llm.providers."""
    import copy
    merged = copy.deepcopy(incoming)
    old_providers = existing.get("llm", {}).get("providers", [])
    new_providers = merged.get("llm", {}).get("providers", [])
    old_keys = {p.get("id"): p.get("api_key") for p in old_providers}
    for p in new_providers:
        pid = p.get("id")
        if pid and not p.get("api_key") and old_keys.get(pid):
            p["api_key"] = old_keys[pid]
    return merged


# ---- llm api keys (stored in config DB, not in .env) --------------------
class LLMKeyMsg(BaseModel):
    provider_id: str
    api_key: str


@app.put("/api/llm/keys")
def api_set_llm_key(body: LLMKeyMsg):
    """Store an LLM provider's API key in the config DB (not .env)."""
    cfg = db.kv_get("config", {})
    providers = cfg.setdefault("llm", {}).setdefault("providers", [])
    for p in providers:
        if p.get("id") == body.provider_id:
            p["api_key"] = body.api_key
            db.kv_set("config", cfg)
            db.audit("decision", actor="human",
                     detail={"llm_key_updated": body.provider_id})
            return {"ok": True}
    raise HTTPException(404, "provider not found")


# ---- memory -------------------------------------------------------------
@app.get("/api/memory/recall")
def api_recall(q: str, scope: str | None = None):
    return memory.search(q, scope)


@app.post("/api/memory/remember")
def api_remember(body: RememberMsg):
    return memory.remember(body.scope, body.title, body.body,
                           ticket_id=body.ticket_id, actor="human")


@app.get("/api/memory/proposals")
def api_proposals(status: str = "pending"):
    return memory.list_proposals(status)


@app.post("/api/memory/proposals/{pid}/approve")
def api_approve_proposal(pid: int, approve: bool = True):
    return memory.approve_proposal(pid, approve)


# ---- audit (FR-11 observability) ----------------------------------------
@app.get("/api/audit")
def api_audit(ticket_id: str | None = None, limit: int = 100):
    if ticket_id:
        rows = db.query(
            "SELECT * FROM audit WHERE ticket_id=? ORDER BY id DESC LIMIT ?",
            (ticket_id, limit))
    else:
        rows = db.query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = db.loads(d["detail"], {})
        out.append(d)
    return out


@app.get("/api/metrics")
def api_metrics():
    """Platform self-metrics (FR-11.2)."""
    tickets = engine.list_tickets()
    by_status: dict[str, int] = {}
    for t in tickets:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
    gate_rows = db.query("SELECT status,COUNT(*) c FROM gate_results GROUP BY status")
    esc = db.query_one("SELECT COUNT(*) c FROM audit WHERE kind='escalation'")
    appr = db.query_one(
        "SELECT COUNT(*) c FROM audit WHERE kind='approval'")
    return {
        "tickets_total": len(tickets),
        "by_status": by_status,
        "gates": {r["status"]: r["c"] for r in gate_rows},
        "escalations": esc["c"] if esc else 0,
        "approvals": appr["c"] if appr else 0,
    }


# ---- projects (repo + docs + per-agent memory) --------------------------
@app.get("/api/projects")
def api_projects():
    return projects.list_projects()


@app.get("/api/projects/{pid}")
def api_project(pid: str):
    p = projects.get_project(pid)
    if not p:
        raise HTTPException(404, "project not found")
    p["memory"] = projects.all_agent_memory(pid)
    return p


@app.post("/api/projects")
async def api_create_project(body: NewProject):
    if body.repo_url:
        # 有关联仓库 → 创建项目记录 + 后台克隆
        p = projects.create_project(name=body.name, repo_url=body.repo_url,
                                    branch=body.branch, docs=body.docs)
        db.execute("UPDATE projects SET status='cloning' WHERE id=?", (p["id"],))

        def _clone_and_notify():
            result = projects.clone_repo(p["id"])
            events.emit("project_status", payload={"project_id": p["id"], **result})

        asyncio.get_running_loop().run_in_executor(None, _clone_and_notify)
    else:
        # 无仓库 → 创建本地沙箱（git init 过的目录，agent 可直接写文件）
        p = projects.create_local_project(name=body.name, docs=body.docs)
    return p


@app.put("/api/projects/{pid}/docs")
def api_project_docs(pid: str, body: DocsUpdate):
    if not projects.get_project(pid):
        raise HTTPException(404, "project not found")
    return projects.update_docs(pid, body.docs)


@app.post("/api/projects/{pid}/clone")
async def api_project_clone(pid: str):
    if not projects.get_project(pid):
        raise HTTPException(404, "project not found")
    db.execute("UPDATE projects SET status='cloning' WHERE id=?", (pid,))

    def _clone_and_notify():
        result = projects.clone_repo(pid)
        # Notify all subscribed clients that this project's status changed
        events.emit("project_status", payload={"project_id": pid, **result})

    asyncio.get_running_loop().run_in_executor(None, _clone_and_notify)
    return {"ok": True}


@app.get("/api/projects/{pid}/memory/{role}")
def api_get_memory(pid: str, role: str):
    return {"role": role, "text": projects.get_agent_memory(pid, role)}


@app.put("/api/projects/{pid}/memory")
def api_set_memory(pid: str, body: AgentMemory):
    if not projects.get_project(pid):
        raise HTTPException(404, "project not found")
    return projects.set_agent_memory(pid, body.role, body.text)


# ---- agent workspace browsing (visibility into per-role clones) ---------
@app.get("/api/projects/{pid}/workspace/{role}")
def api_workspace(pid: str, role: str):
    """File tree + git state of ``role``'s own working copy for this project —
    lets the operator see the code a given agent actually wrote (it lives in a
    per-role clone, not the base checkout)."""
    if not projects.get_project(pid):
        raise HTTPException(404, "project not found")
    return projects.workspace_tree(pid, role)


@app.get("/api/projects/{pid}/workspace/{role}/file")
def api_workspace_file(pid: str, role: str, path: str):
    if not projects.get_project(pid):
        raise HTTPException(404, "project not found")
    return {"path": path, "content": projects.workspace_file(pid, role, path)}


# ---- per-project Agent Manifests (Agent Studio, phase 1) ----------------
class AgentManifestPatch(BaseModel):
    manifest: dict


@app.get("/api/projects/{pid}/agents")
def api_project_agents(pid: str):
    """Resolved Agent Manifest for every role in this project."""
    if not projects.get_project(pid):
        raise HTTPException(404, "project not found")
    return {"agents": agents.list_for_project(pid)}


@app.get("/api/projects/{pid}/agents/{role}")
def api_project_agent(pid: str, role: str):
    if not projects.get_project(pid):
        raise HTTPException(404, "project not found")
    return agents.resolve(pid, role)


@app.put("/api/projects/{pid}/agents/{role}")
def api_set_project_agent(pid: str, role: str, body: AgentManifestPatch):
    """Save a partial override for (project, role); returns the resolved manifest."""
    if not projects.get_project(pid):
        raise HTTPException(404, "project not found")
    return agents.set_overrides(pid, role, body.manifest)


# ---- channels (group chat) -----------------------------------------------

class NewChannel(BaseModel):
    name: str
    project_ids: list[str] | None = None
    roster: list[str] | None = None


class ChannelUpdate(BaseModel):
    name: str | None = None
    status: str | None = None
    mode: str | None = None          # auto | manual (relay gating)


class ConfirmReq(BaseModel):
    choice: str                      # role key | "all" | "none"


class ChannelMember(BaseModel):
    role: str
    state: str | None = None


class AddProject(BaseModel):
    project_id: str


@app.get("/api/channels")
def api_channels():
    return channels.list_channels()


@app.post("/api/channels")
def api_create_channel(body: NewChannel):
    return channels.create_channel(
        name=body.name,
        project_ids=body.project_ids,
        roster=body.roster,
    )


@app.get("/api/channels/{cid}")
def api_channel(cid: str):
    ch = channels.get_channel(cid)
    if not ch:
        raise HTTPException(404, "channel not found")
    return ch


@app.put("/api/channels/{cid}")
def api_update_channel(cid: str, body: ChannelUpdate):
    ch = channels.update_channel(cid, name=body.name, status=body.status,
                                 mode=body.mode)
    if not ch:
        raise HTTPException(404, "channel not found")
    return ch


@app.delete("/api/channels/{cid}")
def api_delete_channel(cid: str):
    channels.delete_channel(cid)
    return {"ok": True}


@app.get("/api/channels/{cid}/messages")
def api_channel_messages(cid: str):
    ch = channels.get_channel(cid)
    if not ch:
        raise HTTPException(404, "channel not found")
    return ch["messages"]


@app.post("/api/channels/{cid}/messages")
def api_post_message(cid: str, body: ChatMsg):
    ch = channels.get_channel(cid)
    if not ch:
        raise HTTPException(404, "channel not found")
    from . import chat as chat_mod
    channels.post_message(cid, "human", html=body.text)
    events.spawn(chat_mod.human_turn(cid, body.text, is_channel=True))
    return channels.get_channel(cid)


@app.post("/api/channels/{cid}/confirm")
def api_channel_confirm(cid: str, body: ConfirmReq):
    """Manual-mode: human answers a pending handoff confirm card. `choice` is a role
    key to run, 'all' to run every pending option, or 'none' to stop the chain."""
    if not channels.get_channel(cid):
        raise HTTPException(404, "channel not found")
    from . import chat as chat_mod
    events.spawn(chat_mod.resume_handoff(cid, body.choice))
    return {"ok": True, "choice": body.choice}


@app.post("/api/channels/{cid}/review")
def api_channel_review(cid: str, body: ReviewReq | None = None):
    """Kick off a real acceptance-gate run against `role`'s work in this channel.
    Returns immediately; gate progress + the result card stream over /api/events."""
    if not channels.get_channel(cid):
        raise HTTPException(404, "channel not found")
    from . import chat as chat_mod
    role = (body.role if body else "developer") or "developer"
    events.spawn(chat_mod.request_review(cid, role))
    return {"ok": True, "role": role}


@app.delete("/api/channels/{cid}/messages")
def api_clear_channel_messages(cid: str):
    if not channels.get_channel(cid):
        raise HTTPException(404, "channel not found")
    removed = channels.clear_messages(cid)
    return {"ok": True, "removed": removed}


@app.delete("/api/channels/{cid}/messages/{mid}")
def api_delete_channel_message(cid: str, mid: int):
    if not channels.get_channel(cid):
        raise HTTPException(404, "channel not found")
    channels.delete_message(cid, mid)
    return {"ok": True}


@app.get("/api/channels/{cid}/projects")
def api_channel_projects(cid: str):
    return channels.list_channel_projects(cid)


@app.post("/api/channels/{cid}/projects")
def api_add_project(cid: str, body: AddProject):
    channels.add_project_to_channel(cid, body.project_id)
    return channels.list_channel_projects(cid)


@app.delete("/api/channels/{cid}/projects/{pid}")
def api_remove_project(cid: str, pid: str):
    channels.remove_project_from_channel(cid, pid)
    return {"ok": True}


@app.get("/api/channels/{cid}/members")
def api_channel_members(cid: str):
    ch = channels.get_channel(cid)
    if not ch:
        raise HTTPException(404, "channel not found")
    return ch["members"]


@app.post("/api/channels/{cid}/members")
def api_add_member(cid: str, body: ChannelMember):
    channels.add_member(cid, body.role, body.state or "idle")
    return channels.get_channel(cid)


@app.delete("/api/channels/{cid}/members/{role}")
def api_remove_member(cid: str, role: str):
    channels.remove_member(cid, role)
    return {"ok": True}


@app.get("/api/projects/{pid}/channels")
def api_project_channels(pid: str):
    """List all channels associated with a project."""
    all_chs = channels.list_channels()
    return [c for c in all_chs if any(p.get("project_id") == pid for p in c.get("projects", []))]


# ---- llm status & providers ---------------------------------------------
@app.get("/api/llm")
def api_llm():
    """Return the status of the currently active LLM provider."""
    active = llm.get_active_provider()
    if active:
        s = llm.provider_status(active)
        return {"available": s["key_configured"], **s}
    return {"available": False, "error": "no provider configured"}


@app.get("/api/llm/providers")
def api_llm_providers():
    """Return status for every configured provider (no API keys exposed)."""
    return {"providers": llm.provider_status_all()}


@app.post("/api/llm/test")
async def api_llm_test(body: TestProviderMsg):
    """Send a short ping to verify a provider's connectivity."""
    return await llm.test_provider(body.provider_id)


# ---- generation skills (document & config drafting) ---------------------
@app.get("/api/skills")
def api_skills():
    """List built-in generation skills (metadata only, no prompts)."""
    return {"skills": skills.list_skills()}


@app.post("/api/skills/generate")
async def api_skill_generate(body: SkillGenMsg):
    """Draft a document/config with the chosen skill, grounded in the project."""
    if not skills.get_skill(body.skill_id):
        raise HTTPException(404, "unknown skill")
    return await skills.generate(skill_id=body.skill_id, project_id=body.project_id,
                                 role=body.role, brief=body.brief)


# ---- connections (credentials for MCP / egress, key-free responses) -----
@app.get("/api/connections")
def api_connections():
    """key-free status list — never exposes plaintext secrets."""
    return {"connections": connections.list_connections()}


@app.post("/api/connections")
def api_save_connection(body: dict):
    """Create/update a connection. Secrets stay in memory; DB keeps only refs."""
    return connections.upsert_connection(body)


@app.delete("/api/connections/{cid}")
def api_delete_connection(cid: str):
    connections.delete_connection(cid)
    return {"ok": True}


@app.post("/api/connections/{cid}/test")
async def api_test_connection(cid: str):
    """Liveness probe for a connection; never raises."""
    return await connections.verify(cid)


# ---- skill store (MCP market + Agent Skills) ----------------------------
@app.get("/api/skills/store")
def api_skill_store(q: str = "", source: str = ""):
    return {"skills": skill_store.search(q, source)}


@app.post("/api/skills/store/install")
async def api_skill_install(body: SkillInstallMsg):
    try:
        return await skill_store.install(body.id, body.source)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/skills/installed")
def api_skills_installed():
    return {"skills": skill_store.list_installed()}


@app.delete("/api/skills/installed/{skill_id}")
def api_uninstall_skill(skill_id: str):
    skill_store.uninstall(skill_id)
    return {"ok": True}


# ---- marketplace (dedicated market page: real MCP + skills) --------------
class InstallCardMsg(BaseModel):
    card: dict


class IdMsg(BaseModel):
    id: str


@app.get("/api/market/mcp")
async def api_market_mcp(q: str = ""):
    """Live MCP connector listings — official MCP Registry + Smithery (if key)."""
    return await marketplace.search_mcp(q)


@app.get("/api/market/skills")
def api_market_skills(q: str = ""):
    """Skills market listings."""
    return marketplace.search_skills(q)


@app.post("/api/market/mcp/install")
def api_market_install_mcp(body: InstallCardMsg):
    """One-click install an MCP server card into the platform catalog."""
    try:
        return marketplace.install_mcp(body.card)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/market/mcp/uninstall")
def api_market_uninstall_mcp(body: IdMsg):
    marketplace.uninstall_mcp(body.id)
    return {"ok": True}


@app.get("/api/market/installed")
def api_market_installed():
    """Platform-level installed catalog (MCP + skills) — for the agent picker."""
    return marketplace.installed()


# ---- SSE: normalized event stream (incl. token deltas) ------------------
@app.get("/api/events")
async def api_events(request: Request):
    q = events.subscribe()

    async def gen():
        try:
            yield "retry: 1500\n\n"
            yield f"data: {json.dumps({'type': 'hello'})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            events.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---- websocket: normalized event stream ---------------------------------
@app.websocket("/ws")
async def ws(socket: WebSocket):
    await socket.accept()
    q = events.subscribe()
    try:
        await socket.send_json({"type": "hello", "payload": {"msg": "connected"}})
        while True:
            event = await q.get()
            await socket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        events.unsubscribe(q)


# ---- static console -----------------------------------------------------
@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True}


# mount assets (served from /assets/*) — no-cache so edits always reload in dev
class NoCacheStatic(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp


app.mount("/assets", NoCacheStatic(directory=str(WEB_DIR / "assets")), name="assets")
