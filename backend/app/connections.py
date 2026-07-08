"""连接器 / 密钥层。

镜像 ``llm.py`` 的 provider 范式：连接的**非密**元数据（类型、host、
env 变量名、出网白名单）持久化在 ``db.kv`` 命名空间 ``"connections"``；
真正的密钥（token / password）**绝不落库**——要么以 inline secret 形式
在请求时临时传入并只回存一个"已配置"标记，要么记一个环境变量名，在
``resolve()`` 时从 ``os.environ`` 现取。返回给前端的一切都经过脱敏。

连接项存储形状（``config`` 列表中的每一项）::

    {
      "id": "connection:ab12cd",
      "name": "GitHub (org)",
      "type": "github",              # github | http | postgres
      "key_env": "GITHUB_TOKEN",     # 环境变量名（回存的引用，非明文）
      "key_set": true,               # 是否曾配过 inline secret（仅标记）
      "config": {                    # 各类型的非密字段（见 CONNECTOR_TYPES）
        "base_url": "https://api.github.com",
        ...
      },
      "egress": ["api.github.com"],  # 允许出网的 host（[] = 不限制）
      "created_at": 1700000000.0,
      "updated_at": 1700000000.0
    }

inline secret（明文 token/password）只在进程内存里驻留（``_SECRETS``），
用于当次 ``resolve()`` / ``verify()``，重启即失；DB 里只留 ``key_set``。
这样 ``resolve()`` 优先返回 inline，其次回退 env 变量名——与
``llm._resolve_api_key`` 的"inline 优先，否则 env"取值顺序保持一致。
"""
from __future__ import annotations

import os
import secrets
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from . import db

KV_NS = "connections"

# 进程内 inline 密钥缓存：{connection_id: secret_plaintext}。绝不落库。
_SECRETS: dict[str, str] = {}


# ---------------------------------------------------------------------------
# 连接器类型 seed —— 每种类型的字段定义（供前端渲染表单 / 校验）
# ---------------------------------------------------------------------------
# 每个 field: {name, label, type(text|url|password|number), required, placeholder}
# type=password 的字段即"密钥"，不落库；其余字段进 connection["config"]。
CONNECTOR_TYPES: list[dict[str, Any]] = [
    {
        "type": "github",
        "label": "GitHub",
        "fields": [
            {"name": "base_url", "label": "API Base URL", "type": "url",
             "required": False, "placeholder": "https://api.github.com"},
            {"name": "token", "label": "Personal Access Token", "type": "password",
             "required": True, "placeholder": "ghp_… 或留空用环境变量"},
        ],
    },
    {
        "type": "http",
        "label": "HTTP 端点",
        "fields": [
            {"name": "url", "label": "URL", "type": "url",
             "required": True, "placeholder": "https://example.com/health"},
            {"name": "auth_header", "label": "Auth Header 名", "type": "text",
             "required": False, "placeholder": "Authorization"},
            {"name": "token", "label": "Bearer Token", "type": "password",
             "required": False, "placeholder": "留空用环境变量"},
        ],
    },
    {
        "type": "postgres",
        "label": "PostgreSQL",
        "fields": [
            {"name": "host", "label": "Host", "type": "text",
             "required": True, "placeholder": "localhost"},
            {"name": "port", "label": "Port", "type": "number",
             "required": False, "placeholder": "5432"},
            {"name": "database", "label": "数据库名", "type": "text",
             "required": False, "placeholder": "postgres"},
            {"name": "user", "label": "用户", "type": "text",
             "required": False, "placeholder": "postgres"},
            {"name": "password", "label": "密码", "type": "password",
             "required": False, "placeholder": "留空用环境变量"},
        ],
    },
]

# 每种类型里哪个 field 承载密钥（password 类型）。
_SECRET_FIELD = {"github": "token", "http": "token", "postgres": "password"}

# 密钥字段名启发式 —— defense in depth：即便调用方把密钥放进未声明的字段，
# 也绝不让它落库或被返回。
_SECRET_NAME_HINT = ("token", "secret", "password", "passwd",
                     "apikey", "api_key", "bearer", "credential")


def _is_secretish(name: str) -> bool:
    n = str(name).lower()
    return any(h in n for h in _SECRET_NAME_HINT)


def _declared_fields(ctype: str) -> dict[str, dict[str, Any]]:
    """该类型声明的字段 name -> field 定义。"""
    for t in CONNECTOR_TYPES:
        if t["type"] == ctype:
            return {f["name"]: f for f in t["fields"]}
    return {}


def connector_types() -> list[dict[str, Any]]:
    """返回 seed 的连接器类型 + fields 定义（供前端建表单）。"""
    return CONNECTOR_TYPES


# ---------------------------------------------------------------------------
# 低层持久化（db.kv 命名空间 "connections"）
# ---------------------------------------------------------------------------


def _load_all() -> list[dict[str, Any]]:
    return db.kv_get(KV_NS, []) or []


def _save_all(items: list[dict[str, Any]]) -> None:
    db.kv_set(KV_NS, items)


