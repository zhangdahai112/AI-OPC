"""Built-in generation skills — curated "market-grade" expert prompts that help
operators draft each kind of project document or agent config.

Design
------
Every generatable surface in the platform (the project requirement doc, and the
per-agent permanent memory / charter) gets a dedicated *Skill*. A Skill is a
small **Strategy**: it owns an expert system prompt plus a user-prompt builder,
and knows which field (``target``) it drafts. Skills live in a **Registry**
(:data:`SKILLS`) keyed by id; role charters are produced by a **Factory**
(:func:`_charter_skill`) from a single role-expertise table, so adding a new
document type is a one-line change here and nothing else.

Generation grounds every draft in the real project context (name, existing
docs, a shallow repo overview, and any current field content) and always returns
Markdown, which the chat/preview UI renders natively.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import llm, projects

_MAX_CTX = 2200          # chars of project context injected per source
_GEN_MAX_TOKENS = 3600   # generous budget for a full document


# ---------------------------------------------------------------------------
# Skill model + registry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Skill:
    id: str
    name: str            # zh display name
    tagline: str         # one-line "what it produces"
    icon: str
    target: str          # which field it drafts: "docs" | "memory"
    system: str          # expert system prompt (best practice, market-grade)
    role: str | None = None   # for memory skills: the agent role it specializes
    hint: str = ""       # placeholder shown in the operator's brief box

    def meta(self) -> dict:
        return {
            "id": self.id, "name": self.name, "tagline": self.tagline,
            "icon": self.icon, "target": self.target, "role": self.role,
            "hint": self.hint,
        }


_OUTPUT_RULES = (
    "\n\n输出要求：\n"
    "- 只输出成品正文，使用规范 Markdown（标题、列表、表格、代码块、加粗按需使用）。\n"
    "- 不要输出寒暄、解释你在做什么、或用 ``` 把整篇文档包起来。\n"
    "- 中文书写，术语可保留英文；内容具体、可执行，拒绝空话套话。"
)


# ---- document skills (target = requirement doc field) -------------------
_PRD_SKILL = Skill(
    id="prd",
    name="PRD 需求文档架构师",
    tagline="把一句话需求扩写成结构化、可验收的产品需求文档",
    icon="📄",
    target="docs",
    hint="用一两句话描述你要做的功能 / 要解决的问题…",
    system=(
        "你是世界顶级的产品需求专家，擅长把模糊的想法收敛成清晰、无歧义、"
        "可直接开发与验收的 PRD。请产出一份结构完整的产品需求文档，至少包含：\n"
        "1. **背景与目标**（要解决什么问题、成功指标 / 北极星）\n"
        "2. **目标用户与场景**（用户画像、关键使用场景）\n"
        "3. **功能需求**（用户故事 + 按 MoSCoW 分级：Must/Should/Could/Won't）\n"
        "4. **非功能需求**（性能、可用性、安全、兼容、可观测性）\n"
        "5. **交互与流程**（关键流程用有序列表或简单流程描述）\n"
        "6. **验收标准**（每条 Must 都要有可测的 Given/When/Then 验收点）\n"
        "7. **边界与非目标**、**风险与依赖**、**里程碑建议**\n"
        "主动补全合理的默认假设并显式标注『假设：…』。" + _OUTPUT_RULES
    ),
)

_TECHSPEC_SKILL = Skill(
    id="techspec",
    name="技术方案设计专家",
    tagline="面向落地的系统设计 / 技术方案（架构、接口、数据、风险）",
    icon="🏗️",
    target="docs",
    hint="描述要设计的系统 / 模块，或粘贴需求要点…",
    system=(
        "你是资深系统架构师，擅长写出既有全局观又能直接落地的技术方案。请产出一份"
        "技术设计文档，至少包含：\n"
        "1. **方案概述与目标**、关键设计约束\n"
        "2. **总体架构**（组件划分、职责、数据流；用文字 + 列表描述清楚）\n"
        "3. **关键技术选型**（给出取舍理由与备选对比表格）\n"
        "4. **数据模型 / 接口设计**（核心表结构或 API 契约，可用代码块）\n"
        "5. **关键流程与时序**（有序步骤）\n"
        "6. **非功能设计**（性能、扩展、容错、灰度、回滚、可观测）\n"
        "7. **风险与缓解**、**分阶段实施计划**\n"
        "在有多种可行方案时给出推荐并说明理由。" + _OUTPUT_RULES
    ),
)


# ---- agent-charter skills (target = per-role permanent memory) ----------
# role -> (zh name, icon, one-line expertise focus)
_ROLE_EXPERTISE: dict[str, tuple[str, str, str]] = {
    "coordinator": ("项目经理", "🎯",
                    "统筹拆解需求、按职责分派、汇总进度、判断路由与处理升级"),
    "analyst": ("需求分析", "🔍",
                "澄清模糊需求、消除歧义、产出可验收的规格与验收标准"),
    "developer": ("开发工程师", "💻",
                  "阅读代码库、定位问题、给出可落地的实现方案与具体代码改动"),
    "tester": ("测试工程师", "🧪",
               "设计测试用例、覆盖边界与回归、评估质量与覆盖率"),
    "devops": ("运维工程师", "🚀",
               "负责部署、灰度、回滚、监控与线上稳定性"),
    "reporter": ("上报 / 值守", "📡",
                 "盯监控告警、归类去重异常、把问题转化成结构化工单"),
}


def _charter_skill(role: str) -> Skill:
    """Factory: build a role-specialized *agent charter* generation skill."""
    name_cn, icon, focus = _ROLE_EXPERTISE[role]
    system = (
        f"你是顶级的多智能体协作系统设计专家。请为『{name_cn}』这一 Agent 角色，"
        f"针对某个具体项目撰写一份**永久记忆 / 角色宪章**（Charter）。该文档会被"
        f"注入该 Agent 在本项目每一次回答的系统提示词，因此必须稳定、精确、可执行。\n"
        f"该角色的核心职责聚焦于：{focus}。\n\n"
        "文档结构（用 Markdown 标题）：\n"
        "## 角色定位\n"
        "## 职责边界（明确『负责什么』与『不负责什么』）\n"
        "## 标准工作流（分步骤，遇到不确定时如何处理）\n"
        "## 协作契约（与其他角色的输入/输出接口、交接物、@ 谁）\n"
        "## 输出规范（回答的格式、语气、粒度）\n"
        "## 质量红线（绝不能做的事、必须坚持的原则）\n"
        "## 本项目约定（技术栈、已知坑、历史决策——依据下方项目上下文填写）\n"
        "内容要具体到本项目，避免泛泛而谈。" + _OUTPUT_RULES
    )
    return Skill(
        id=f"charter_{role}",
        name=f"{name_cn} · 角色宪章专家",
        tagline=f"为「{name_cn}」生成项目专属的永久记忆 / 角色宪章",
        icon=icon,
        target="memory",
        role=role,
        hint=f"补充你希望「{name_cn}」在本项目特别注意的事项（可留空）…",
        system=system,
    )


# ---- the registry -------------------------------------------------------
SKILLS: dict[str, Skill] = {}


def _register(skill: Skill) -> None:
    SKILLS[skill.id] = skill


for _s in (_PRD_SKILL, _TECHSPEC_SKILL):
    _register(_s)
for _role in _ROLE_EXPERTISE:
    _register(_charter_skill(_role))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def list_skills() -> list[dict]:
    """All skill metadata (no prompts), ordered doc-skills first."""
    return [s.meta() for s in SKILLS.values()]


def get_skill(skill_id: str) -> Skill | None:
    return SKILLS.get(skill_id)


def _clip(text: str, limit: int = _MAX_CTX) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "\n…（已截断）"


def _project_context(pid: str, *, role: str | None) -> str:
    """Grounding block: project name, existing docs, repo overview, and (for a
    charter) the role's current memory so the model refines rather than resets."""
    if not pid:
        return ""
    p = projects.get_project(pid)
    if not p:
        return ""
    parts = [f"项目名称：{p['name']}"]
    if p.get("repo_url"):
        parts.append(f"代码仓库：{p['repo_url']} @ {p.get('branch', 'main')}")
    if p.get("docs"):
        parts.append("现有需求文档：\n" + _clip(p["docs"]))
    repo = projects.repo_context(pid, max_files=60)
    if repo:
        parts.append("代码库概览：\n" + _clip(repo))
    if role:
        cur = projects.get_agent_memory(pid, role)
        if cur:
            parts.append("该角色现有记忆（可在其基础上改进）：\n" + _clip(cur, 1500))
    return "\n\n".join(parts)


