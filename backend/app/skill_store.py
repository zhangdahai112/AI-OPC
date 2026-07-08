"""技能市场 / 安装器 —— Agent Skills 与 MCP 市场的统一入口。

一个 agent 的能力不止内置工具（tools.py）与外部 MCP（mcp.py），还有*技能*
（skills）：一段可渐进式披露的领域知识 / 操作手册。本模块给平台一个诚实的
技能来源：

    search()        → 浏览技能市场（先内置 curated 种子，可离线用；有网再 best-effort 拉取）
    install()       → 拉取并校验 SKILL.md，pin 版本，落库 db.kv "installed_skills"
    list/get/uninstall → 已装技能的增删查
    system_index()  → 把已装技能渲染成"可用技能"索引，注入 system prompt（渐进式披露）
    body()          → 完整技能体，仅在 activate（真正用到）时读取

设计取向与 llm.py / tools.py 一致：
- 任何网络/解析失败都*静默回退*到种子或抛清晰错误，绝不让市场把请求打挂；
- DB 里只存*非密*元数据与技能体，凭证/密钥归 connections.py 管；
- source 三类：``anthropic``（Agent Skills 格式）/ ``smithery``（MCP 市场）/ ``builtin``。
"""
from __future__ import annotations

import re
from typing import Any

from . import db

_KV_INSTALLED = "installed_skills"   # db.kv 命名空间：{skill_id: <installed meta>}

# 远程拉取超时（秒）——市场是锦上添花，别让它拖慢安装。
_FETCH_TIMEOUT = 12


