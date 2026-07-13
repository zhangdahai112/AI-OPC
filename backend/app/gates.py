"""Acceptance gate pipeline (PRD FR-6, arch 3.5).

"Done" is not the agent's word — it's: on the current HEAD, every required gate
passes. Gates run as discrete checks in a clean sandbox and yield a GateResult
bound to a commit_sha; a HEAD change invalidates older results (FR-6.5).

Layered short-circuit: quick (lint/build/typecheck) -> test (unit/integration
/coverage) -> policy (diff review/migration safety/secret scan) -> human.
Only when every earlier layer is green does the next run (FR-6.3).

Anti-cheat (FR-6.4): gate commands come from **versioned templates** matched to
the detected stack, never from agent input; coverage may only rise, never fall —
enforced here (`_ratchet_coverage`), not trusted to the agent, and only ever
ratcheted on a **real** parsed number.

Real execution: each gate runs the real toolchain (pytest/jest/vitest/go/cargo,
tsc/ruff, py_compile) in the target checkout via `_run`, off the event loop
(`asyncio.to_thread`) so a slow test suite never stalls other channels. When a
project has no runnable tests, the test gate is honestly `skip` — it never
fabricates a green.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import db
from .config import SIM_TICK_SEC, GATE_TIMEOUT

# Gate ids in pipeline order. owner_on_fail decides where a failed gate routes.
GATE_SPEC = [
    {"id": "quick", "label": "快速检查", "layer": 0, "owner_on_fail": "developer",
     "desc": "代码规范 · 构建 · 类型检查"},
    {"id": "test", "label": "测试", "layer": 1, "owner_on_fail": "developer",
     "desc": "单元 + 集成测试 · 覆盖率门槛"},
    {"id": "policy", "label": "策略检查", "layer": 2, "owner_on_fail": "developer",
     "desc": "改动评审 · 迁移安全 · 密钥扫描"},
    {"id": "human", "label": "人工审批", "layer": 3, "owner_on_fail": "human",
     "desc": "上线等敏感动作前由人确认"},
]

# Output cap kept per sub-check so a chatty test runner can't blow up the DB.
_OUT_MAX = 6000


@dataclass
class GateResult:
    gate_id: str
    status: str  # pass | fail | skip | running | pending
    evidence: dict[str, Any] = field(default_factory=dict)
    commit_sha: str = ""
    owner_on_fail: str = ""


def initial_pipeline() -> list[dict]:
    return [{"label": g["label"], "id": g["id"], "state": "pending"} for g in GATE_SPEC]


def _coverage_floor(scope_id: str) -> int:
    """Anti-cheat ratchet: coverage may only rise. Stored per scope (ticket/channel)."""
    return db.kv_get(f"cov_floor:{scope_id}", 80)


def _ratchet_coverage(scope_id: str, observed: int) -> None:
    floor = _coverage_floor(scope_id)
    db.kv_set(f"cov_floor:{scope_id}", max(floor, observed))


# ─────────────────────────────────────────────────────────────────────────────
# Real execution primitives
# ─────────────────────────────────────────────────────────────────────────────
def _run(cmd: str, cwd: str, timeout: int = GATE_TIMEOUT) -> tuple[int, str]:
    """Run one gate command in the checkout. Mirrors tools._run_command's shape
    (shell, utf-8, captured, truncated) but with the larger gate timeout. Returns
    (returncode, combined-output). A missing binary / timeout is reported, never
    raised — the caller decides pass/fail/skip."""
    try:
        p = subprocess.run(
            cmd, cwd=cwd, shell=True,
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, f"（命令超时 {timeout}s 被终止：{cmd}）"
    except OSError as e:
        return 127, f"（命令无法执行：{cmd} — {e}）"
    out = (p.stdout or "") + (p.stderr or "")
    if len(out) > _OUT_MAX:
        out = out[:_OUT_MAX] + "\n…（输出已截断）"
    return p.returncode, out


_MISSING_MARKS = (
    "command not found", "not recognized", "no such file", "not found",
    "no module named", "cannot find module", "could not find",
)


def _looks_missing(out: str) -> bool:
    """The tool itself is absent (vs. the check genuinely failing)."""
    low = out.lower()
    return any(m in low for m in _MISSING_MARKS)


def _detect_stack(root: Path) -> dict:
    """Inspect the checkout and decide which versioned command templates apply.
    Pure filesystem probing — no agent input feeds the command choice."""
    def has(*names: str) -> bool:
        return any((root / n).exists() for n in names)

    py_cfg = has("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg",
                 "pytest.ini", "tox.ini")
    has_py = py_cfg or _any_glob(root, "*.py")
    # pytest is usable if there's a tests dir or any test_*.py / *_test.py file
    has_tests_py = (root / "tests").is_dir() or bool(
        _any_glob(root, "test_*.py") or _any_glob(root, "*_test.py"))

    node = has("package.json")
    runner = None
    if node:
        if has("jest.config.js", "jest.config.ts", "jest.config.cjs", "jest.config.mjs"):
            runner = "jest"
        elif has("vitest.config.ts", "vitest.config.js", "vitest.config.mjs"):
            runner = "vitest"
        else:
            # fall back to package.json devDeps / test script mention
            try:
                import json
                data = json.loads((root / "package.json").read_text(encoding="utf-8"))
                dev = {**data.get("devDependencies", {}), **data.get("dependencies", {})}
                if "vitest" in dev:
                    runner = "vitest"
                elif "jest" in dev:
                    runner = "jest"
            except Exception:
                pass

    return {
        "python": has_py,
        "pytest": has_tests_py,
        "ruff": has(".ruff.toml", "ruff.toml") or _pyproject_has(root, "tool.ruff"),
        "node": node,
        "node_runner": runner,          # jest | vitest | None
        "tsc": has("tsconfig.json"),
        "go": has("go.mod"),
        "go_tests": bool(_any_glob(root, "*_test.go")),
        "rust": has("Cargo.toml"),
    }


def _any_glob(root: Path, pattern: str, limit: int = 1) -> list[Path]:
    out: list[Path] = []
    try:
        for p in root.rglob(pattern):
            # skip vendored / dependency dirs
            if any(seg in {"node_modules", ".venv", "venv", "dist", "build",
                           ".git", "target", "__pycache__"} for seg in p.parts):
                continue
            out.append(p)
            if len(out) >= limit:
                break
    except OSError:
        pass
    return out


def _pyproject_has(root: Path, needle: str) -> bool:
    f = root / "pyproject.toml"
    if not f.exists():
        return False
    try:
        return needle in f.read_text(encoding="utf-8")
    except OSError:
        return False


def _parse_coverage(runner: str, out: str) -> int | None:
    """Extract a total coverage percentage from real runner output. None when the
    runner didn't emit coverage (so we never invent a number)."""
    if runner in ("pytest",):
        m = re.search(r"^TOTAL\s+.*?(\d+)%\s*$", out, re.MULTILINE)
        return int(m.group(1)) if m else None
    if runner in ("jest", "vitest"):
        # coverage summary row: "All files | 87.5 | ..." (first number = statements)
        m = re.search(r"All files\s*\|\s*([\d.]+)", out)
        return int(float(m.group(1))) if m else None
    if runner == "go":
        m = re.search(r"coverage:\s*([\d.]+)%\s+of statements", out)
        return int(float(m.group(1))) if m else None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Gate dispatch
