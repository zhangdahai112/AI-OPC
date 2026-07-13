"""Read-only fan-out investigation sub-agent — the "Explore" pattern.

An agent can call the ``explore`` tool to delegate a broad, cross-layer code
investigation to a dedicated read-only sub-agent. The sub-agent sweeps the whole
repo (frontend + backend + docs) under the investigation methodology, then
returns a synthesized, file-grounded conclusion — so the caller gets the answer
without pulling every file into its own context, and without converging on the
first path it happens to find.

The sub-agent only holds read-only tools (repo_map / list_dir / read_file / grep)
and cannot call ``explore`` itself, so there is no recursion or write surface.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from . import llm, projects, tools

# read-only surface for the explorer (deliberately excludes explore/write/run)
_READONLY = {"list_dir", "read_file", "grep", "find_symbol", "repo_map"}

OnSubTool = Callable[[str, dict, str], Awaitable[None]]

EXPLORER_SYSTEM = (
    "你是一个只读的代码探查子代理（类似 Explore）。你的唯一任务：针对给定问题，"
    "横扫整个仓库把答案调查清楚，然后给出一份带文件定位（file:line 或 文件+函数名）的综合结论。\n"
    "你有只读工具：repo_map / list_dir / read_file / grep / find_symbol。"
    "你不能改文件、不能跑命令、不能再派子代理。\n"
    "\n【追链而非概览——核心工作方式】\n"
    "别只 grep 一遍关键词就下结论。要顺着调用链一步步追：从入口（路由/接口）出发，"
    "用 **find_symbol(名字)** 跳到被调函数的定义、并看它被谁调用，逐跳 read 打开，"
    "一直追到真正的实现——**包括异步/队列那一跳**（提交 → celery task/poll → 写结果 → "
    "序列化返回）。加解密/改写常发生在**序列化/返回层**（response_model、`*_out()`、"
    "schema 的 model_validator），必须追到那里，不能停在路由或 service 中间。\n"
    "\n【调查方法论——必须遵守】\n"
    "1. 先复述问题的真实意图并主动放宽范围：能力常不在名字最直白的那条路径上"
    "（例如「图片上传怎么加密」真正的加解密可能在输出/下发/前端，而不在上传函数里）。\n"
    "2. 动手前先枚举跨层候选位置再逐一排查：前端(src/utils、components)、后端接口(api/openapi)、"
    "service、utils、模型、docs 设计文档、配置(.env/nginx)。别只盯一个目录。\n"
    "3. 按「能力的词汇」用正则多关键词搜，而不是问题里的名词："
    "查加解密就搜 encrypt|decrypt|加密|解密|AES|cipher|bng|防盗链|sign|secure_link|crypto。\n"
    "4. 数据覆盖两个方向：输入/上传 与 输出/下发/serve/前端渲染。\n"
    "5. 下关键结论前先扫 docs/README/设计文档，那里常直接写着方案入口。\n"
    "6. 一条路径没有 X ≠ 整个系统没有 X：下否定结论前必须已覆盖上面所有层，"
    "否则只能说「在我查过的范围内没有」。\n"
    "\n【省 token 纪律】工具调用要吝啬：先 grep 定位、再只读必要文件一次；"
    "不要重复读同一个文件、不要重复搜同一个词；拿到足够信息就停手给结论。\n"
    "\n【输出格式】用中文，简洁结构化：① 一句话结论 ② 涉及文件与关键函数(带路径) "
    "③ 数据流/机制说明 ④ 若存在多套子系统务必区分。不要贴大段原始 grep 输出。"
)

# read-only sub-agent gets a tighter tool budget than a top-level agent — a
# focused investigation rarely needs more, and this caps its token cost.
_EXPLORE_MAX_ITERS = 16


async def explore(question: str, ctx: "tools.ToolContext", project_ids: list[str],
                  *, role: str | None = None, on_subtool: OnSubTool | None = None,
                  max_tokens: int = 2200) -> str:
    """Run the read-only explorer on ``question`` against ``ctx``'s checkout(s)
    and return its synthesized conclusion. ``on_subtool`` (if given) is awaited
    for every internal tool call so the operator can watch the sweep live."""
    if not (question or "").strip():
        return "（explore 缺少 question 参数）"
    if not ctx.has_repo:
        return "（无可用项目仓库，无法探查）"

    # ground the explorer in the repo (file tree + surfaced design-doc leads)
    grounding = ""
    if project_ids:
        grounding = projects.repo_context(project_ids[0], role=role) or ""
    system = EXPLORER_SYSTEM + (f"\n\n【仓库背景】\n{grounding}" if grounding else "")

    specs = tools.tool_specs(allow=_READONLY)

    # within-sweep dedup: the explorer often re-reads the same file — short-circuit
    # exact repeats (all its tools are read-only, so all are safe to cache).
    seen: dict[str, bool] = {}

    async def sub_on_tool(name: str, args: dict, uid: str) -> str:
        if on_subtool:
            try:
                await on_subtool(name, args, uid)
            except Exception:
                pass  # surfacing is best-effort, never break the sweep
        import json
        try:
            key = name + ":" + json.dumps(args, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            key = name + ":" + repr(args)
        if key in seen:
            return (f"（重复调用：已用相同参数执行过 {name}，为省 token 未再执行，"
                    "请基于已获得信息继续。）")
        seen[key] = True
        return await asyncio.to_thread(tools.execute, name, args, ctx)

    async def _noop(_text: str) -> None:
        return None

    msgs = [{"role": "user", "content": f"要调查的问题：{question}"}]
    res = await llm.stream_reply(system, msgs, _noop, tools=specs,
                                 on_tool=sub_on_tool, max_tokens=max_tokens,
                                 max_iters=_EXPLORE_MAX_ITERS)
    return (res.get("text") or "").strip() or "（探查未产出结论）"
