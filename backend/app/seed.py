"""Seed the platform so the console is alive on first load.

Writes the default platform config (mirrors the prototype's config panel) and a
handful of tickets in representative states (awaiting approval, blocked, working,
done) so every UI path is visible immediately. Idempotent: only seeds an empty DB.
"""
from __future__ import annotations

from . import db, gates, memory


DEFAULT_CONFIG = {
    "agents": [
        {"role": "coordinator", "on": True, "exec": "内置 Agent SDK"},
        {"role": "analyst", "on": True, "exec": "Claude Code"},
        {"role": "developer", "on": True, "exec": "Claude Code"},
        {"role": "tester", "on": True, "exec": "OpenCode"},
        {"role": "devops", "on": True, "exec": "内置 Agent SDK"},
        {"role": "reporter", "on": True, "exec": "内置 Agent SDK"},
    ],
    "templates": [
        {"type": "bug", "lane": "快车道", "roster": ["developer", "tester"]},
        {"type": "feature", "lane": "作战群",
         "roster": ["coordinator", "analyst", "developer", "tester", "devops"]},
        {"type": "incident", "lane": "作战群",
         "roster": ["coordinator", "developer", "devops"]},
    ],
    "gates": [
        {"name": "快速检查", "id": "quick", "desc": "代码规范 · 构建 · 类型检查", "on": True},
        {"name": "测试", "id": "test", "desc": "单元 + 集成测试 · 覆盖率门槛", "on": True, "thr": 80},
        {"name": "策略检查", "id": "policy", "desc": "改动评审 · 数据库迁移安全 · 密钥扫描", "on": True},
        {"name": "人工审批", "id": "human", "desc": "上线等敏感动作前由人确认", "on": True},
    ],
    "approve": [
        {"name": "上线到生产环境", "on": True}, {"name": "数据库变更", "on": True},
        {"name": "删除数据", "on": True}, {"name": "修改权限", "on": True},
        {"name": "使用敏感凭证", "on": True},
    ],
    "skills": [
        {"name": "开发规约", "desc": "分支命名 · 提交格式 · 代码风格", "on": True,
         "roles": ["developer"], "ver": "v3"},
        {"name": "测试规约", "desc": "覆盖率门槛 · 用例命名", "on": True,
         "roles": ["tester"], "ver": "v2"},
        {"name": "前端设计规约", "desc": "组件模式 · 设计 token · 可访问性", "on": True,
         "roles": ["developer"], "ver": "v1"},
        {"name": "部署模式", "desc": "蓝绿 / 金丝雀 · 回滚步骤", "on": True,
         "roles": ["devops"], "ver": "v2"},
        {"name": "数据库变更", "desc": "只进不退 · 加索引 · 数据回填", "on": True,
         "roles": ["developer", "devops"], "ver": "v4"},
    ],
    "repos": [
        {"name": "export-svc", "url": "git@github.com:acme/export-svc", "branch": "main"},
        {"name": "web-admin", "url": "git@github.com:acme/web-admin", "branch": "main"},
    ],
    "integrations": [
        {"name": "GitHub", "on": True},
        {"name": "持续集成(GitHub Actions)", "on": True},
        {"name": "监控(Sentry)", "on": True},
        {"name": "群聊(飞书)", "on": True},
    ],
    "budget": {"tokens": 200_000, "cost": 5, "steps": 40, "autoLow": True},

    # LLM providers — stored in config so the frontend can manage them, API keys
    # stay in environment variables only.
    "llm": {
        "active_provider": "anthropic-1",
        "providers": [
            {
                "id": "anthropic-1",
                "name": "Anthropic Claude",
                "type": "anthropic",
                "api_key_env": "ANTHROPIC_API_KEY",
                "api_key": "",
                "base_url": "",
                "model": "claude-sonnet-4-6",
                "enabled": True,
                "max_tokens": 4096,
                "effort": "medium",
            },
            {
                "id": "openai-1",
                "name": "OpenAI 兼容",
                "type": "openai",
                "api_key_env": "OPENAI_API_KEY",
                "api_key": "",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "enabled": False,
                "max_tokens": 4096,
                "effort": "",
            },
            {
                "id": "deepseek-1",
                "name": "DeepSeek",
                "type": "openai",
                "api_key_env": "DEEPSEEK_API_KEY",
                "api_key": "",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "enabled": False,
                "max_tokens": 4096,
                "effort": "",
            },
        ],
    },
}


def _ticket(tid, title, ttype, source, lane, status, needs, roster, pipeline,
            contract=None, trusted=1):
    db.execute(
        "INSERT INTO tickets(id,title,type,description,repo,source,lane,status,"
        "needs,trusted,contract,pipeline,budget,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, title, ttype, "", "export-svc", source, lane, status,
         needs, trusted, db.dumps(contract) if contract else None,
         db.dumps({"steps": pipeline}),
         db.dumps({"max_tokens": 200000, "max_cost_usd": 5, "max_steps": 40,
                   "timeout_sec": 1800, "spent_tokens": 42000, "spent_cost": 0.63,
                   "steps": 18}),
         db.now(), db.now()))
    for role, state in roster:
        db.execute("INSERT INTO roster(ticket_id,role,state) VALUES(?,?,?)",
                   (tid, role, state))


