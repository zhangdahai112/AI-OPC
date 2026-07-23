"""Channels = war-room group chats. A channel is a temporary collaboration session
anchored to one or more projects; deleting a channel does not delete projects or memory.
"""
from __future__ import annotations

from . import db
from .config import WORKSPACES_DIR

ROLES = ["coordinator", "analyst", "developer", "tester", "devops", "reporter"]
ROLE_CN = {
    "coordinator": "项目经理", "analyst": "需求分析", "developer": "开发",
    "tester": "测试", "devops": "运维", "reporter": "上报",
}


# ---- CRUD ---------------------------------------------------------------

def create_channel(*, name: str, project_ids: list[str] | None = None,
                   roster: list[str] | None = None) -> dict:
    """Create a new channel. If project_ids given, associate those projects."""
    cid = _next_id()
    db.execute(
        "INSERT INTO channels(id,name,status,created_at,updated_at) "
        "VALUES(?,?,?,?,?)",
        (cid, name, "active", db.now(), db.now()))
    for pid in (project_ids or []):
        db.execute(
            "INSERT INTO channel_projects(channel_id,project_id) VALUES(?,?)",
            (cid, pid))
    for role in (roster or ROLES):
        db.execute(
            "INSERT INTO channel_members(channel_id,role,state) VALUES(?,?,?)",
            (cid, role, "idle"))
    db.audit("decision", actor="human", detail={"channel_created": cid, "name": name})
    return get_channel(cid)


def _next_id() -> str:
    n = db.kv_get("channel_seq", 1)
    db.kv_set("channel_seq", n + 1)
    return f"C-{n:03d}"


def get_channel(cid: str) -> dict | None:
    row = db.query_one("SELECT * FROM channels WHERE id=?", (cid,))
    if not row:
        return None
    ch = dict(row)
    ch.setdefault("mode", "auto")
    ch["projects"] = [
        dict(r) for r in db.query(
            "SELECT project_id FROM channel_projects WHERE channel_id=?", (cid,))]
    ch["members"] = [
        dict(r) for r in db.query(
            "SELECT role,state FROM channel_members WHERE channel_id=?", (cid,))]
    ch["messages"] = _messages(cid)
    return ch


def list_channels() -> list[dict]:
    rows = db.query("SELECT id FROM channels ORDER BY created_at DESC")
    return [get_channel(r["id"]) for r in rows]


def update_channel(cid: str, name: str | None = None,
                  status: str | None = None, mode: str | None = None) -> dict:
    if name is not None:
        db.execute("UPDATE channels SET name=?, updated_at=? WHERE id=?",
                   (name, db.now(), cid))
    if status is not None:
        db.execute("UPDATE channels SET status=?, updated_at=? WHERE id=?",
                   (status, db.now(), cid))
    if mode in ("auto", "manual"):
        db.execute("UPDATE channels SET mode=?, updated_at=? WHERE id=?",
                   (mode, db.now(), cid))
        db.audit("decision", actor="human",
                 detail={"channel_mode": {"channel": cid, "mode": mode}})
    return get_channel(cid)


def get_mode(cid: str) -> str:
    """Relay mode for a channel: 'auto' (agents call each other) or 'manual'
    (every agent→agent handoff waits for human confirmation)."""
    row = db.query_one("SELECT mode FROM channels WHERE id=?", (cid,))
    return (row["mode"] if row and row["mode"] else "auto")


def add_project_to_channel(cid: str, project_id: str) -> None:
    db.execute(
        "INSERT OR IGNORE INTO channel_projects(channel_id,project_id) VALUES(?,?)",
        (cid, project_id))
    db.execute("UPDATE channels SET updated_at=? WHERE id=?", (db.now(), cid))


def remove_project_from_channel(cid: str, project_id: str) -> None:
    db.execute(
        "DELETE FROM channel_projects WHERE channel_id=? AND project_id=?",
        (cid, project_id))
    db.execute("UPDATE channels SET updated_at=? WHERE id=?", (db.now(), cid))


def list_channel_projects(cid: str) -> list[dict]:
    rows = db.query(
        "SELECT p.id,p.name,p.repo_url,p.branch,p.status,p.local_path "
        "FROM projects p "
        "JOIN channel_projects cp ON cp.project_id=p.id "
        "WHERE cp.channel_id=?", (cid,))
    return [dict(r) for r in rows]


