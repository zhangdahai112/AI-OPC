"""Agent Manifest — the standardized, per-(project,role) configuration unit.

Design
------
Everything that used to be scattered (executor toggle, per-agent charter
markdown, a static global skills list) is collapsed into ONE spec — the
*Agent Manifest* — resolved through a three-layer inheritance chain::

    platform default  →  project override  →  agent(role) override

Only the *overrides* are persisted (table ``agent_manifests``); the platform
default is derived in code, so a brand-new project inherits sane behavior with
zero configuration ("simple"), while any field can be overridden per project or
per role ("professional").

A resolved manifest is a plain dict with a stable shape (see ``platform_default``)
so the prompt builder (:func:`chat.assemble_system`), the tool layer
(:func:`tools.tool_specs`) and — later — the MCP client / skill installer all
read from the same source of truth.

The charter (system prompt) is *referenced*, not copied: it is loaded live from
the existing per-agent memory (``projects.get_agent_memory``), so this migration
is non-destructive — old data keeps working untouched.
"""
from __future__ import annotations

import copy
from typing import Any

from . import db, llm, projects
from .channels import ROLE_CN, ROLES

# role -> (zh name, icon, one-line focus) — reuse the charter role table.
try:
    from .skills import _ROLE_EXPERTISE as _ROLE_META
except Exception:  # pragma: no cover — defensive, skills should always import
    _ROLE_META = {r: (ROLE_CN.get(r, r), "🤖", "") for r in ROLES}

MANIFEST_VERSION = "warroom/v1"

# ── built-in tool sets (least privilege by role) ───────────────────────────
_ALL_TOOLS = ["list_dir", "read_file", "grep", "write_file", "run_command"]
_READONLY_TOOLS = ["list_dir", "read_file", "grep"]

# developer/tester/devops actually change the repo; the rest are read-only.
_ROLE_TOOLS: dict[str, list[str]] = {
    "coordinator": _READONLY_TOOLS,
    "analyst": _READONLY_TOOLS,
    "developer": _ALL_TOOLS,
    "tester": _ALL_TOOLS,
    "devops": _ALL_TOOLS,
    "reporter": _READONLY_TOOLS,
}

# write / run default to "ask" (surface for approval) for roles that have them.
_MUTATING_TOOLS = {"write_file", "run_command"}


def _default_model() -> dict[str, Any]:
    """Model defaults inherited from the active LLM provider (if any)."""
    p = llm.get_active_provider() or {}
    return {
        "provider": p.get("id", ""),
        "model": p.get("model", ""),
        "maxTokens": p.get("max_tokens", 4096),
        "effort": p.get("effort", "medium"),
    }


def platform_default(role: str) -> dict[str, Any]:
    """The base manifest for a role, before any project/agent override."""
    name_cn, icon, focus = _ROLE_META.get(role, (ROLE_CN.get(role, role), "🤖", ""))
    tools = list(_ROLE_TOOLS.get(role, _READONLY_TOOLS))
    policy = {t: "ask" for t in tools if t in _MUTATING_TOOLS}
    return {
        "apiVersion": MANIFEST_VERSION,
        "kind": "Agent",
        "role": role,
        "identity": {"role": role, "name": name_cn, "avatar": icon, "focus": focus},
        "model": _default_model(),
        "prompt": {"charter": "", "guardrails": []},
        "harness": {"builtinTools": tools, "toolPolicy": policy},
        "mcp": [],          # phase 2: external MCP server mounts
        "skills": [],       # phase 2: installed skills (pinned)
        "memory": {"scopes": ["agents", "projects"]},
        "budget": {"maxTokens": 0, "maxCostUsd": 0},  # 0 = inherit ticket budget
        "enabled": True,
        "inherited": True,  # flipped to False once an override exists
    }


# ── override persistence ───────────────────────────────────────────────────
def get_overrides(project_id: str, role: str) -> dict[str, Any]:
    """The partial override patch stored for (project, role), or {}."""
    row = db.query_one(
        "SELECT manifest FROM agent_manifests WHERE project_id=? AND role=?",
        (project_id or "", role))
    return db.loads(row["manifest"], {}) if row else {}


def set_overrides(project_id: str, role: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Store the override patch (partial manifest) for (project, role).

    An empty/falsey patch means "reset to platform default" — the override row is
    deleted so the manifest goes back to fully inherited.
    """
    if not patch:
        db.execute("DELETE FROM agent_manifests WHERE project_id=? AND role=?",
                   (project_id or "", role))
        action = "agent_manifest_reset"
    else:
        db.execute(
            "INSERT INTO agent_manifests(project_id,role,manifest,updated_at) "
            "VALUES(?,?,?,?) ON CONFLICT(project_id,role) DO UPDATE SET "
            "manifest=excluded.manifest, updated_at=excluded.updated_at",
            (project_id or "", role, db.dumps(patch), db.now()))
        action = "agent_manifest_saved"
    db.audit("decision", ticket_id=None, actor="human",
             detail={action: {"project": project_id, "role": role}})
    return resolve(project_id, role)


# ── merge + resolve ────────────────────────────────────────────────────────
def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``patch`` over ``base``. Lists are replaced wholesale
    (a manifest's tool list / mcp list is an explicit set, not append-only)."""
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def resolve(project_id: str | None, role: str) -> dict[str, Any]:
    """Fully-resolved manifest for (project, role): platform default ← project/
    role override, with the charter loaded live from per-agent memory."""
    manifest = platform_default(role)
    patch = get_overrides(project_id, role) if project_id else {}
    if patch:
        manifest = deep_merge(manifest, patch)
        manifest["inherited"] = False
    # charter is referenced, not copied — load it live so old memory keeps working
    if project_id and not manifest["prompt"].get("charter"):
        manifest["prompt"]["charter"] = projects.get_agent_memory(project_id, role) or ""
    return manifest


def list_for_project(project_id: str) -> list[dict[str, Any]]:
    """Resolved manifest for every role, for the Agent Studio UI."""
    return [resolve(project_id, role) for role in ROLES]


def allowed_tools(manifest: dict[str, Any]) -> set[str]:
    """The built-in tool names this manifest permits (least-privilege gate)."""
    return set(manifest.get("harness", {}).get("builtinTools", []))