# ── curated 种子清单（离线可用；均为真实存在的知名条目作示例）────────────────
# 每条含 store 元数据 + 一份可离线安装的 body / mount 建议，这样即使断网，
# search→install→system_index→body 全链路也能跑通。
_SEED: list[dict[str, Any]] = [
    # —— Anthropic Agent Skills（渐进式披露：name/description/body）——
    {
        "id": "pdf",
        "name": "PDF 处理",
        "description": "填写 PDF 表单、合并/拆分页面、提取文本与表格。当任务涉及读取或生成 PDF 文档时使用。",
        "source": "anthropic",
        "version": "1.0.0",
        "permissions": ["read_file", "write_file", "run_command"],
        "when_to_use": "需要解析、填写或生成 PDF 文档时。",
        "body": (
            "# PDF 处理技能\n\n"
            "用 `pypdf` / `pdfplumber` 读取，`reportlab` 生成。\n\n"
            "- 提取文本：`pdfplumber.open(path).pages[i].extract_text()`。\n"
            "- 提取表格：`page.extract_tables()`。\n"
            "- 合并/拆分：`pypdf.PdfWriter` 逐页 `add_page`。\n"
            "- 填表单：`writer.update_page_form_field_values(page, {field: value})`。\n"
        ),
    },
    {
        "id": "docx",
        "name": "Word 文档",
        "description": "创建与编辑 .docx：样式、表格、页眉页脚、批注。当任务需要产出 Word 文档时使用。",
        "source": "anthropic",
        "version": "1.0.0",
        "permissions": ["read_file", "write_file"],
        "when_to_use": "需要生成或修改 Microsoft Word (.docx) 文档时。",
        "body": (
            "# Word 文档技能\n\n"
            "用 `python-docx`。\n\n"
            "- 新建：`from docx import Document; doc = Document()`。\n"
            "- 段落/标题：`doc.add_heading(...)` / `doc.add_paragraph(...)`。\n"
            "- 表格：`doc.add_table(rows, cols)`，`cell.text = ...`。\n"
            "- 保存：`doc.save(path)`。\n"
        ),
    },
    {
        "id": "xlsx",
        "name": "Excel 表格",
        "description": "读写 .xlsx：公式、图表、多工作表、单元格样式。处理电子表格数据时使用。",
        "source": "anthropic",
        "version": "1.0.0",
        "permissions": ["read_file", "write_file"],
        "when_to_use": "需要读取或生成 Excel (.xlsx) 电子表格时。",
        "body": (
            "# Excel 表格技能\n\n"
            "用 `openpyxl`。\n\n"
            "- 打开：`from openpyxl import load_workbook; wb = load_workbook(path)`。\n"
            "- 写值：`ws['A1'] = 1` 或 `ws.cell(row, col, value)`。\n"
            "- 公式：直接写 `ws['C1'] = '=A1+B1'`。\n"
            "- 保存：`wb.save(path)`。\n"
        ),
    },
    {
        "id": "pptx",
        "name": "PowerPoint 演示",
        "description": "生成与编辑 .pptx：版式、图表、图片、母版。需要产出幻灯片时使用。",
        "source": "anthropic",
        "version": "1.0.0",
        "permissions": ["read_file", "write_file"],
        "when_to_use": "需要生成或修改 PowerPoint (.pptx) 演示文稿时。",
        "body": (
            "# PowerPoint 演示技能\n\n"
            "用 `python-pptx`。\n\n"
            "- 新建：`from pptx import Presentation; prs = Presentation()`。\n"
            "- 加页：`prs.slides.add_slide(prs.slide_layouts[i])`。\n"
            "- 文本框/占位符：`slide.shapes.title.text = ...`。\n"
            "- 保存：`prs.save(path)`。\n"
        ),
    },
    # —— Smithery MCP 市场（安装后返回一条 mcp mount 建议）——
    {
        "id": "@modelcontextprotocol/server-filesystem",
        "name": "Filesystem MCP",
        "description": "通过 MCP 暴露本地文件系统的读写工具。为 agent 挂载受控的文件访问能力。",
        "source": "smithery",
        "version": "latest",
        "permissions": ["filesystem:read", "filesystem:write"],
        "when_to_use": "需要通过标准 MCP 协议访问本地文件系统时。",
        "mount": {
            "server": "filesystem",
            "transport": "stdio",
            "command": "npx -y @modelcontextprotocol/server-filesystem",
            "tools": ["*"],
        },
    },
    {
        "id": "@modelcontextprotocol/server-github",
        "name": "GitHub MCP",
        "description": "通过 MCP 操作 GitHub：仓库、issue、PR、文件。需要与 GitHub 交互时使用。",
        "source": "smithery",
        "version": "latest",
        "permissions": ["github:repo", "github:issues", "github:pull_requests"],
        "when_to_use": "需要读写 GitHub 仓库 / issue / PR 时。",
        "mount": {
            "server": "github",
            "transport": "stdio",
            "command": "npx -y @modelcontextprotocol/server-github",
            "tools": ["*"],
        },
    },
    {
        "id": "@modelcontextprotocol/server-fetch",
        "name": "Fetch MCP",
        "description": "通过 MCP 抓取网页并转成 Markdown，供模型阅读。需要联网取内容时使用。",
        "source": "smithery",
        "version": "latest",
        "permissions": ["network:http"],
        "when_to_use": "需要抓取网页 / HTTP 内容供模型阅读时。",
        "mount": {
            "server": "fetch",
            "transport": "stdio",
            "command": "npx -y @modelcontextprotocol/server-fetch",
            "tools": ["*"],
        },
    },
    # —— builtin：平台内建、无需外部依赖的技能——
    {
        "id": "code-review",
        "name": "代码评审",
        "description": "系统化评审 diff：正确性缺陷、可复用/简化、效率与风格。需要评审代码变更时使用。",
        "source": "builtin",
        "version": "1.0.0",
        "permissions": ["read_file", "grep"],
        "when_to_use": "需要评审一处代码变更（diff / PR）时。",
        "body": (
            "# 代码评审技能\n\n"
            "按优先级逐条检查，最严重在前：\n\n"
            "1. 正确性：边界、空值、并发、错误处理是否漏掉。\n"
            "2. 契约：改动是否破坏调用方签名或既有行为。\n"
            "3. 复用/简化：有无重复逻辑可抽取，有无过度设计。\n"
            "4. 效率：热点路径是否有 O(n^2)、重复 IO。\n"
            "5. 风格：与既有模块的命名/注释密度是否一致。\n"
        ),
    },
]