def _find(items: list[dict[str, Any]], cid: str) -> dict[str, Any] | None:
    for it in items:
        if it.get("id") == cid:
            return it
    return None


# ---------------------------------------------------------------------------
# 脱敏 —— 返回给前端 / list 时绝不含明文密钥
# ---------------------------------------------------------------------------


def _sanitize(item: dict[str, Any]) -> dict[str, Any]:
    """把一条连接抹掉一切密钥字段，附上 ``key_configured`` 状态标记。"""
    cid = item.get("id", "")
    cfg = dict(item.get("config") or {})
    # config 里理论上不该存密钥，但保险起见剥掉：① 该类型声明的 password 字段
    # ② 任何名字看着像密钥的字段（防未声明字段泄漏 / 旧数据残留）。
    declared = _declared_fields(item.get("type", ""))
    for k in list(cfg.keys()):
        if _is_secretish(k) or declared.get(k, {}).get("type") == "password":
            cfg.pop(k, None)
    return {
        "id": cid,
        "name": item.get("name", ""),
        "type": item.get("type", ""),
        "key_env": item.get("key_env", ""),
        "config": cfg,
        "egress": item.get("egress", []) or [],
        "key_configured": _has_secret(item) is not None,
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# 密钥解析（inline 优先，否则 env 变量名）—— 仿 llm._resolve_api_key
# ---------------------------------------------------------------------------


def _has_secret(item: dict[str, Any]) -> str | None:
    """当前是否拿得到该连接的密钥（inline 内存 或 env 变量）。"""
    cid = item.get("id", "")
    inline = _SECRETS.get(cid)
    if inline:
        return inline
    env_var = item.get("key_env", "") or ""
    if env_var:
        return os.environ.get(env_var)
    return None


def _headers_for(item: dict[str, Any], secret: str) -> dict[str, str]:
    """依类型把密钥拼成 http 注入用的 headers。"""
    t = item.get("type", "")
    cfg = item.get("config") or {}
    if t == "github":
        return {"Authorization": f"Bearer {secret}",
                "Accept": "application/vnd.github+json"}
    if t == "http":
        hdr = cfg.get("auth_header") or "Authorization"
        # 若用户自定义了非 Authorization 头，按原样放 token；否则加 Bearer 前缀
        val = secret if hdr.lower() != "authorization" else f"Bearer {secret}"
        return {hdr: val}
    return {}


def _env_for(item: dict[str, Any], secret: str) -> dict[str, str]:
    """依类型把密钥拼成 stdio/子进程注入用的环境变量。"""
    t = item.get("type", "")
    if t == "github":
        return {"GITHUB_TOKEN": secret}
    if t == "postgres":
        return {"PGPASSWORD": secret}
    return {}


# ---------------------------------------------------------------------------
# 契约 API
# ---------------------------------------------------------------------------


def resolve(ref: str) -> dict | None:
    """``ref`` 形如 ``"connection:<id>"``，返回 ``{"env":{…}, "headers":{…}}`` 或 None。

    密钥按 inline 优先、否则 env 变量名 现取；拿不到密钥则返回空的 env/headers
    （连接存在但未配密钥），连接根本不存在才返回 None。
    """
    if not ref:
        return None
    items = _load_all()
    item = _find(items, ref)
    if item is None:
        return None
    secret = _has_secret(item)
    if not secret:
        return {"env": {}, "headers": {}}
    return {"env": _env_for(item, secret), "headers": _headers_for(item, secret)}


def egress_allow(ref: str) -> list[str]:
    """该连接允许出网的 host 列表（``[]`` = 不限制/未配）。"""
    if not ref:
        return []
    item = _find(_load_all(), ref)
    if item is None:
        return []
    return list(item.get("egress") or [])


def list_connections() -> list[dict]:
    """key-free 状态列表（不含明文密钥）。"""
    return [_sanitize(it) for it in _load_all()]


def upsert_connection(data: dict) -> dict:
    """存入 ``db.kv`` 命名空间 ``"connections"``；缺 id 则生成。

    ``data`` 接受前端提交的原始表单：type / name / key_env / egress，
    加上散落的字段（其中 password 类字段被识别为 inline 密钥，进内存不落库；
    其余字段进 ``config``）。返回**脱敏后**的连接。
    """
    items = _load_all()
    cid = data.get("id") or f"connection:{secrets.token_hex(3)}"
    existing = _find(items, cid)

    ctype = data.get("type") or (existing or {}).get("type") or "http"
    now = db.now()

    # 收集所有传入字段（顶层非保留 + config 子对象）。
    submitted = dict(data.get("config") or {})
    _reserved = {"id", "name", "type", "key_env", "egress", "config"}
    for k, v in data.items():
        if k not in _reserved:
            submitted[k] = v

    # 依连接器类型做**白名单持久化**：已知类型只接受它声明过的非密字段进
    # config；声明的密钥字段（及任何 secret-ish 字段）一律进内存不落库；
    # 未声明字段直接丢弃。这样即便密钥被塞进错误/未知字段也绝不会明文落库。
    secret_field = _SECRET_FIELD.get(ctype)
    declared = _declared_fields(ctype)
    raw_cfg = dict((existing or {}).get("config") or {})
    inline_secret = None
    for k, v in submitted.items():
        if secret_field and k == secret_field:
            if v:                       # 空串=不改动已配密钥
                inline_secret = str(v)
            continue
        if declared:                    # 已知类型：只留声明过的非密字段
            f = declared.get(k)
            if f and f["type"] == "password":
                if v:
                    inline_secret = str(v)
            elif f and not _is_secretish(k):
                raw_cfg[k] = v
            # 未声明字段：丢弃
        elif _is_secretish(k):          # 未知类型兜底：secret-ish 进内存
            if v:
                inline_secret = str(v)
        else:
            raw_cfg[k] = v
    # 防御：剥掉 config 里一切 secret-ish / 声明为 password 的残留（含旧数据）
    for k in list(raw_cfg.keys()):
        if _is_secretish(k) or declared.get(k, {}).get("type") == "password":
            raw_cfg.pop(k, None)

    item: dict[str, Any] = {
        "id": cid,
        "name": data.get("name") or (existing or {}).get("name") or ctype,
        "type": ctype,
        "key_env": data.get("key_env", (existing or {}).get("key_env", "")) or "",
        "config": raw_cfg,
        "egress": data.get("egress", (existing or {}).get("egress", [])) or [],
        "key_set": (existing or {}).get("key_set", False),
        "created_at": (existing or {}).get("created_at", now),
        "updated_at": now,
    }

    if inline_secret is not None:
        _SECRETS[cid] = inline_secret
        item["key_set"] = True

    if existing is not None:
        items = [item if it.get("id") == cid else it for it in items]
    else:
        items.append(item)
    _save_all(items)
    return _sanitize(item)


def delete_connection(cid: str) -> None:
    items = [it for it in _load_all() if it.get("id") != cid]
    _SECRETS.pop(cid, None)
    _save_all(items)


async def verify(cid: str) -> dict:
    """探活：``{ok, detail|error}``。

    - github: ``GET <base_url>/user``（带 token）
    - http:   ``GET <url>``（带可选 auth）
    - postgres: socket 连 ``host:port``（不引 psycopg，只判端口可达）
    """
    item = _find(_load_all(), cid)
    if item is None:
        return {"ok": False, "error": "connection not found"}

    ctype = item.get("type", "")
    try:
        if ctype == "github":
            return await _verify_github(item)
        if ctype == "http":
            return await _verify_http(item)
        if ctype == "postgres":
            return _verify_postgres(item)
        return {"ok": False, "error": f"unsupported type: {ctype}"}
    except Exception as e:  # noqa: BLE001 —— 探活绝不 raise
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def _verify_github(item: dict[str, Any]) -> dict[str, Any]:
    secret = _has_secret(item)
    if not secret:
        env_var = item.get("key_env", "") or "?"
        return {"ok": False, "error": f"token 未配置（env: {env_var}）"}
    base = (item.get("config") or {}).get("base_url") or "https://api.github.com"
    base = base.rstrip("/")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{base}/user",
            headers={"Authorization": f"Bearer {secret}",
                     "Accept": "application/vnd.github+json"},
        )
    if resp.status_code == 200:
        login = resp.json().get("login", "?")
        return {"ok": True, "detail": f"authenticated as {login}"}
    return {"ok": False, "error": f"HTTP {resp.status_code}"}