async def generate(*, skill_id: str, project_id: str = "",
                   role: str = "", brief: str = "") -> dict:
    """Run a skill and return ``{"skill", "text"}``. Grounds the draft in the
    project's real context; never streams (the operator gets the finished doc)."""
    skill = get_skill(skill_id)
    if not skill:
        return {"skill": skill_id, "text": "", "error": "unknown skill"}

    ctx = _project_context(project_id, role=role or skill.role)

    user_parts: list[str] = []
    if ctx:
        user_parts.append("【项目上下文】\n" + ctx)
    if brief.strip():
        user_parts.append("【操作者补充要求】\n" + brief.strip())
    if not user_parts:
        user_parts.append("暂无额外上下文，请基于该文档类型的通用最佳实践生成一份高质量样板，"
                          "并用占位符标注需要人工补充的地方。")
    user_parts.append("请直接输出成品文档。")
    user = "\n\n".join(user_parts)

    async def _sink(_t: str) -> None:  # generation is collected, not streamed
        return None

    res = await llm.stream_reply(
        skill.system,
        [{"role": "user", "content": user}],
        _sink,
        max_tokens=_GEN_MAX_TOKENS,
    )
    return {"skill": skill.id, "text": res.get("text", ""),
            "stop_reason": res.get("stop_reason")}