# 快速索引（seed 只读，按 id 查）。
_SEED_BY_ID = {s["id"]: s for s in _SEED}


# ── 市场浏览 ────────────────────────────────────────────────────────────────
def search(query: str = "", source: str = "") -> list[dict[str, Any]]:
    """返回技能卡元数据 ``[{id,name,description,source,version,permissions}]``。

    先取内置 curated 种子（离线永远可用），再 best-effort 叠加远程结果；远程
    失败静默回退。``query`` 子串过滤（不区分大小写，匹配 name/description/id），
    ``source`` 精确过滤（anthropic / smithery / builtin）。
    """
    cards = [_card(s) for s in _SEED]

    # best-effort：有网则尝试补充远程条目，任何异常都静默忽略、只用种子。
    try:
        cards = _merge_remote(cards, query, source)
    except Exception as e:  # pragma: no cover — 市场是锦上添花
        print(f"[skill_store] remote search skipped: {type(e).__name__}: {e}")

    if source:
        cards = [c for c in cards if c.get("source") == source]
    if query:
        q = query.lower()
        cards = [c for c in cards
                 if q in c.get("name", "").lower()
                 or q in c.get("description", "").lower()
                 or q in c.get("id", "").lower()]
    return cards


def _card(spec: dict[str, Any]) -> dict[str, Any]:
    """从内部 spec 抽出对外的 store 卡元数据（不含 body / mount）。"""
    return {
        "id": spec["id"],
        "name": spec.get("name", spec["id"]),
        "description": spec.get("description", ""),
        "source": spec.get("source", "builtin"),
        "version": spec.get("version", "1.0.0"),
        "permissions": list(spec.get("permissions", [])),
    }


