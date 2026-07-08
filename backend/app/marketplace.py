"""市场（Marketplace）—— 聚合**真实**的 MCP 连接器市场与技能市场，一键安装。

数据源
------
- 官方 MCP Registry（**免费、无需 key**）：``GET registry.modelcontextprotocol.io/v0/servers``
- Smithery（**免费版，需 API key**）：``GET registry.smithery.ai/servers?q=``（Authorization: Bearer）
  安装 Smithery 托管服务时，自动确保一条 ``connection:smithery`` 承载 key（凭证走 connections 层）。
- 技能：复用 ``skill_store``（curated 种子 + best-effort 远程）。

一键安装 = 把归一化后的条目写进**平台级已装目录**（``db.kv`` 命名空间 ``installed_mcp``，
技能复用 ``skill_store``）。Agent 从已装目录里挑选挂载（写进 manifest.mcp / manifest.skills）。

所有远程拉取都是 best-effort：短超时、失败静默回退，**绝不 raise**，保证市场页永远可用。
"""
from __future__ import annotations

import os
import re
from typing import Any

import httpx

from . import connections, db, skill_store

_OFFICIAL_URL = "https://registry.modelcontextprotocol.io/v0/servers"
_SMITHERY_URL = "https://registry.smithery.ai/servers"
_KV_MCP = "installed_mcp"
_TIMEOUT = 12


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
_GENERIC = {"mcp", "server", "mcp-server", "mcpserver", "api", "service"}


def _slug(name: str) -> str:
    """把任意字符串压成干净 token。"""
    return re.sub(r"[^a-zA-Z0-9]+", "_", (name or "").lower()).strip("_")


def _server_slug(name: str) -> str:
    """从服务名派生**有区分度**的 server token（mcp__<server>__<tool> 命名空间用）。

    末段若是 mcp/server 之类泛化词，退回用前一段，避免 "*/mcp" 全部撞成 "mcp"。
    """
    parts = [p for p in re.split(r"[/]", name or "") if p]
    tail = parts[-1] if parts else name
    if len(parts) > 1 and tail.lower() in _GENERIC:
        tail = parts[-2]
    return _slug(tail) or _slug(name) or "mcp"


def smithery_key() -> str | None:
    """Smithery API key：环境变量优先，其次平台 config。"""
    key = os.environ.get("SMITHERY_API_KEY")
    if key:
        return key
    cfg = db.kv_get("config", {}) or {}
    return (cfg.get("market", {}) or {}).get("smithery_key") or None


# ---------------------------------------------------------------------------
# MCP 市场搜索（真实数据源，归一化成统一卡片）
# ---------------------------------------------------------------------------
# 卡片形状：{id, source, name, description, homepage, icon, transport, mount, verified, useCount}
async def search_mcp(q: str = "", limit: int = 40) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        # ① Smithery（若配了 key）—— 语义搜索，条目更丰富
        key = smithery_key()
        if key:
            try:
                r = await client.get(
                    _SMITHERY_URL,
                    params={"q": q or "", "pageSize": min(limit, 30), "page": 1},
                    headers={"Authorization": f"Bearer {key}"},
                )
                if r.status_code == 200:
                    for s in (r.json().get("servers") or []):
                        c = _from_smithery(s)
                        if c and c["id"] not in seen:
                            seen.add(c["id"]); cards.append(c)
            except Exception as e:  # noqa: BLE001
                print(f"[market] smithery search failed: {type(e).__name__}: {e}")

        # ② 官方 MCP Registry（无需 key，兜底保证有真实清单）
        try:
            r = await client.get(_OFFICIAL_URL, params={"limit": 60})
            if r.status_code == 200:
                ql = (q or "").lower()
                for entry in (r.json().get("servers") or []):
                    c = _from_official(entry)
                    if not c or c["id"] in seen:
                        continue
                    if ql and ql not in (c["name"] + " " + c["description"]).lower():
                        continue
                    seen.add(c["id"]); cards.append(c)
        except Exception as e:  # noqa: BLE001
            print(f"[market] official registry search failed: {type(e).__name__}: {e}")

    return cards[:limit]


def _from_smithery(s: dict[str, Any]) -> dict[str, Any] | None:
    qn = s.get("qualifiedName") or ""
    if not qn:
        return None
    server = _server_slug(qn)
    return {
        "id": f"smithery:{qn}",
        "source": "smithery",
        "name": s.get("displayName") or qn,
        "description": s.get("description") or "",
        "homepage": s.get("homepage") or f"https://smithery.ai/server/{qn}",
        "icon": s.get("iconUrl") or "",
        "transport": "http",
        "verified": bool(s.get("verified")),
        "useCount": s.get("useCount") or 0,
        "mount": {
            "server": server,
            "transport": "http",
            "url": f"https://server.smithery.ai/{qn}/mcp",
            "ref": "connection:smithery",
            "tools": ["*"],
        },
    }