async def _verify_http(item: dict[str, Any]) -> dict[str, Any]:
    cfg = item.get("config") or {}
    url = cfg.get("url") or ""
    if not url:
        return {"ok": False, "error": "url 未配置"}
    headers: dict[str, str] = {}
    secret = _has_secret(item)
    if secret:
        headers = _headers_for(item, secret)
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
    ok = resp.status_code < 400
    return {"ok": ok,
            "detail": f"HTTP {resp.status_code}" if ok else None,
            "error": None if ok else f"HTTP {resp.status_code}"}


def _verify_postgres(item: dict[str, Any]) -> dict[str, Any]:
    cfg = item.get("config") or {}
    host = cfg.get("host") or "localhost"
    try:
        port = int(cfg.get("port") or 5432)
    except (TypeError, ValueError):
        port = 5432
    # 不引入 psycopg —— 仅用 socket 判端口可达
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect((host, port))
        return {"ok": True, "detail": f"{host}:{port} 可达"}
    except OSError as e:
        return {"ok": False, "error": f"{host}:{port} 不可达：{e}"}
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# 便捷：egress 校验（供 mcp/http 出网前调用；host 在白名单才放行）
# ---------------------------------------------------------------------------


def egress_permitted(ref: str, url: str) -> bool:
    """给定 url 的 host 是否被该连接的 egress 白名单允许（空白名单=放行）。"""
    allow = egress_allow(ref)
    if not allow:
        return True
    host = urlparse(url).hostname or ""
    return host in allow
