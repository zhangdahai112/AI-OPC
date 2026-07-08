"""Memory subsystem (PRD FR-8, arch 3.6).

Retrieval is local high-grade grep (ripgrep preferred, with platform fallback).
Organization *is* the index: a directory per scope, no vector store to maintain.
Scopes form a hard pre-filter and widen only when the narrow scope misses:

    channels/ < agents/ < projects/ < history/ < permanent/

Write governance: channel/agent scopes write directly; project/permanent are
privileged — agents only *propose*, a human approves, then it lands (FR-8.6),
which defends against memory poisoning. Code is the source of truth; memory only
holds what the repo doesn't (decisions, rationale, cross-ticket lessons).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import db
from .config import MEMORY_DIR, MEMORY_SCOPES

# Scopes that an agent may write without human review.
DIRECT_WRITE = {"channels", "agents"}


def _backend() -> str:
    """Capability-probe for the best available search tool (arch 3.6)."""
    if shutil.which("rg"):
        return "rg"
    if shutil.which("grep"):
        return "grep"
    if shutil.which("powershell"):
        return "powershell"
    return "python"  # last-resort in-process scan


def _scope_dirs(scope: str | None) -> list[Path]:
    if scope and scope in MEMORY_SCOPES:
        order = MEMORY_SCOPES[MEMORY_SCOPES.index(scope):]
    else:
        order = MEMORY_SCOPES
    return [MEMORY_DIR / s for s in order]


def search(pattern: str, scope: str | None = None, limit: int = 12) -> list[dict]:
    """Grep across scopes; returns path:line:text hits, narrowest scope first."""
    backend = _backend()
    hits: list[dict] = []
    for d in _scope_dirs(scope):
        if not d.exists():
            continue
        hits.extend(_run_search(backend, pattern, d))
        if len(hits) >= limit:
            break
    db.audit("memory", detail={"op": "recall", "pattern": pattern,
                               "scope": scope, "backend": backend, "hits": len(hits)})
    return hits[:limit]


def _run_search(backend: str, pattern: str, root: Path) -> list[dict]:
    """Try the preferred external tool; fall back to the in-process scanner if it
    errors *or* returns nothing. The fallback matters on Windows, where some grep
    builds mis-encode non-ASCII args and silently match zero lines — an empty
    external result must not mask a real hit. The Python scanner is UTF-8 correct
    and locale-independent, so it is authoritative when the external path is dry."""
    hits: list[dict] = []
    try:
        if backend == "rg":
            cmd = ["rg", "--no-heading", "--line-number", "-i", "-S",
                   pattern, str(root)]
        elif backend == "grep":
            cmd = ["grep", "-rIn", "-i", pattern, str(root)]
        elif backend == "powershell":
            cmd = ["powershell", "-NoProfile", "-Command",
                   f"Select-String -Path '{root}\\*' -Recurse -Pattern '{pattern}'"]
        else:
            cmd = None
        if cmd:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                                 encoding="utf-8", errors="replace")
            hits = _parse(out.stdout or "", root)
    except (subprocess.SubprocessError, OSError):
        hits = []
    return hits or _python_scan(pattern, root)


def _python_scan(pattern: str, root: Path) -> list[dict]:
    res, pl = [], pattern.lower()
    for f in root.rglob("*.md"):
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if pl in line.lower():
                    res.append({"scope": root.name, "source": _src(f),
                                "line": i, "text": line.strip()})
        except OSError:
            continue
    return res


def _parse(stdout: str, root: Path) -> list[dict]:
    out = []
    for raw in stdout.splitlines():
        # ripgrep/grep: path:line:text
        parts = raw.split(":", 2)
        if len(parts) < 3:
            continue
        path, line, text = parts
        try:
            ln = int(line)
        except ValueError:
            continue
        out.append({"scope": root.name, "source": _src(Path(path)),
                    "line": ln, "text": text.strip()})
    return out


def _src(f: Path) -> str:
    try:
        return f.stem
    except Exception:
        return str(f)


def lookup(key: str, scope: str = "permanent") -> str | None:
    """Exact primary-key fetch for config/permanent layer (FR-8.3) — no similarity."""
    f = MEMORY_DIR / scope / f"{key}.md"
    db.audit("memory", detail={"op": "lookup", "key": key, "scope": scope})
    return f.read_text(encoding="utf-8") if f.exists() else None


def remember(scope: str, title: str, body: str, *, ticket_id: str | None = None,
             actor: str = "agent") -> dict:
    """Write a memory. channel/agent land directly; project/permanent propose
    for human review (write governance, FR-8.6)."""
    if scope not in MEMORY_SCOPES:
        raise ValueError(f"unknown scope {scope}")

    if scope in DIRECT_WRITE or actor == "human":
        _write(scope, title, body)
        db.audit("memory", ticket_id=ticket_id, actor=actor,
                 detail={"op": "remember", "scope": scope, "title": title})
        return {"status": "written", "scope": scope, "title": title}

    # privileged: proposal pending human approval
    db.execute(
        "INSERT INTO memory_proposals(scope,title,body,ticket_id,status,created_at)"
        " VALUES(?,?,?,?,?,?)",
        (scope, title, body, ticket_id, "pending", db.now()),
    )
    db.audit("memory", ticket_id=ticket_id, actor=actor,
             detail={"op": "propose", "scope": scope, "title": title})
    return {"status": "proposed", "scope": scope, "title": title}


def _write(scope: str, title: str, body: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in title)[:60]
    f = MEMORY_DIR / scope / f"{safe}.md"
    f.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    return f


def approve_proposal(pid: int, approve: bool = True) -> dict:
    row = db.query_one("SELECT * FROM memory_proposals WHERE id=?", (pid,))
    if not row:
        raise ValueError("proposal not found")
    if approve:
        _write(row["scope"], row["title"], row["body"])
    db.execute("UPDATE memory_proposals SET status=? WHERE id=?",
               ("approved" if approve else "rejected", pid))
    return {"id": pid, "status": "approved" if approve else "rejected"}


def list_proposals(status: str = "pending") -> list[dict]:
    rows = db.query("SELECT * FROM memory_proposals WHERE status=? ORDER BY id DESC",
                    (status,))
    return [dict(r) for r in rows]
