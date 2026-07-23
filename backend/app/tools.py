"""Real agent tools — file/exec operations bound to a channel's project worktree.

Until now agents only produced text; the platform's "tools" UI was never wired to
anything real (PRD FR-12.x).  This module gives each agent an honest tool surface:

    list_dir / read_file / grep      → read-only grounding in the actual checkout
    write_file                       → create or overwrite a file in the worktree
    run_command                      → run a shell command inside the worktree

Every operation is confined to the project's checkout directory (path-traversal is
rejected), commands run with a timeout and truncated output, and a small denylist
blocks catastrophic commands.  Each call is surfaced to the operator as a
``tool_call`` event and persisted onto the agent message, so the human can see
exactly what every member did.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import projects

# ── limits ────────────────────────────────────────────────────────────────
_READ_MAX = 8000          # chars returned by read_file
_CMD_TIMEOUT = 60         # seconds per run_command
_CMD_OUT_MAX = 6000       # chars of command output kept
_GREP_MAX = 40
_LIST_MAX = 200

# commands that could nuke the host or escape the sandbox — refused outright.
_DANGER = (
    "rm -rf /", "rm -rf ~", ":(){", "mkfs", "dd if=", "shutdown", "reboot",
    "> /dev/sd", "chmod -r 000", "git push", "curl", "wget", "ssh ",
)

_REPO_MAP_MAX_FILES = 200
_REPO_MAP_TREE_MAX = 300
# repo_map is an overview, not a source of truth — cap its size so a ~35KB map
# doesn't get re-sent across every tool-loop iteration. Details come from grep/read.
_REPO_MAP_MAX_CHARS = 12000


# ── tool schema (Anthropic tool-use format) ────────────────────────────────
def tool_specs(allow: set[str] | None = None) -> list[dict[str, Any]]:
    """JSON schema for the tools, in Anthropic ``tools=`` format.

    ``allow`` is the least-privilege gate from the agent's manifest: when given,
    only tools whose name is in the set are exposed to the model.
    """
    specs = [
        {
            "name": "list_dir",
            "description": "列出项目仓库里某个目录的文件与子目录。path 相对仓库根，默认根目录。",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "相对仓库根的目录，默认 '.'"}},
            },
        },
        {
            "name": "read_file",
            "description": ("读取项目仓库里一个文件的内容（相对仓库根的路径）。单次最多返回约 8000 字符，"
                            "过长会截断；用 offset（字符偏移）继续读取后续部分。"),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对仓库根的文件路径"},
                    "offset": {"type": "integer",
                               "description": "从第几个字符开始读，默认 0；文件被截断时用它读后续内容"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "grep",
            "description": ("在项目仓库里用正则搜索（忽略大小写），返回命中的文件、行号、行内容。"
                            "支持用 | 一次搜一族同义词，例如 "
                            "`encrypt|decrypt|加密|解密|AES|cipher|secret|hmac|secure_link`。"
                            "覆盖源码与常见配置/脚本文件（.env、nginx.conf、Dockerfile、.sql 等）。"
                            "多项目群会搜索所有关联项目。命中或文件过多时会明确提示已截断——"
                            "「未命中」只代表在已扫描范围内没找到，不等于不存在。"),
            "input_schema": {
                "type": "object",
                "properties": {"pattern": {"type": "string",
                                           "description": "正则表达式；可用 | 表达多个关键词"}},
                "required": ["pattern"],
            },
        },
        {
            "name": "write_file",
            "description": "在项目仓库里写入/覆盖一个文件（相对仓库根的路径）。会创建缺失的父目录。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对仓库根的文件路径"},
                    "content": {"type": "string", "description": "完整的新文件内容"},
                },
                "required": ["path", "content"],
            },
        },
        {
            "name": "run_command",
            "description": ("在项目仓库目录里执行一条 shell 命令并返回输出（含 stdout/stderr）。"
                            "用于跑测试、git status/diff/add/commit、构建等。超时 60s。"),
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "要执行的 shell 命令"}},
                "required": ["command"],
            },
        },
        {
            "name": "repo_map",
            "description": "生成项目仓库的结构化地图：目录树、关键符号（函数/类）、入口点、依赖信息、构建命令、git 状态。在首次接触一个仓库时优先调用此工具了解全貌。",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "find_symbol",
            "description": ("定位一个符号（函数/类/变量）的**定义位置**和所有**调用/引用点**，"
                            "用于顺着调用链一步步追代码（跳转到定义 + 查找调用方）——"
                            "比 grep 更适合追踪「入口→被调函数→再被调函数」的链路，"
                            "尤其是异步/队列/序列化这种间接调用。"),
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string",
                                        "description": "要定位的符号精确名（函数/类/变量名）"}},
                "required": ["name"],
            },
        },
        {
            "name": "create_project",
            "description": ("创建一个全新的本地项目并绑定到当前群：会建好一个**可写的 git 工作区**，"
                            "之后就能用 write_file 往里真正落地代码。当人类要求「新建/搭建一个项目」"
                            "而当前群里还没有对应的可写项目时，先调用它，再开始写文件。"),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "项目名称"},
                    "description": {"type": "string", "description": "一句话说明项目要做什么（可选）"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "explore",
            "description": ("派发一个只读探查子代理，横扫整个仓库（含前端/后端/docs）调查一个问题，"
                            "自动 repo_map+grep+read 后返回带文件定位的综合结论。"
                            "适合「某功能怎么实现 / 有没有 X / 加解密怎么做」这类需要横扫多文件、"
                            "跨前后端的问题——你只需给出清晰的问题，不用自己一个个翻文件。"),
            "input_schema": {
                "type": "object",
                "properties": {"question": {"type": "string",
                                            "description": "要调查的问题，越具体越好"}},
                "required": ["question"],
            },
        },
    ]
    if allow is not None:
        specs = [s for s in specs if s["name"] in allow]
    return specs


# ── execution context ──────────────────────────────────────────────────────
@dataclass
class ToolContext:
    """Tools are confined to these project checkouts. ``default_pid`` is used when
    a call does not name a project (the common single-project channel case)."""
    roots: dict[str, Path] = field(default_factory=dict)   # pid -> resolved root
    default_pid: str | None = None

    @classmethod
    def for_projects(cls, project_ids: list[str]) -> "ToolContext":
        roots: dict[str, Path] = {}
        for pid in project_ids:
            p = projects.get_project(pid)
            if not p or not p.get("local_path"):
                continue
            root = Path(p["local_path"]).resolve()
            if root.exists():
                roots[pid] = root
        default = next(iter(roots), None)
        return cls(roots=roots, default_pid=default)

    @classmethod
    def for_agent(cls, project_ids: list[str], role: str) -> "ToolContext":
        """All agents share the **base checkout** (not per-role clones) so every
        role's writes are immediately visible to the entire team. Cross-role
        propagation is baked in — no sync needed.

        Git branch isolation is still available per-role, but the working tree
        is shared. Falls back to the shared checkout when there is no git repo."""
        roots: dict[str, Path] = {}
        for pid in project_ids:
            p = projects.get_project(pid)
            if not p or not p.get("local_path"):
                continue
            root = Path(p["local_path"]).resolve()
            if root.exists():
                roots[pid] = root
        default = next(iter(roots), None)
        return cls(roots=roots, default_pid=default)

    @property
    def has_repo(self) -> bool:
        return bool(self.roots)

    def _root(self) -> Path | None:
        if self.default_pid and self.default_pid in self.roots:
            return self.roots[self.default_pid]
        return next(iter(self.roots.values()), None)

    def _resolve(self, rel_path: str) -> Path | None:
        """Resolve a repo-relative path, rejecting anything outside the checkout."""
        root = self._root()
        if not root:
            return None
        target = (root / (rel_path or ".")).resolve()
        if target != root and root not in target.parents:
            return None
        return target


# ── dispatch ────────────────────────────────────────────────────────────────
def execute(name: str, args: dict[str, Any], ctx: ToolContext) -> str:
    """Run one tool call. Always returns a string (never raises) so the tool
    result can be fed straight back to the model."""
    try:
        if name == "list_dir":
            return _list_dir(args.get("path", "."), ctx)
        if name == "read_file":
            return _read_file(args.get("path", ""), ctx, _int(args.get("offset")))
        if name == "grep":
            return _grep(args.get("pattern", ""), ctx)
        if name == "find_symbol":
            return _find_symbol(args.get("name", ""), ctx)
        if name == "repo_map":
            return _repo_map(ctx)
        if name == "write_file":
            return _write_file(args.get("path", ""), args.get("content", ""), ctx)
        if name == "run_command":
            return _run_command(args.get("command", ""), ctx)
        if name == "explore":
            # explore is async (spawns a sub-agent); it is dispatched in chat.on_tool,
            # never through this synchronous path. Reaching here means mis-wiring.
            return "（explore 需由上层异步处理，未在此同步执行）"
        return f"（未知工具：{name}）"
    except Exception as e:  # never let a tool crash the agent turn
        return f"（工具 {name} 执行异常：{type(e).__name__}: {e}）"


def summarize(name: str, args: dict[str, Any]) -> str:
    """A short human-readable label for a tool call (shown live in the UI)."""
    if name == "read_file":
        return args.get("path", "")
    if name == "list_dir":
        return args.get("path", ".") or "."
    if name == "grep":
        return repr(args.get("pattern", ""))
    if name == "write_file":
        return args.get("path", "")
    if name == "run_command":
        return args.get("command", "")
    if name == "repo_map":
        return "生成仓库地图"
    if name == "find_symbol":
        return args.get("name", "")
    if name == "create_project":
        return args.get("name", "")
    if name == "explore":
        return (args.get("question", "") or "")[:60]
    return ""


# ── individual tools ────────────────────────────────────────────────────────
def _list_dir(rel: str, ctx: ToolContext) -> str:
    target = ctx._resolve(rel)
    if target is None:
        return "（无可用项目仓库，或路径越界）"
    if not target.exists():
        return f"（目录不存在：{rel}）"
    if not target.is_dir():
        return f"（不是目录：{rel}）"
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name)):
        if child.name in projects._IGNORE_DIRS:
            continue
        entries.append(f"{child.name}/" if child.is_dir() else child.name)
        if len(entries) >= _LIST_MAX:
            entries.append("…（已截断）")
            break
    root = ctx._root()
    relshown = target.relative_to(root) if root else rel
    return f"{relshown}/ 下的条目：\n" + "\n".join(entries) if entries else f"{relshown}/ 为空"


def _int(v: Any) -> int:
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


def _read_file(rel: str, ctx: ToolContext, offset: int = 0) -> str:
    if not rel:
        return "（缺少 path 参数）"
    target = ctx._resolve(rel)
    if target is None:
        return "（无可用项目仓库，或路径越界）"
    if not target.is_file():
        return f"（文件不存在：{rel}）"
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"（读取失败：{e}）"
    total = len(text)
    window = text[offset:offset + _READ_MAX]
    end = offset + len(window)
    if end < total:
        window += (f"\n…（文件在此截断：共 {total} 字符，本次显示 {offset}~{end}；"
                   f"如需后续内容，用 read_file(path='{rel}', offset={end}) 继续读取）")
    elif offset:
        window += f"\n…（已读到文件末尾，共 {total} 字符）"
    return window


def _grep(pattern: str, ctx: ToolContext) -> str:
    if not pattern:
        return "（缺少 pattern 参数）"
    if not ctx.roots:
        return "（无可用项目仓库）"
    # Search every project bound to the channel (not just the default one) — the
    # answer may live in a sibling repo (e.g. an image-serving service). Each
    # agent searches its own per-role worktree so its fresh writes are visible.
    multi = len(ctx.roots) > 1
    blocks: list[str] = []
    for pid, root in ctx.roots.items():
        res = projects.grep_repo(pid, pattern, max_hits=_GREP_MAX, root=root)
        head = f"【项目 {pid}】" if multi else ""
        notes: list[str] = []
        if not res["regex_ok"]:
            notes.append("注：该 pattern 作为正则无效，已按纯文本匹配。")
        if not res["hits"]:
            tail = (" " + " ".join(notes)) if notes else ""
            blocks.append(f"{head}未命中「{pattern}」"
                          f"（已扫描 {res['files_scanned']} 个文件）。{tail}")
            continue
        lines = [f"{h['file']}:{h['line']}: {h['text']}" for h in res["hits"]]
        if res["truncated_hits"]:
            notes.append(f"⚠️ 命中已达上限 {_GREP_MAX} 条，可能还有更多——请缩小或精确化查询后再搜。")
        if res["truncated_files"]:
            notes.append("⚠️ 仓库文件过多、扫描已截断，部分目录可能未覆盖——建议按子目录分别搜索。")
        body = f"{head}命中 {len(res['hits'])} 处（扫描 {res['files_scanned']} 个文件）：\n" + "\n".join(lines)
        if notes:
            body += "\n" + "\n".join(notes)
        blocks.append(body)
    return "\n\n".join(blocks)


def _find_symbol(name: str, ctx: ToolContext) -> str:
    if not (name or "").strip():
        return "（缺少 name 参数）"
    if not ctx.roots:
        return "（无可用项目仓库）"
    multi = len(ctx.roots) > 1
    blocks: list[str] = []
    for pid, root in ctx.roots.items():
        res = projects.find_symbol_repo(pid, name, max_hits=_GREP_MAX, root=root)
        head = f"【项目 {pid}】" if multi else ""
        if not res["defs"] and not res["calls"]:
            blocks.append(f"{head}未找到符号「{name}」的定义或调用"
                          f"（已扫描 {res['files_scanned']} 个文件）。可能是外部库、"
                          f"动态生成，或名字不对。")
            continue
        parts = [f"{head}符号「{name}」（扫描 {res['files_scanned']} 个文件）："]
        if res["defs"]:
            parts.append(f"● 定义（{len(res['defs'])} 处）：")
            parts += [f"  {d['file']}:{d['line']}: {d['text']}" for d in res["defs"]]
        else:
            parts.append("● 定义：未找到（可能是外部库/属性/动态定义）")
        if res["calls"]:
            parts.append(f"● 调用/引用（{len(res['calls'])} 处，顺着这些点往上/下游追）：")
            parts += [f"  {c['file']}:{c['line']}: {c['text']}" for c in res["calls"]]
        if res["truncated"]:
            parts.append("⚠️ 结果已达上限、可能还有更多——按目录缩小或直接 read 定义文件。")
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)


def _repo_map(ctx: ToolContext) -> str:
    """Generate a structured codebase map: tree + symbols + entry points + deps + git status."""
    import re, json
    root = ctx._root()
    if root is None:
        return "（无可用项目仓库）"
    pid = ctx.default_pid
    p = projects.get_project(pid) if pid else {}
    lines: list[str] = []
    lines.append(f"# 仓库地图: {p.get('name', root.name)}")
    if p.get("repo_url"):
        lines.append(f"远程: {p['repo_url']}  |  分支: {p.get('branch', '?')}")
    lines.append("")

    # 1. Directory tree
    lines.append("## 目录树")
    lines.extend(_build_tree(root))
    lines.append("")

    # 2. Entry points
    lines.append("## 入口点")
    eps = _find_entry_points(root)
    lines.extend(eps if eps else ["（未识别到标准入口文件）"])
    lines.append("")

    # 3. Dependencies
    lines.append("## 依赖信息")
    deps = _extract_deps(root)
    lines.extend(deps if deps else ["（未找到依赖清单文件）"])
    lines.append("")

    # 4. Build / test commands
    lines.append("## 构建 / 测试命令")
    cmds = _extract_commands(root)
    lines.extend(cmds if cmds else ["（未找到构建或测试脚本）"])
    lines.append("")

    # 5. Key symbols (capped)
    lines.append("## 关键符号")
    syms = _extract_symbols(root)
    lines.extend(syms)
    lines.append("")

    # 6. Git status
    lines.append("## Git 状态")
    git_lines = _git_status(root)
    lines.extend(git_lines)

    out = "\n".join(lines)
    if len(out) > _REPO_MAP_MAX_CHARS:
        out = (out[:_REPO_MAP_MAX_CHARS]
               + "\n…（仓库地图已截断；用 list_dir 看具体目录、grep 定位符号）")
    return out


# ── repo_map helpers ──────────────────────────────────────────────────────

def _build_tree(root: Path) -> list[str]:
    """Indented directory tree from _walk_files output."""
    entries: list[tuple[str, bool]] = []  # (rel_path, is_dir)
    seen_dirs: set[str] = set()
    for f in projects._walk_files(root, _REPO_MAP_TREE_MAX):
        rel = str(f.relative_to(root)).replace("\\", "/")
        parent = str(f.parent.relative_to(root)).replace("\\", "/") if f.parent != root else ""
        # ensure all parent dirs are recorded
        if parent and parent != ".":
            parts = parent.split("/")
            for i in range(len(parts)):
                d = "/".join(parts[:i+1])
                if d not in seen_dirs:
                    seen_dirs.add(d)
                    entries.append((d, True))
        entries.append((rel, False))
    if len(entries) >= _REPO_MAP_TREE_MAX:
        entries.append(("…（已截断）", False))
    # format with indentation
    out: list[str] = []
    for path, is_dir in entries:
        depth = path.count("/")
        name = path.rsplit("/", 1)[-1] if "/" in path else path
        prefix = "  " * depth + ("📁 " if is_dir else "📄 ")
        out.append(f"{prefix}{name}")
    return out


def _find_entry_points(root: Path) -> list[str]:
    """Check for common entry-point files."""
    checks = [
        ("main.go", "Go 入口"), ("app.py", "Python 入口"), ("main.py", "Python 入口"),
        ("run.py", "Python 入口"), ("index.ts", "TS 入口"), ("index.tsx", "React 入口"),
        ("index.js", "JS 入口"), ("index.jsx", "React 入口"), ("main.rs", "Rust 入口"),
        ("lib.rs", "Rust 库根"), ("Dockerfile", "容器入口"),
        ("docker-compose.yml", "容器编排"), ("docker-compose.yaml", "容器编排"),
        ("Makefile", "构建入口"), ("package.json", "Node 项目根"),
        ("setup.py", "Python 项目根"), ("pyproject.toml", "Python 项目根"),
        ("go.mod", "Go 项目根"), ("Cargo.toml", "Rust 项目根"),
    ]
    found: list[str] = []
    for filename, label in checks:
        if (root / filename).exists():
            found.append(f"- {filename} ({label})")
    return found


def _extract_deps(root: Path) -> list[str]:
    """Parse dependency manifests for top-level info."""
    import json
    out: list[str] = []
    # package.json
    pj = root / "package.json"
    if pj.exists():
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
            name = data.get("name", "?")
            ver = data.get("version", "?")
            deps = len(data.get("dependencies", {}))
            dev = len(data.get("devDependencies", {}))
            out.append(f"- package.json: {name}@{ver}, {deps} 生产依赖, {dev} 开发依赖")
        except Exception:
            out.append("- package.json（解析失败）")
    # requirements.txt
    rt = root / "requirements.txt"
    if rt.exists():
        try:
            count = sum(1 for l in rt.read_text(encoding="utf-8").splitlines()
                       if l.strip() and not l.strip().startswith("#"))
            out.append(f"- requirements.txt: {count} 个依赖")
        except Exception:
            pass
    # go.mod
    gm = root / "go.mod"
    if gm.exists():
        try:
            text = gm.read_text(encoding="utf-8")
            mod = ""
            m = __import__("re").search(r'^module\s+(\S+)', text, __import__("re").MULTILINE)
            if m: mod = m.group(1)
            reqs = len(__import__("re").findall(r'^\s*require\s+', text, __import__("re").MULTILINE))
            out.append(f"- go.mod: module {mod}, {reqs} 个 require")
        except Exception:
            out.append("- go.mod")
    # Cargo.toml
    ct = root / "Cargo.toml"
    if ct.exists():
        try:
            text = ct.read_text(encoding="utf-8")
            m = __import__("re").search(r'^name\s*=\s*"(\S+)"', text, __import__("re").MULTILINE)
            name = m.group(1) if m else "?"
            deps = len(__import__("re").findall(r'^\[dependencies\]', text, __import__("re").MULTILINE))
            out.append(f"- Cargo.toml: {name}, 含 [dependencies]")
        except Exception:
            out.append("- Cargo.toml")
    # pyproject.toml
    ppt = root / "pyproject.toml"
    if ppt.exists():
        out.append("- pyproject.toml（Python 项目配置）")
    # Gemfile
    if (root / "Gemfile").exists():
        out.append("- Gemfile（Ruby 依赖）")
    # pom.xml / build.gradle
    if (root / "pom.xml").exists():
        out.append("- pom.xml（Maven 项目）")
    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        out.append("- build.gradle（Gradle 项目）")
    return out


def _extract_commands(root: Path) -> list[str]:
    """Extract build/test commands from Makefile and package.json."""
    import json
    out: list[str] = []
    # Makefile
    mf = root / "Makefile"
    if mf.exists():
        try:
            text = mf.read_text(encoding="utf-8")
            targets = set(__import__("re").findall(r'^([a-zA-Z_][a-zA-Z0-9_.-]*):', text, __import__("re").MULTILINE))
            notable = targets & {"build", "test", "run", "install", "deploy", "lint", "fmt", "clean", "dev", "start"}
            if notable:
                out.append(f"- make {' / make '.join(sorted(notable))}")
        except Exception:
            pass
    # package.json scripts
    pj = root / "package.json"
    if pj.exists():
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
            scripts = data.get("scripts", {})
            notable = {k: v for k, v in scripts.items() if k in {"build", "test", "dev", "start", "lint", "format", "deploy", "serve"}}
            if notable:
                cmds = " / ".join(f"npm run {k}" for k in sorted(notable))
                out.append(f"- {cmds}")
        except Exception:
            pass
    # CI configs
    gh_workflows = root / ".github" / "workflows"
    if gh_workflows.exists():
        try:
            wfs = list(gh_workflows.glob("*.yml")) + list(gh_workflows.glob("*.yaml"))
            if wfs:
                names = [w.stem for w in wfs]
                out.append(f"- GitHub Actions: {', '.join(names)}")
        except Exception:
            pass
    if not out:
        # fallback: note config files found
        for f in ("pytest.ini", "tox.ini", "jest.config.js", "jest.config.ts",
                  "vitest.config.ts", "vitest.config.js", ".gitlab-ci.yml"):
            if (root / f).exists():
                out.append(f"- 测试/CI 配置: {f}")
    return out


def _extract_symbols(root: Path) -> list[str]:
    """Walk source files and extract key symbols (def/class/func/export) by language regex."""
    import re
    # Language-specific patterns: (extensions, [(pattern, kind), ...])
    PATTERNS: dict[str, tuple[list[str], list[tuple[str, str]]]] = {
        "py": (["py"], [
            (r'^\s*(async\s+)?def\s+(\w+)', "def"),
            (r'^\s*class\s+(\w+)', "class"),
        ]),
        "go": (["go"], [
            (r'^func\s+(?:\([^)]*\)\s+)?(\w+)', "func"),
            (r'^type\s+(\w+)', "type"),
        ]),
        "ts": (["ts", "tsx", "js", "jsx", "mjs", "cjs"], [
            (r'^(?:export\s+)?(?:async\s+)?function\s+(\w+)', "fn"),
            (r'^(?:export\s+)?class\s+(\w+)', "class"),
            (r'^(?:export\s+)?(?:const|let|var)\s+(\w+)', "var"),
            (r'^(?:export\s+)?(?:interface|type)\s+(\w+)', "type"),
        ]),
        "rs": (["rs"], [
            (r'^\s*(?:pub(?:\s*\(\s*crate\s*\))?\s+)?fn\s+(\w+)', "fn"),
            (r'^\s*(?:pub\s+)?struct\s+(\w+)', "struct"),
            (r'^\s*(?:pub\s+)?enum\s+(\w+)', "enum"),
            (r'^\s*(?:pub\s+)?trait\s+(\w+)', "trait"),
        ]),
        "java": (["java"], [
            (r'^\s*(?:public\s+)?(?:abstract\s+)?(?:static\s+)?(?:final\s+)?(?:class|interface|enum)\s+(\w+)', "class"),
        ]),
    }

    # Build ext->pattern_list index
    ext_map: dict[str, list[tuple[str, str]]] = {}
    for lang_cfg in PATTERNS.values():
        exts, pats = lang_cfg
        for ext in exts:
            ext_map[ext] = pats

    # Walk files
    results: list[tuple[str, str, list[str]]] = []  # (dir_group, rel_path, [symbol_str])
    total_syms = 0
    files_scanned = 0
    for f in projects._walk_files(root, _REPO_MAP_MAX_FILES):
        if files_scanned >= _REPO_MAP_MAX_FILES:
            break
        ext = f.suffix.lstrip(".").lower()
        if ext not in ext_map:
            continue
        if ext not in projects._SRC_EXTS and f.suffix.lower() not in projects._SRC_EXTS:
            continue
        files_scanned += 1
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        pats = ext_map[ext]
        syms: list[str] = []
        for line in text.splitlines()[:400]:  # only scan top 400 lines per file
            for pat, kind in pats:
                m = re.search(pat, line)
                if m:
                    syms.append(f"{kind} {m.group(2) if m.lastindex >= 2 else m.group(1)}")
                    if len(syms) >= 30:
                        break
            if len(syms) >= 30:
                syms.append("…")
                break
        if not syms:
            continue
        rel = str(f.relative_to(root)).replace("\\", "/")
        # group by top-level dir
        parts = rel.split("/")
        group = parts[0] if len(parts) > 1 else "（根目录）"
        results.append((group, rel, syms))
        total_syms += len(syms)
        if total_syms >= 500:
            break

    if not results:
        return ["（未找到可解析的源文件符号）"]

    # Group by directory
    from collections import defaultdict
    grouped: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    for group, rel, syms in results:
        grouped[group].append((rel, syms))

    out: list[str] = []
    for grp in sorted(grouped.keys()):
        out.append(f"\n### {grp}/")
        for rel, syms in grouped[grp]:
            sym_str = ", ".join(syms[:25])
            out.append(f"  {rel}: {sym_str}")
    out.append(f"\n（共扫描 {files_scanned} 个源文件，提取 {total_syms} 个符号）")
    return out


def _git_status(root: Path) -> list[str]:
    """Run git commands to get branch, status, recent log."""
    out: list[str] = []
    try:
        branch = projects._git(str(root), "branch", "--show-current")
        out.append(f"- 当前分支: {branch[1].strip() if branch[0] == 0 else '?'}")
    except Exception:
        out.append("- 当前分支: ?")
    try:
        status = projects._git(str(root), "status", "--short")
        if status[0] == 0 and status[1].strip():
            lines = status[1].strip().split("\n")[:15]
            out.append("- 工作区状态:")
            for l in lines:
                out.append(f"  {l}")
        elif status[0] != 0:
            out.append("- 工作区: （无法获取）")
        else:
            out.append("- 工作区: 干净")
    except Exception:
        out.append("- 工作区: （git 不可用）")
    try:
        log = projects._git(str(root), "log", "--oneline", "-5")
        if log[0] == 0 and log[1].strip():
            out.append("- 最近提交:")
            for l in log[1].strip().split("\n")[:5]:
                out.append(f"  {l}")
    except Exception:
        pass
    return out


def _write_file(rel: str, content: str, ctx: ToolContext) -> str:
    if not rel:
        return "（缺少 path 参数）"
    target = ctx._resolve(rel)
    if target is None:
        return "（无可用项目仓库，或路径越界）"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        target.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"（写入失败：{e}）"
    verb = "覆盖" if existed else "创建"
    return f"已{verb} {rel}（{len(content)} 字符）。"


def _run_command(command: str, ctx: ToolContext) -> str:
    command = (command or "").strip()
    if not command:
        return "（缺少 command 参数）"
    low = command.lower()
    if any(d in low for d in _DANGER):
        return f"（命令被安全策略拒绝：{command}）"
    root = ctx._root()
    if root is None:
        return "（无可用项目仓库）"
    try:
        p = subprocess.run(
            command, cwd=str(root), shell=True,
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_CMD_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"（命令超时 {_CMD_TIMEOUT}s 被终止：{command}）"
    out = (p.stdout or "") + (p.stderr or "")
    if len(out) > _CMD_OUT_MAX:
        out = out[:_CMD_OUT_MAX] + "\n…（输出已截断）"
    head = f"$ {command}\n[exit {p.returncode}]\n"
    return head + (out or "（无输出）")