def _from_official(entry: dict[str, Any]) -> dict[str, Any] | None:
    srv = entry.get("server") or entry  # tolerate both {server:{}} and flat
    name = srv.get("name") or ""
    if not name:
        return None
    server = _server_slug(name)
    remotes = srv.get("remotes") or []
    packages = srv.get("packages") or []
    icons = srv.get("icons") or []
    mount: dict[str, Any] | None = None
    if remotes:
        url = remotes[0].get("url") or remotes[0].get("endpoint")
        if url:
            mount = {"server": server, "transport": "http", "url": url, "tools": ["*"]}
    if mount is None and packages:
        cmd = _package_command(packages[0])
        if cmd:
            mount = {"server": server, "transport": "stdio", "command": cmd, "tools": ["*"]}
    if mount is None:
        return None
    return {
        "id": f"official:{name}",
        "source": "official",
        "name": srv.get("title") or name,
        "description": srv.get("description") or "",
        "homepage": srv.get("websiteUrl") or (srv.get("repository") or {}).get("url") or "",
        "icon": (icons[0].get("src") if icons and isinstance(icons[0], dict) else "") or "",
        "transport": mount["transport"],
        "verified": False,
        "useCount": 0,
        "mount": mount,
    }


def _package_command(pkg: dict[str, Any]) -> str | None:
    """把一个 package 描述映射成 stdio 启动命令（best-effort）。"""
    rt = (pkg.get("registryType") or pkg.get("registry_name") or "").lower()
    ident = pkg.get("identifier") or pkg.get("name") or ""
    if not ident:
        return None
    if rt in ("npm", "node"):
        return f"npx -y {ident}"
    if rt in ("pypi", "python"):
        return f"uvx {ident}"
    if rt in ("oci", "docker"):
        return f"docker run -i --rm {ident}"
    return None


# ---------------------------------------------------------------------------
# 技能市场（复用 skill_store）
# ---------------------------------------------------------------------------
def search_skills(q: str = "", source: str = "") -> list[dict[str, Any]]:
    out = []
    for s in skill_store.search(q, source):
        c = dict(s)
        c["kind"] = "skill"
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# 一键安装 / 已装目录（平台级）
# ---------------------------------------------------------------------------
def _load_installed() -> list[dict[str, Any]]:
    return db.kv_get(_KV_MCP, []) or []


def _save_installed(items: list[dict[str, Any]]) -> None:
    db.kv_set(_KV_MCP, items)


def install_mcp(card: dict[str, Any]) -> dict[str, Any]:
    """把一张 MCP 市场卡片装进平台级已装目录。返回已装条目（含 mount）。

    Smithery 托管服务：若配了 key，顺手确保一条 connection:smithery 承载它。
    """
    if not card or not card.get("id") or not card.get("mount"):
        raise ValueError("invalid card: 需要 id 与 mount")

    if card.get("source") == "smithery":
        key = smithery_key()
        if key:
            connections.upsert_connection(
                {"id": "connection:smithery", "type": "http", "name": "Smithery",
                 "token": key})

    entry = {
        "id": card["id"],
        "name": card.get("name") or card["id"],
        "description": card.get("description") or "",
        "source": card.get("source") or "official",
        "homepage": card.get("homepage") or "",
        "icon": card.get("icon") or "",
        "mount": card["mount"],
        "needs_key": card.get("source") == "smithery" and not smithery_key(),
        "installed_at": db.now(),
    }
    items = [it for it in _load_installed() if it.get("id") != entry["id"]]
    items.append(entry)
    _save_installed(items)
    db.audit("decision", actor="human", detail={"mcp_installed": entry["id"]})
    return entry


def list_installed_mcp() -> list[dict[str, Any]]:
    return _load_installed()


def uninstall_mcp(mcp_id: str) -> None:
    _save_installed([it for it in _load_installed() if it.get("id") != mcp_id])
    db.audit("decision", actor="human", detail={"mcp_uninstalled": mcp_id})


def installed() -> dict[str, Any]:
    """平台级已装目录：MCP + 技能。供 Agent Studio「从已装挑选」。"""
    return {"mcp": list_installed_mcp(), "skills": skill_store.list_installed()}