def _merge_remote(cards: list[dict[str, Any]], query: str,
                  source: str) -> list[dict[str, Any]]:
    """best-effort 拉取远程市场并叠加到种子上（去重按 id，种子优先保留）。

    这里刻意不引入新依赖：用 httpx（已装）短超时探一次 Smithery 注册表；拉不到
    就原样返回种子。任何异常由调用方吞掉。
    """
    if source and source not in ("smithery",):
        return cards  # 目前只有 Smithery 有公开注册表可拉

    import httpx

    seen = {c["id"] for c in cards}
    out = list(cards)
    try:
        resp = httpx.get(
            "https://registry.smithery.ai/servers",
            params={"q": query} if query else None,
            timeout=_FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return cards  # 静默回退到种子

    for item in (data.get("servers") or data.get("results") or [])[:30]:
        sid = item.get("qualifiedName") or item.get("name") or item.get("id")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        out.append({
            "id": sid,
            "name": item.get("displayName") or item.get("name") or sid,
            "description": item.get("description", ""),
            "source": "smithery",
            "version": item.get("version", "latest"),
            "permissions": list(item.get("permissions", [])),
        })
    return out


# ── 安装 ────────────────────────────────────────────────────────────────────
async def install(skill_id: str, source: str) -> dict[str, Any]:
    """拉取 SKILL.md、校验必填、pin 版本、落库，返回已装元数据。

    - Agent Skills（anthropic/builtin）：解析出 name/description/body。
    - MCP（smithery）：转成一条 ``mcp`` mount 建议（供前端写入 manifest.mcp）。

    ``name`` 或 ``description`` 缺失时抛 :class:`ValueError`（契约要求）。
    """
    spec = await _fetch_skill(skill_id, source)

    name = (spec.get("name") or "").strip()
    description = (spec.get("description") or "").strip()
    if not name or not description:
        raise ValueError(f"技能 {skill_id!r} 缺少必填字段 name/description，安装被拒绝。")

    src = spec.get("source", source or "builtin")
    installed: dict[str, Any] = {
        "id": skill_id,
        "name": name,
        "description": description,
        "source": src,
        # pin 版本：把 "latest" 等浮动标签固化为安装当时的具体值。
        "version": _pin_version(spec.get("version", "1.0.0")),
        "permissions": list(spec.get("permissions", [])),
        "when_to_use": (spec.get("when_to_use") or description).strip(),
        "installed_at": db.now(),
    }
    # Agent Skills：存技能体（渐进式披露，activate 时才读）。
    if spec.get("body"):
        installed["body"] = spec["body"]
    # MCP：附一条挂载建议，前端据此写入 manifest.mcp。
    if spec.get("mount"):
        installed["mount"] = spec["mount"]

    store = db.kv_get(_KV_INSTALLED, {}) or {}
    store[skill_id] = installed
    db.kv_set(_KV_INSTALLED, store)
    db.audit("decision", ticket_id=None, actor="human",
             detail={"skill_installed": {"id": skill_id, "source": src,
                                         "version": installed["version"]}})
    return installed


async def _fetch_skill(skill_id: str, source: str) -> dict[str, Any]:
    """取一份完整技能 spec。种子命中直接返回；否则 best-effort 远程拉 SKILL.md。

    远程失败时，若种子里有同 id 的条目仍回退到它，否则抛 ValueError。
    """
    seed = _SEED_BY_ID.get(skill_id)
    if seed is not None:
        return seed

    # 非种子：best-effort 远程拉取。MCP 市场条目本身没有 SKILL.md，转成 mount。
    if source == "smithery":
        remote = await _fetch_remote_mcp(skill_id)
        if remote is not None:
            return remote
        raise ValueError(f"无法从 Smithery 拉取 {skill_id!r}（不可达或不存在）。")

    remote = await _fetch_skill_md(skill_id)
    if remote is not None:
        return remote
    raise ValueError(f"未找到技能 {skill_id!r}（source={source!r}），且无法远程拉取。")


async def _fetch_skill_md(skill_id: str) -> dict[str, Any] | None:
    """best-effort 拉取一份 Agent Skills 的 SKILL.md 并解析出 name/description/body。

    约定 skill_id 可为 ``owner/repo`` 形式，尝试从其 raw SKILL.md 读取。任何失败
    返回 None（由调用方决定回退还是报错）。
    """
    if "/" not in skill_id:
        return None
    import httpx

    candidates = [
        f"https://raw.githubusercontent.com/{skill_id}/main/SKILL.md",
        f"https://raw.githubusercontent.com/{skill_id}/master/SKILL.md",
    ]
    for url in candidates:
        try:
            resp = httpx.get(url, timeout=_FETCH_TIMEOUT,
                             follow_redirects=True)
            if resp.status_code != 200 or not resp.text.strip():
                continue
            return _parse_skill_md(skill_id, resp.text)
        except Exception:
            continue
    return None


async def _fetch_remote_mcp(skill_id: str) -> dict[str, Any] | None:
    """best-effort 查 Smithery 注册表，把一个 MCP server 转成可安装 spec。"""
    import httpx

    try:
        resp = httpx.get(
            f"https://registry.smithery.ai/servers/{skill_id}",
            timeout=_FETCH_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        item = resp.json()
    except Exception:
        return None

    server = (item.get("qualifiedName") or item.get("name")
              or skill_id).split("/")[-1]
    return {
        "id": skill_id,
        "name": item.get("displayName") or item.get("name") or skill_id,
        "description": item.get("description", ""),
        "source": "smithery",
        "version": item.get("version", "latest"),
        "permissions": list(item.get("permissions", [])),
        "mount": {
            "server": server,
            "transport": "stdio",
            "command": f"npx -y {skill_id}",
            "tools": ["*"],
        },
    }


def _parse_skill_md(skill_id: str, text: str) -> dict[str, Any]:
    """解析 Agent Skills 的 SKILL.md：YAML frontmatter（name/description）+ 正文 body。

    不引 YAML 依赖，只按 ``key: value`` 行手解 frontmatter（Agent Skills 的
    frontmatter 都是扁平字符串字段，够用）。
    """
    meta: dict[str, str] = {}
    body = text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if m:
        front, body = m.group(1), m.group(2)
        for line in front.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip().lower()] = v.strip().strip("'\"")
    return {
        "id": skill_id,
        "name": meta.get("name", skill_id.split("/")[-1]),
        "description": meta.get("description", ""),
        "source": "anthropic",
        "version": meta.get("version", "1.0.0"),
        "permissions": [p.strip() for p in meta.get("permissions", "").split(",")
                        if p.strip()],
        "when_to_use": meta.get("when_to_use") or meta.get("description", ""),
        "body": body.strip(),
    }


def _pin_version(version: str) -> str:
    """把浮动标签 pin 成具体值：``latest``/空 → 带时间戳的快照标签。"""
    v = (version or "").strip()
    if not v or v.lower() in ("latest", "*", "main", "master"):
        return f"pinned-{int(db.now())}"
    return v


# ── 已装技能：增删查 ────────────────────────────────────────────────────────
def list_installed() -> list[dict[str, Any]]:
    """所有已装技能的元数据（不含 body，列表页不需要技能体）。"""
    store = db.kv_get(_KV_INSTALLED, {}) or {}
    out = []
    for meta in store.values():
        m = dict(meta)
        m.pop("body", None)
        out.append(m)
    out.sort(key=lambda m: m.get("installed_at", 0))
    return out


def get_installed(skill_id: str) -> dict[str, Any] | None:
    """按 id 取一条已装技能的完整元数据（含 body / mount），未装返回 None。"""
    store = db.kv_get(_KV_INSTALLED, {}) or {}
    meta = store.get(skill_id)
    return dict(meta) if meta else None


def uninstall(skill_id: str) -> None:
    """卸载一个技能（从 db.kv "installed_skills" 移除）。不存在则静默。"""
    store = db.kv_get(_KV_INSTALLED, {}) or {}
    if skill_id in store:
        store.pop(skill_id, None)
        db.kv_set(_KV_INSTALLED, store)
        db.audit("decision", ticket_id=None, actor="human",
                 detail={"skill_uninstalled": {"id": skill_id}})


# ── 渐进式披露：注入 system prompt 的技能索引 ───────────────────────────────
def system_index(skills: list[dict[str, Any]]) -> str:
    """把 ``manifest.skills``（已装技能的引用）渲染成"可用技能"索引文本。

    只披露 *name + when_to_use*（渐进式披露的第一层）——完整技能体由模型在真正
    需要时通过 :func:`body` 拉取，避免一次性把所有细节塞进上下文。

    ``skills`` 每项形如 ``{id, source, version}``（manifest.skills 结构）。返回空
    串表示没有可注入的技能（调用方可据此决定是否插这一段）。
    """
    if not skills:
        return ""
    lines: list[str] = []
    for ref in skills:
        sid = ref.get("id") if isinstance(ref, dict) else ref
        if not sid:
            continue
        meta = get_installed(sid)
        if not meta:
            continue
        # MCP 类技能是通过工具生效的，不进"技能索引"（避免误导模型去 activate）。
        if meta.get("mount") and not meta.get("body"):
            continue
        when = (meta.get("when_to_use") or meta.get("description") or "").strip()
        name = meta.get("name", sid)
        lines.append(f"- {name}（id: {sid}）：{when}" if when
                     else f"- {name}（id: {sid}）")
    if not lines:
        return ""
    return (
        "## 可用技能（渐进式披露）\n"
        "下列技能已为你装配；仅在命中其适用场景时才激活并按其技能体行事：\n"
        + "\n".join(lines)
    )


def body(skill_id: str) -> str:
    """完整技能体（activate 时才用）。未装或该技能无 body 时返回空串。"""
    meta = get_installed(skill_id)
    if not meta:
        return ""
    return meta.get("body", "") or ""
