"""Platform configuration and paths.

Central place for filesystem locations and tunables. Kept dependency-free so it
can be imported from anywhere (engine, executors, memory) without cycles.
"""
from __future__ import annotations

import os
from pathlib import Path

# ---- Paths --------------------------------------------------------------
# Repo root = two levels up from this file (backend/app/config.py -> repo root)
ROOT = Path(__file__).resolve().parents[2]

# Load .env (ANTHROPIC_API_KEY etc.) before anything reads the environment.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

# ---- LLM ----------------------------------------------------------------
# Provider-specific model/effort are now stored in the platform config (DB kv)
# under config["llm"]["providers"][].  The env-var-override below is for
# backward-compat when the DB hasn't been seeded yet.
LLM_MAX_TOKENS = int(os.environ.get("WARROOM_MAX_TOKENS", "4096"))
# Tool-use loop: max model<->tool round-trips per agent turn. Deep investigations
# (repo_map → many grep/read) routinely need >12, so default generously.
LLM_MAX_TOOL_ITERS = int(os.environ.get("WARROOM_MAX_TOOL_ITERS", "24"))
# Hard ceiling on tokens generated per single model call — the model's max output
# (128K on current Claude/Opus-tier models). Streaming is always on, so large
# ceilings don't hit HTTP timeouts. NOTE: the effective per-call limit is
# min(this, provider.max_tokens); a provider configured with an absurd value
# (e.g. 111111) will now use that value again rather than being clamped down —
# fix the provider's max_tokens in 「配置 → LLM 供应商」 to a sane cap if needed.
LLM_MAX_TOKENS_CEILING = int(os.environ.get("WARROOM_MAX_TOKENS_CEILING", "131072"))
# Default output budget for a normal agent reply, passed explicitly so a turn does
# NOT inherit a misconfigured provider.max_tokens (e.g. 111111). Plenty for an
# answer + tool-call args; the 128K ceiling above still applies when a caller
# explicitly asks for more.
LLM_ANSWER_MAX_TOKENS = int(os.environ.get("WARROOM_ANSWER_MAX_TOKENS", "16384"))


def has_api_key() -> bool:
    """Legacy check — does the Anthropic env var exist?"""
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

DATA_DIR = Path(os.environ.get("WARROOM_DATA", ROOT / "data"))
WORKSPACES_DIR = Path(os.environ.get("WARROOM_WORKSPACES", ROOT / "workspaces"))
# Each agent role gets its OWN independent clone here: agent_repos/<pid>/<role>.
# These are separate git repos (own .git/history), not worktrees or submodules of
# the base checkout, so agents on the same project are mutually independent.
AGENT_REPOS_DIR = Path(os.environ.get("WARROOM_AGENT_REPOS", ROOT / "agent_repos"))
MEMORY_DIR = Path(os.environ.get("WARROOM_MEMORY", DATA_DIR / "memory"))
WEB_DIR = ROOT / "web"
DB_PATH = DATA_DIR / "warroom.db"

for _p in (DATA_DIR, WORKSPACES_DIR, AGENT_REPOS_DIR, MEMORY_DIR):
    _p.mkdir(parents=True, exist_ok=True)

# Memory scopes (PRD FR-8.1 / arch 3.6): channel < agent < project < history < permanent
MEMORY_SCOPES = ["channels", "agents", "projects", "history", "permanent"]
for _s in MEMORY_SCOPES:
    (MEMORY_DIR / _s).mkdir(parents=True, exist_ok=True)

# ---- Engine tunables ----------------------------------------------------
# External stuck-detection (PRD FR-5.2 / arch 3.10)
STUCK_NO_PROGRESS_SEC = int(os.environ.get("WARROOM_STUCK_SEC", "90"))
MAX_GATE_FAILURES = int(os.environ.get("WARROOM_MAX_GATE_FAIL", "3"))
MAX_SELF_RETRIES = int(os.environ.get("WARROOM_MAX_SELF_RETRIES", "3"))

# Per-ticket hard budget (PRD NFR-3)
DEFAULT_BUDGET = {
    "max_tokens": 200_000,
    "max_cost_usd": 5.0,
    "max_steps": 40,
    "timeout_sec": 1800,
}

# How fast the mock executor "thinks" (seconds between simulated events).
# Kept short so the demo console feels live without burning real time.
SIM_TICK_SEC = float(os.environ.get("WARROOM_SIM_TICK", "1.4"))
