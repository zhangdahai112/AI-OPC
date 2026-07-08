"""Executor abstraction (arch 3.3, PRD FR-9).

Execution layer is decoupled from orchestration via one contract: a task
envelope goes in; a structured result + a normalized event stream comes out.
Governance (routing, gates, secrets, memory writes, human approval, audit)
stays in the platform layer regardless of which executor runs — so mixing a
strong and a weak backend never lowers the security baseline (FR-9.5).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol


@dataclass
class CapProfile:
    """What an executor can do; drives graceful degradation in the UI."""
    streams_events: bool = False   # fine-grained tool_call stream vs coarse "working -> diff"
    mcp: bool = False
    hooks: bool = False
    subagents: bool = False
    structured_out: bool = False
    resume: bool = False


@dataclass
class Budget:
    max_tokens: int = 200_000
    max_steps: int = 40
    timeout_sec: int = 1800
    max_cost_usd: float = 5.0


@dataclass
class RepoRef:
    name: str = ""
    worktree: str = ""     # filesystem path of the isolated worktree
    branch: str = "main"
    base_sha: str = ""


@dataclass
class TaskEnvelope:
    """Everything an executor needs — note: never carries plaintext secrets.
    Credentials are a broker handle injected into the sandbox env at runtime
    (arch 3.7), never placed into `instruction`/context."""
    task_id: str
    ticket_id: str
    channel_id: str
    role: str
    instruction: str                 # role + boundary + its routing slice only
    repo: RepoRef
    allowed_tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)
    model: str | None = None
    done_hint: str = ""
    # context the mock executor uses to synthesise plausible work
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecResult:
    status: Literal["completed", "needs_input", "failed", "budget_exhausted", "timeout"]
    head_sha: str = ""
    diff_ref: str = ""
    handoff: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    summary: str = ""


# Emitter the engine passes in; executors push normalized events through it.
Emit = Callable[..., None]


class Executor(Protocol):
    name: str
    caps: CapProfile

    async def run(self, env: TaskEnvelope, emit: Emit) -> ExecResult: ...