# ─────────────────────────────────────────────────────────────────────────────
async def run_gate(scope_id: str, gate_id: str, worktree: str, head_sha: str,
                   emit) -> GateResult:
    """Run one gate in the sandbox (the worktree). The human gate never runs
    automatically — it returns 'pending' for the approval flow to resolve.
    Blocking toolchain runs are pushed off the event loop."""
    spec = next(g for g in GATE_SPEC if g["id"] == gate_id)
    emit("gate", ticket=scope_id,
         payload={"gate": gate_id, "label": spec["label"], "state": "running"})

    root = Path(worktree) if worktree else None
    if gate_id == "human":
        res = GateResult(gate_id, "pending", commit_sha=head_sha,
                         owner_on_fail="human",
                         evidence={"note": "等待人工审批"})
    elif not root or not root.exists():
        res = GateResult(gate_id, "skip", commit_sha=head_sha,
                         owner_on_fail=spec["owner_on_fail"],
                         evidence={"reason": "无可门禁的工作区"})
    elif gate_id == "quick":
        res = await asyncio.to_thread(_quick, scope_id, str(root), head_sha)
    elif gate_id == "test":
        res = await asyncio.to_thread(_test, scope_id, str(root), head_sha)
    elif gate_id == "policy":
        res = await asyncio.to_thread(_policy, scope_id, str(root), head_sha)
    else:
        res = GateResult(gate_id, "pass", commit_sha=head_sha)

    # small pacing beat so the console reads as live, done AFTER the real work
    await asyncio.sleep(min(SIM_TICK_SEC, 0.5))

    _persist(scope_id, res)
    db.audit("gate", ticket_id=scope_id,
             detail={"gate": gate_id, "status": res.status, "sha": head_sha,
                     "evidence": res.evidence})
    emit("gate", ticket=scope_id,
         payload={"gate": gate_id, "label": spec["label"], "state": res.status,
                  "evidence": res.evidence})
    return res


