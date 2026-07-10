"""Projects = code repo (cloned from a git URL) + requirement docs.

A project defines the world a war-room operates in. Each agent gets a
**project-bound permanent memory** (one markdown file per role) that the operator
edits in the config UI; it is injected into that agent's system prompt for every
turn on that project, so the agent's knowledge is stable and project-specific
(the user's "为每个 agent 配置永久不变的记忆，根据项目来").

Repo context (a shallow file tree + README excerpt) is built from the cloned
checkout and also fed to agents, so answers are grounded in the real codebase.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from . import db
from .config import AGENT_REPOS_DIR, MEMORY_DIR, WORKSPACES_DIR

ROLES = ["coordinator", "analyst", "developer", "tester", "devops", "reporter"]
ROLE_CN = {"coordinator": "项目经理", "analyst": "需求分析", "developer": "开发",
           "tester": "测试", "devops": "运维", "reporter": "上报"}


def _git(cwd: str, *args: str, timeout: int = 600) -> tuple[int, str]:
    p = subprocess.run(
        ["git", *args], cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# ---- CRUD ---------------------------------------------------------------
def create_project(*, name: str, repo_url: str = "", branch: str = "main",
                   docs: str = "") -> dict:
    pid = _next_id()
    path = str(WORKSPACES_DIR / pid)
    db.execute(
        "INSERT INTO projects(id,name,repo_url,branch,docs,status,local_path,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (pid, name, repo_url, branch, docs,
         "cloning" if repo_url else "ready", path, db.now(), db.now()))
    # seed per-agent memory files
    for role in ROLES:
        p = _mem_path(pid, role)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_default_memory(role, name), encoding="utf-8")
    db.audit("decision", actor="human", detail={"project_created": pid, "name": name})
    return get_project(pid)


def _next_id() -> str:
    n = db.kv_get("project_seq", 1)
    db.kv_set("project_seq", n + 1)
    return f"P-{n:03d}"


def clone_repo(pid: str) -> dict:
    """Clone (or refresh) the project's repo into its workspace. Synchronous —
    called in a thread by the API layer."""
    p = get_project(pid)
    if not p or not p["repo_url"]:
        return p or {}
    path = Path(p["local_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if (path / ".git").exists():
            rc, out = _git(str(path), "pull", "--ff-only")
        else:
            rc, out = _git(str(WORKSPACES_DIR), "clone", "--depth", "30",
                           "-b", p["branch"], p["repo_url"], pid)
            if rc != 0 and "Remote branch" in out:  # branch may not exist
                rc, out = _git(str(WORKSPACES_DIR), "clone", "--depth", "30",
                               p["repo_url"], pid)
        status = "ready" if rc == 0 else "error"
        db.execute("UPDATE projects SET status=?, clone_log=?, updated_at=? WHERE id=?",
                   (status, out[-2000:], db.now(), pid))
        db.audit("tool", actor="engine",
                 detail={"clone": pid, "status": status})
    except Exception as e:
        db.execute("UPDATE projects SET status=?, clone_log=?, updated_at=? WHERE id=?",
                   ("error", str(e), db.now(), pid))
    return get_project(pid)


# ---- per-agent independent git repos ------------------------------------
# Each agent role gets its OWN independent clone (its own .git / history / index),
# NOT a git worktree and NOT a submodule of the base checkout — agent repos are
# mutually independent and not subordinate to any trunk. They are seeded from the
# local base clone (fast, offline) but their ``origin`` points at the real
# upstream, so the internal base is just a seed, not a parent. Cloning is
# serialized so concurrent first-use of the same base doesn't lock-fight.
_WT_LOCK = threading.Lock()


def agent_repo_path(pid: str, role: str) -> Path:
    return AGENT_REPOS_DIR / pid / role


def ensure_agent_repo(pid: str, role: str) -> Path | None:
    """Return the agent's independent clone for (pid, role), creating it on first
    use. Returns ``None`` when the project has no git source to clone from."""
    p = get_project(pid)
    if not p or not p.get("local_path"):
        return None
    base = Path(p["local_path"])
    dest = agent_repo_path(pid, role)
    if (dest / ".git").exists():        # already cloned (fast path, no lock)
        return dest
    # seed source: the local base checkout if it's a git repo, else the remote.
    seed = str(base) if (base / ".git").exists() else (p.get("repo_url") or "")
    if not seed:
        return None                     # nothing to clone → can't isolate
    with _WT_LOCK:
        if (dest / ".git").exists():    # double-check under lock
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        # a full, independent clone (own object store) — not a worktree, not a
        # shared-.git; ``--no-hardlinks`` so nothing is physically shared either.
        rc, out = _git(str(dest.parent), "clone", "--no-hardlinks", seed, role)
        ok = rc == 0
        if ok:
            # decouple from the internal seed: point origin at the real upstream
            # (so this repo relates to the project's real remote, not our base).
            if p.get("repo_url"):
                _git(str(dest), "remote", "set-url", "origin", p["repo_url"])
            _git(str(dest), "checkout", "-B", f"agent/{role}")
            # give the repo its own git identity so agent commits succeed even
            # with no global identity, and self-identify which agent committed.
            _git(str(dest), "config", "user.name", f"agent-{role}")
            _git(str(dest), "config", "user.email", f"{role}@warroom.local")
        db.audit("tool", actor="engine",
                 detail={"agent_repo": pid, "role": role,
                         "status": "ready" if ok else "error",
                         **({} if ok else {"log": out[-400:]})})
        return dest if ok else None


def agent_root(pid: str, role: str | None = None) -> Path | None:
    """The directory an agent operates in: its own independent per-role clone when
    the project has a git source, otherwise the shared checkout (or None)."""
    p = get_project(pid)
    if not p or not p.get("local_path"):
        return None
    if role:
        repo = ensure_agent_repo(pid, role)
        if repo:
            return repo
    base = Path(p["local_path"])
    return base if base.exists() else None


def get_project(pid: str) -> dict | None:
    row = db.query_one("SELECT * FROM projects WHERE id=?", (pid,))
    return dict(row) if row else None


def list_projects() -> list[dict]:
    return [dict(r) for r in
            db.query("SELECT * FROM projects ORDER BY created_at DESC")]


def update_docs(pid: str, docs: str) -> dict:
    db.execute("UPDATE projects SET docs=?, updated_at=? WHERE id=?",
               (docs, db.now(), pid))
    return get_project(pid)


# ---- per-agent project memory ------------------------------------------
def _mem_path(pid: str, role: str) -> Path:
    return MEMORY_DIR / "projects" / pid / "agents" / f"{role}.md"


def get_agent_memory(pid: str, role: str) -> str:
    p = _mem_path(pid, role)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def set_agent_memory(pid: str, role: str, text: str) -> dict:
    p = _mem_path(pid, role)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    db.audit("memory", actor="human",
             detail={"project": pid, "role": role, "op": "set_agent_memory"})
    return {"project": pid, "role": role, "len": len(text)}


def all_agent_memory(pid: str) -> dict[str, str]:
    return {role: get_agent_memory(pid, role) for role in ROLES}


def _default_memory(role: str, project: str) -> str:
    base = {
        "coordinator": "你是项目经理。负责拆解工单、按职责分派给合适的 agent、汇总进度、处理升级。判断谁该回答某个问题。",
        "analyst": "你是需求分析。把模糊需求理清成明确、可验收的规格；发现歧义时主动提出澄清问题。",
        "developer": "你是开发工程师。阅读代码库、定位问题、给出具体可落地的实现方案与代码改动。",
        "tester": "你是测试工程师。设计测试用例、关注边界与回归、评估覆盖率。",
        "devops": "你是运维工程师。负责部署、灰度、回滚与线上稳定性。",
        "reporter": "你是上报 agent。盯监控与告警，把异常归类成工单。",
    }[role]
    return (f"# {ROLE_CN[role]} · 项目「{project}」永久记忆\n\n"
            f"{base}\n\n"
            "## 项目约定（在此补充本项目专属的规则、技术栈、坑、历史决策）\n"
            "- （示例）后端 Python / FastAPI，前端原生 JS。\n"
            "- （在配置页编辑本文件，对该 agent 在本项目的所有回答永久生效。）\n")


# Directories that are noise for grounding (deps, build output, vcs).
_IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__",
                "dist", "build", ".next", ".cache", "coverage", ".idea",
                ".vscode", "target", "vendor"}
_SRC_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".css", ".scss",
             ".html", ".json", ".md", ".yml", ".yaml", ".go", ".rs", ".java",
             # config / infra / script / other languages — these often hold the
             # real answer (nginx 防盗链、OSS 加密、密钥、SQL、Dockerfile 等)。
             ".env", ".sh", ".bash", ".conf", ".cfg", ".ini", ".toml", ".sql",
             ".proto", ".txt", ".xml", ".gradle", ".properties", ".php", ".rb",
             ".kt", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".swift", ".dart"}

# Important files that have no (or a non-source) extension but are worth searching.
_SEARCH_NAMES = {"dockerfile", "makefile", "nginx.conf", ".env", ".env.local",
                 ".env.production", ".env.development", "caddyfile", ".htaccess"}


def _is_searchable(f: Path) -> bool:
    """Whether grep should scan this file: known source/config extension, a
    known config filename, or a dot-env variant (.env, .env.prod, ...)."""
    name = f.name.lower()
    if f.suffix.lower() in _SRC_EXTS:
        return True
    if name in _SEARCH_NAMES:
        return True
    return name.startswith(".env")


def _walk_files(root: Path, max_files: int):
    """Yield repo files, skipping dependency/build dirs."""
    count = 0
    for f in sorted(root.rglob("*")):
        if any(part in _IGNORE_DIRS for part in f.parts):
            continue
        if f.is_file():
            yield f
            count += 1
            if count >= max_files:
                return


def _doc_leads(root: Path, cap: int = 40) -> list[str]:
    """Design docs / guides / CLAUDE.md worth reading FIRST — they often name the
    answer's entry point directly (e.g. docs/bng-encryption.html). Surfaced
    separately because the 80-file tree is alpha-truncated and usually cuts off
    before docs/ or deep util dirs. Ranked so design/security docs float up."""
    HINT = ("claude", "readme", "design", "arch", "prd", "spec", "guide",
            "encrypt", "crypto", "secur", "auth", "api", "doc", "开发", "方案", "设计")
    docs: list[tuple[int, str]] = []
    for f in _walk_files(root, max_files=3000):
        name = f.name.lower()
        if f.suffix.lower() not in (".md", ".html", ".htm", ".rst", ".txt") and name != "claude.md":
            continue
        rel = str(f.relative_to(root)).replace("\\", "/")
        low = rel.lower()
        score = sum(1 for h in HINT if h in low)
        docs.append((score, rel))
    docs.sort(key=lambda x: (-x[0], len(x[1]), x[1]))
    return [rel for _, rel in docs[:cap]]


def repo_context(pid: str, max_files: int = 80, role: str | None = None) -> str:
    """A compact file tree + doc leads + README excerpt, fed to agents so answers
    are grounded in the actual checkout (code is the source of truth). When
    ``role`` is given the agent sees its own worktree, so its prior writes are
    visible."""
    p = get_project(pid)
    if not p:
        return ""
    root = agent_root(pid, role) if role else Path(p["local_path"])
    if not root or not root.exists():
        return f"项目「{p['name']}」尚无本地代码（repo: {p['repo_url'] or '未配置'}）。"
    lines = [f"项目「{p['name']}」代码库（{p['repo_url'] or 'local'} @ {p['branch']}）文件树："]
    files = list(_walk_files(root, max_files))
    for f in files:
        lines.append(f"  {f.relative_to(root)}")
    if len(files) >= max_files:
        lines.append("  …（文件树已截断，可用 read_file 读取具体文件）")
    leads = _doc_leads(root)
    if leads:
        lines.append("\n📎 设计文档 / 说明（回答前优先扫这些找方案线索，"
                     "文件树可能已把它们截断）：")
        lines.extend(f"  {l}" for l in leads)
    for readme in ("README.md", "readme.md", "README.txt"):
        rp = root / readme
        if rp.exists():
            try:
                lines.append("\nREADME 摘录：\n" + rp.read_text(encoding="utf-8")[:1500])
            except OSError:
                pass
            break
    return "\n".join(lines)


def read_file(pid: str, rel_path: str, max_chars: int = 6000) -> str:
    """Read one file from a project's checkout (for agent grounding)."""
    p = get_project(pid)
    if not p:
        return ""
    root = Path(p["local_path"]).resolve()
    target = (root / rel_path).resolve()
    # path-traversal guard: must stay inside the checkout
    if root not in target.parents and target != root:
        return "（路径越界，拒绝读取）"
    if not target.is_file():
        return f"（文件不存在：{rel_path}）"
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"（读取失败：{e}）"
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…（已截断）"
    return text