def delete_channel(cid: str) -> None:
    """Delete channel and member/join records; leave projects & memory intact."""
    db.execute("DELETE FROM channel_members WHERE channel_id=?", (cid,))
    db.execute("DELETE FROM channel_projects WHERE channel_id=?", (cid,))
    db.execute("DELETE FROM channel_messages WHERE channel_id=?", (cid,))
    db.execute("DELETE FROM channels WHERE id=?", (cid,))


# ---- members -----------------------------------------------------------

def add_member(cid: str, role: str, state: str = "idle") -> None:
    db.execute(
        "INSERT OR IGNORE INTO channel_members(channel_id,role,state) VALUES(?,?,?)",
        (cid, role, state))


def remove_member(cid: str, role: str) -> None:
    db.execute(
        "DELETE FROM channel_members WHERE channel_id=? AND role=?", (cid, role))


def set_member_state(cid: str, role: str, state: str) -> None:
    db.execute(
        "UPDATE channel_members SET state=? WHERE channel_id=? AND role=?",
        (state, cid, role))


# ---- messages ----------------------------------------------------------

def _messages(cid: str) -> list[dict]:
    rows = db.query(
        "SELECT id,kind,role,payload,created_at FROM channel_messages "
        "WHERE channel_id=? ORDER BY id", (cid,))
    out = []
    for r in rows:
        m: dict = dict(r)
        m.update(db.loads(r["payload"], {}))
        m["t"] = _clock(r["created_at"])
        out.append(m)
    return out


def _clock(ts: float) -> str:
    import time
    return time.strftime("%H:%M", time.localtime(ts))


def post_message(cid: str, kind: str, *, role: str | None = None,
                **payload) -> None:
    db.execute(
        "INSERT INTO channel_messages(channel_id,kind,role,payload,created_at) "
        "VALUES(?,?,?,?,?)",
        (cid, kind, role, db.dumps(payload), db.now()))


def delete_message(cid: str, mid: int) -> bool:
    """Delete a single message from a channel. Returns True if a row was removed."""
    row = db.query_one(
        "SELECT id FROM channel_messages WHERE id=? AND channel_id=?", (mid, cid))
    if not row:
        return False
    db.execute("DELETE FROM channel_messages WHERE id=?", (mid,))
    db.audit("decision", actor="human",
             detail={"channel_message_deleted": mid, "channel": cid})
    return True


def clear_messages(cid: str) -> int:
    """Delete all messages in a channel. Returns the number of rows removed."""
    n = db.query_one(
        "SELECT COUNT(*) AS c FROM channel_messages WHERE channel_id=?", (cid,))
    db.execute("DELETE FROM channel_messages WHERE channel_id=?", (cid,))
    db.audit("decision", actor="human", detail={"channel_cleared": cid})
    return dict(n).get("c", 0) if n else 0


# ---- migrate from tickets -----------------------------------------------

def migrate_from_tickets() -> dict:
    """One-time migration: copy existing tickets → channels, channel_projects.
    Call once on startup if channels table is empty and tickets exist."""
    if db.query_one("SELECT COUNT(*) FROM channels"):
        return {"status": "already_has_channels"}
    rows = db.query("SELECT * FROM tickets")
    count = 0
    for t in rows:
        cid = f"C-{t['id']}"  # e.g. C-T-1042
        db.execute(
            "INSERT INTO channels(id,name,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?)",
            (cid, t["title"], t["status"], t["created_at"], t["updated_at"]))
        if t["project_id"]:
            db.execute(
                "INSERT OR IGNORE INTO channel_projects(channel_id,project_id) "
                "VALUES(?,?)", (cid, t["project_id"]))
        for role, state in db.query(
                "SELECT role,state FROM roster WHERE ticket_id=?", (t["id"],)):
            db.execute(
                "INSERT OR IGNORE INTO channel_members(channel_id,role,state) "
                "VALUES(?,?,?)", (cid, role, state))
        for mid in db.query(
                "SELECT id,kind,role,payload,created_at FROM messages "
                "WHERE ticket_id=?", (t["id"],)):
            db.execute(
                "INSERT INTO channel_messages(channel_id,kind,role,payload,created_at) "
                "VALUES(?,?,?,?,?)",
                (cid, mid["kind"], mid["role"], mid["payload"], mid["created_at"]))
        count += 1
    db.kv_set("channel_seq", 2000)
    return {"status": "migrated", "count": count}