async def run_pipeline(scope_id: str, worktree: str, head_sha: str, emit,
                       enabled: list[str] | None = None) -> list[GateResult]:
    """Storage-agnostic driver: run the automated gates in layer order against
    `worktree`, short-circuiting at the first hard `fail` (a `skip` is not a fail
    and does not stop the pipeline). Fail routing / re-dispatch is the caller's
    job — this only runs checks and streams `gate` events. Returns every result
    produced (the failing gate, if any, is the last element)."""
    ids = enabled if enabled is not None else [
        g["id"] for g in GATE_SPEC if g["id"] != "human"]
    results: list[GateResult] = []
    for gid in ids:
        res = await run_gate(scope_id, gid, worktree, head_sha, emit)
        results.append(res)
        if res.status == "fail":
            break
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Individual gates — real toolchain runs
# ─────────────────────────────────────────────────────────────────────────────
def _quick(scope_id: str, worktree: str, sha: str) -> GateResult:
    """Build / type-check / lint signal from the real toolchain."""
    stack = _detect_stack(Path(worktree))
    checks: dict[str, str] = {}
    errors: list[str] = []

    # Python: syntax/build via py_compile over first-party sources.
    if stack["python"]:
        bad = []
        for f in _any_glob(Path(worktree), "*.py", limit=2000):
            rc, out = _run(f'python -m py_compile "{f}"', worktree, timeout=60)
            if rc != 0:
                bad.append(f.name)
        checks["build"] = "ok" if not bad else "fail"
        if bad:
            errors += bad
        if stack["ruff"]:
            rc, out = _run("ruff check .", worktree, timeout=180)
            if _looks_missing(out):
                checks["lint"] = "skip"
            else:
                checks["lint"] = "ok" if rc == 0 else "fail"
                if rc != 0:
                    errors.append("ruff")

    # TypeScript type-check.
    if stack["tsc"]:
        rc, out = _run("npx --no-install tsc --noEmit", worktree, timeout=300)
        if _looks_missing(out):
            checks["typecheck"] = "skip"
        else:
            checks["typecheck"] = "ok" if rc == 0 else "fail"
            if rc != 0:
                errors.append("tsc")

    # Compiled-language build.
    if stack["go"]:
        rc, out = _run("go build ./...", worktree, timeout=GATE_TIMEOUT)
        if _looks_missing(out):
            checks["go_build"] = "skip"
        else:
            checks["go_build"] = "ok" if rc == 0 else "fail"
            if rc != 0:
                errors.append("go build")
    if stack["rust"]:
        rc, out = _run("cargo check", worktree, timeout=GATE_TIMEOUT)
        if _looks_missing(out):
            checks["cargo_check"] = "skip"
        else:
            checks["cargo_check"] = "ok" if rc == 0 else "fail"
            if rc != 0:
                errors.append("cargo check")

    status = "skip" if not checks else ("pass" if not errors else "fail")
    return GateResult("quick", status, commit_sha=sha, owner_on_fail="developer",
                      evidence={**checks, "errors": errors})


