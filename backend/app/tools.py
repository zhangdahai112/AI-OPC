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


# ── tool schema (Anthropic tool-use format) ────────────────────────────────
def tool_specs() -> list[dict[str, Any]]:
    """JSON schema for every tool, in Anthropic ``tools=`` format."""
    return [
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
            "description": "读取项目仓库里一个文件的内容（相对仓库根的路径）。",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "相对仓库根的文件路径"}},
                "required": ["path"],
            },
        },
        {
            "name": "grep",
            "description": "在项目仓库源码里全文搜索一个子串（不区分大小写），返回命中的文件、行号、行内容。",
            "input_schema": {
                "type": "object",
                "properties": {"pattern": {"type": "string", "description": "要搜索的子串"}},
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
    ]


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
            return _read_file(args.get("path", ""), ctx)
        if name == "grep":
            return _grep(args.get("pattern", ""), ctx)
        if name == "write_file":
            return _write_file(args.get("path", ""), args.get("content", ""), ctx)
        if name == "run_command":
            return _run_command(args.get("command", ""), ctx)
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


def _read_file(rel: str, ctx: ToolContext) -> str:
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
    if len(text) > _READ_MAX:
        text = text[:_READ_MAX] + "\n…（文件已截断）"
    return text


def _grep(pattern: str, ctx: ToolContext) -> str:
    if not pattern:
        return "（缺少 pattern 参数）"
    if not ctx.default_pid:
        return "（无可用项目仓库）"
    hits = projects.grep_repo(ctx.default_pid, pattern, max_hits=_GREP_MAX)
    if not hits:
        return f"未命中「{pattern}」。"
    lines = [f"{h['file']}:{h['line']}: {h['text']}" for h in hits]
    return f"命中 {len(hits)} 处：\n" + "\n".join(lines)


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