_GREP_WALK_CAP = 6000  # max files walked before we admit the scan was truncated


def grep_repo(pid: str, pattern: str, max_hits: int = 40,
              root: Path | None = None) -> dict:
    """Search a checkout with a **regex** (case-insensitive). ``root`` overrides
    the base checkout so a tool call can search the agent's own worktree.

    Returns a dict so the caller can surface honest truncation signals instead of
    letting "no hits" be mistaken for "the thing doesn't exist":
        hits           list[{file,line,text}]
        files_scanned  how many files were actually grepped
        truncated_hits max_hits reached — there may be more matches
        truncated_files the file walk hit its cap — some dirs went unscanned
        regex_ok       False if pattern was an invalid regex (fell back to literal)
    """
    import re
    empty = {"hits": [], "files_scanned": 0, "truncated_hits": False,
             "truncated_files": False, "regex_ok": True}
    if root is None:
        p = get_project(pid)
        if not p:
            return empty
        root = Path(p["local_path"])
    if not root.exists():
        return empty

    regex_ok = True
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        rx = re.compile(re.escape(pattern), re.IGNORECASE)
        regex_ok = False

    hits: list[dict] = []
    walked = files_scanned = 0
    truncated_hits = False
    for f in _walk_files(root, max_files=_GREP_WALK_CAP):
        walked += 1
        if not _is_searchable(f):
            continue
        files_scanned += 1
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append({"file": str(f.relative_to(root)), "line": i,
                                 "text": line.strip()[:200]})
                    if len(hits) >= max_hits:
                        truncated_hits = True
                        break
        except OSError:
            continue
        if truncated_hits:
            break
    return {"hits": hits, "files_scanned": files_scanned,
            "truncated_hits": truncated_hits,
            "truncated_files": walked >= _GREP_WALK_CAP,
            "regex_ok": regex_ok}