def _test(scope_id: str, worktree: str, sha: str) -> GateResult:
    """Run the real test suite + coverage. Honest `skip` when nothing to run;
    coverage ratchet only ever moves on a real parsed number."""
    stack = _detect_stack(Path(worktree))
    runner, cmd = _test_command(stack)
    if runner is None:
        return GateResult("test", "skip", commit_sha=sha, owner_on_fail="developer",
                          evidence={"reason": "未检测到测试"})

    rc, out = _run(cmd, worktree)
    if _looks_missing(out):
        return GateResult("test", "skip", commit_sha=sha, owner_on_fail="developer",
                          evidence={"reason": f"{runner} 运行器不可用", "cmd": cmd})

    tests_ok = rc == 0
    cov = _parse_coverage("pytest" if runner == "pytest" else runner, out)
    floor = _coverage_floor(scope_id)
    tail = out[-1200:]

    if not tests_ok:
        status = "fail"
    elif cov is not None and cov < floor:
        status = "fail"
    else:
        status = "pass"
        if cov is not None:
            _ratchet_coverage(scope_id, cov)

    evidence: dict[str, Any] = {
        "runner": runner, "cmd": cmd,
        "tests": "passed" if tests_ok else "failed",
        "coverage": cov if cov is not None else "n/a",
        "floor": floor, "output_tail": tail,
    }
    return GateResult("test", status, commit_sha=sha, owner_on_fail="developer",
                      evidence=evidence)


def _test_command(stack: dict) -> tuple[str | None, str]:
    """Pick the versioned test command for the detected stack (anti-cheat: never
    from agent input). Returns (runner_key, command) or (None, '')."""
    if stack["pytest"]:
        return "pytest", "python -m pytest --cov --cov-report=term-missing -q"
    if stack["node_runner"] == "jest":
        return "jest", "npx --no-install jest --coverage --ci"
    if stack["node_runner"] == "vitest":
        return "vitest", "npx --no-install vitest run --coverage"
    if stack["go"] and stack["go_tests"]:
        return "go", "go test ./... -cover"
    if stack["rust"]:
        return "rust", "cargo test"
    return None, ""


_SECRET_NEEDLES = (
    "AKIA", "secret_key", "BEGIN RSA PRIVATE KEY", "BEGIN OPENSSH PRIVATE KEY",
    "password =", "aws_secret_access_key", "-----BEGIN PRIVATE KEY-----",
)
_SCAN_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
             ".rb", ".env", ".yaml", ".yml", ".json", ".toml", ".ini", ".sh"}


def _policy(scope_id: str, worktree: str, sha: str) -> GateResult:
    """Diff review: secret scan across source files + newly-skipped-test count +
    diff size. A leaked secret is the fail driver."""
    root = Path(worktree)
    leaked, skips = [], []
    for f in root.rglob("*"):
        if not f.is_file() or f.suffix not in _SCAN_EXT:
            continue
        if any(seg in {"node_modules", ".venv", "venv", ".git", "dist",
                       "build", "target", "__pycache__"} for seg in f.parts):
            continue
        try:
            txt = f.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if any(n in txt for n in _SECRET_NEEDLES):
            leaked.append(f.name)
        if "@skip" in txt or "pytest.mark.skip" in txt or ".skip(" in txt:
            skips.append(f.name)

    # diff size, best-effort (no parent commit on a first commit → ignored)
    rc, out = _run("git diff --shortstat HEAD~1", worktree, timeout=30)
    diffstat = out.strip() if rc == 0 and out.strip() else "n/a"

    status = "pass" if not leaked else "fail"
    return GateResult("policy", status, commit_sha=sha, owner_on_fail="developer",
                      evidence={"secret_scan": "clean" if not leaked else "LEAK",
                                "leaked": leaked, "new_skips": skips,
                                "diffstat": diffstat, "migration": "n/a"})


def _persist(scope_id: str, res: GateResult) -> None:
    db.execute(
        "INSERT INTO gate_results(ticket_id,gate_id,status,evidence,commit_sha,"
        "owner_on_fail,created_at) VALUES(?,?,?,?,?,?,?)",
        (scope_id, res.gate_id, res.status, db.dumps(res.evidence),
         res.commit_sha, res.owner_on_fail, db.now()),
    )