def _msgs(tid, msgs):
    for m in msgs:
        kind = m.pop("kind")
        role = m.pop("role", None)
        db.execute(
            "INSERT INTO messages(ticket_id,kind,role,payload,created_at) "
            "VALUES(?,?,?,?,?)",
            (tid, kind, role, db.dumps(m), db.now()))


def _pipe(states):
    base = gates.initial_pipeline()
    for s, st in zip(base, states):
        s["state"] = st
    return base


def seed_if_empty() -> None:
    if db.kv_get("config") is None:
        db.kv_set("config", DEFAULT_CONFIG)
    if db.kv_get("seeded", False):
        return

    db.kv_set("ticket_seq", 1001)
    # A starter project so the operator can immediately create a real war-room.
    # No mock tickets — conversations are real (streamed from Claude).
    from . import projects
    projects.create_project(
        name="示例项目",
        docs=("这是一个示例项目。把它换成你自己的：在「项目」页填入 git 仓库 URL 克隆代码，"
              "粘贴需求文档，并为每个 agent 编辑「永久记忆」。然后在工作台「新建工单 / 拉群」，"
              "选项目、勾选要拉进群的 agent，agent 会用真实 LLM 流式回答。"))

    db.kv_set("seeded", True)
    return


def _seed_demo_tickets_disabled() -> None:
    db.kv_set("ticket_seq", 1052)

    # T-1042 incident, awaiting approval
    _ticket("T-1042", "导出接口超时，大客户批量导出报 504", "incident", "reporter",
            "warroom", "awaiting_approval", 1,
            [("coordinator", "working"), ("analyst", "done"), ("developer", "working"),
             ("tester", "done"), ("devops", "idle")],
            _pipe(["pass", "pass", "pass", "pending"]), trusted=0)
    _msgs("T-1042", [
        {"kind": "sys", "text": "上报 agent 建群，拉入 5 个 agent"},
        {"kind": "agent", "role": "reporter",
         "html": "监控发现导出接口超时(504 占比 18%)，已建工单并拉群。"},
        {"kind": "card", "card": "contract"},
        {"kind": "card", "card": "handoff", "from": "analyst", "to": "developer",
         "pt": "root_cause", "note": "导出没分页，全表加载导致超时"},
        {"kind": "agent", "role": "developer",
         "html": "改成分页流式导出 + 加索引，本地把响应从 6 秒降到 0.4 秒，已提交代码。"},
        {"kind": "card", "card": "gate"},
        {"kind": "card", "card": "approval"},
    ])

    # T-1051 feature, blocked (escalation)
    _ticket("T-1051", "后台新增「批量导出报表」入口", "feature", "human",
            "warroom", "blocked", 1,
            [("coordinator", "working"), ("analyst", "escalated"),
             ("developer", "idle"), ("tester", "idle"), ("devops", "idle")],
            _pipe(["pending", "pending", "pending", "pending"]))
    _msgs("T-1051", [
        {"kind": "sys", "text": "你创建了工单，拉入 5 个 agent"},
        {"kind": "agent", "role": "coordinator", "html": "先让分析把需求补全，再开始开发。"},
        {"kind": "card", "card": "escalation", "from": "analyst",
         "pt": "requirement_ambiguity",
         "q": "导出格式用 CSV 还是 Excel？要不要支持自己选导出哪些列？这会影响工作量。"},
    ])

    # T-1043 bug, working (fast lane)
    _ticket("T-1043", "登录页在 Safari 下样式错位", "bug", "human",
            "fast", "working", 0,
            [("developer", "working"), ("tester", "working")],
            _pipe(["pass", "running", "pending", "pending"]))
    _msgs("T-1043", [
        {"kind": "sys", "text": "你创建了工单，拉入 2 个 agent(小工单走快车道)"},
        {"kind": "agent", "role": "developer",
         "html": "定位到旧版 Safari 的兼容问题，已修复，正在跑测试。"},
        {"kind": "card", "card": "gate-running"},
    ])

    # T-1037 feature, done
    _ticket("T-1037", "订单列表加导出按钮", "feature", "human",
            "warroom", "done", 0,
            [("developer", "done"), ("tester", "done"), ("devops", "done")],
            _pipe(["pass", "pass", "pass", "pass"]))
    _msgs("T-1037", [
        {"kind": "sys", "text": "工单已关闭并归档"},
        {"kind": "agent", "role": "devops", "html": "已灰度到 100%，全量上线完成。"},
        {"kind": "card", "card": "closed"},
    ])

    # a couple of seeded project memories so recall returns something real
    memory._write("projects", "export-timeout",
                  "导出超时以前回滚过一次，后来改用分页 + 加索引解决。根因常是全表加载。")
    memory._write("permanent", "export-svc",
                  "repo: git@github.com:acme/export-svc, branch main, runbook: 灰度 10%→100%")

    db.kv_set("seeded", True)
