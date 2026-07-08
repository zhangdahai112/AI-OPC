# AI-OPC · AI 一人公司

> 一个人，一支 AI agent 队伍，一家公司。
> 你只做**老板**该做的事——定方向、拍关键板、收结果；具体的开发与运维，交给 agent 团队自治完成。

把「招人、盯活、验收」压缩成一块屏幕：左栏是待你处理的工单，中栏是 AI 团队的作战群消息流，右栏是团队状态与验收进度。人只在两个地方出现——**关键决策**与**最终审批**，其余全程自动。

真实 LLM 驱动：每个 agent 由 Claude（`claude-opus-4-8`）驱动，回答经 **SSE 流式**实时返回。每个 agent 按**项目**绑定**永久记忆**（项目 = git 仓库 + 需求文档），越用越懂你的业务。

---

## 快速开始

```bash
# 1. 安装依赖（首次）
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt # macOS/Linux

# 2. 配置 API Key
cp .env.example .env      # 编辑 .env 填入 ANTHROPIC_API_KEY

# 3. 启动
bash run.sh
```

手动启动：

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8777
```

浏览器打开 <http://127.0.0.1:8777> 。

> 重置数据：删除 `data/warroom.db*` 后重启。

## 怎么用

1. **项目** 页 → 新建项目：填 git URL（克隆代码）+ 需求文档；给每个 agent 编辑「永久记忆」（注入系统提示词，按项目永久生效）。
2. **工作台** → 新建工单/拉群：选项目、工单类型，勾选要拉进群的 agent。项目经理自动开场。
3. 群里发言 → 引擎路由到合适的 agent → 结合项目记忆 + 代码库上下文**流式**回答。

---

## 核心能力

| 能力 | 说明 |
|---|---|
| **分诊：快车道 / 作战群** | 小工单单 agent 直跑，大工单建群签契约 |
| **协作契约** | coordinator 草拟机器可读契约（roster/路由/升级/门），人审后生效，引擎按表路由 |
| **多 agent 协作** | 每个子 agent 只拿"它那一片"指令，按契约流转，非自身职责自动转交 |
| **升级 + 卡死检测** | 子 agent 主动升级给人；后台按无进展超时 / 连续门失败 / 预算耗尽自动判定升级 |
| **验收门** | 快门→测试门→策略门→人审门分层短路；完成 = 当前 HEAD 上所有必需门全绿 |
| **防作弊** | 门命令来自版本化模板；覆盖率/测试数只升不降（ratchet）；策略门扫密钥与新增 skip |
| **人类审批** | 自动门全绿才触发；"一眼可批"决策包（diff 摘要 + 绿门证据 + 动作 + 回滚预案） |
| **记忆子系统** | 五作用域 grep 记忆；永久层写入 propose→人审→落库（防投毒）；关单蒸馏入记忆 |
| **执行器抽象** | `Executor` 协议：任务信封进、结构化结果出；Claude Code / OpenCode 可同契约接入 |
| **成本与预算** | 每工单硬预算（token/费用/步数）+ 计费累加 + 熔断升级 |
| **密钥姿态** | 凭证为 broker 句柄，绝不进 instruction/上下文/日志，运行时注入 |

---

## 架构

```
┌──────────────────────────────────────────────────────────────┐
│  前端    web/  三栏控制台 + SSE 实时事件                        │
├──────────────────────────────────────────────────────────────┤
│  API     backend/app/main.py   REST + SSE                      │
├──────────────────────────────────────────────────────────────┤
│  编排     engine.py  分诊·契约·路由·门编排·升级·卡死检测·生命周期 │
│  治理     gates.py 验收门 · memory.py 记忆 · db.py 审计/持久化    │
├──────────────────────────────────────────────────────────────┤
│  持久     SQLite（WAL）持久 · 崩溃可恢复 · 可无限期挂起等人       │
└──────────────────────────────────────────────────────────────┘
```

### 后端模块

| 文件 | 职责 |
|---|---|
| `config.py` | 路径、记忆作用域、卡死/预算阈值 |
| `db.py` | SQLite schema、KV 配置、审计日志 |
| `events.py` | 归一化 AgentEvent 总线 + 广播 + 跨线程 `spawn` |
| `llm.py` · `chat.py` | Claude 流式调用 + 路由与流式回答 |
| `projects.py` | git-clone 项目 + 按项目绑定的 agent 永久记忆 |
| `memory.py` | 五作用域 grep 记忆：`recall / lookup / remember` |
| `gates.py` | 验收门流水线 + 防作弊 ratchet |
| `engine.py` | 编排引擎：triage / contract / dispatch / gate / escalate |
| `main.py` | FastAPI：REST + SSE + 静态控制台 |

---

## API 速览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/tickets` · `/api/tickets/{id}` | 工单列表 / 详情 |
| POST | `/api/tickets` | 新建工单（自动分诊） |
| POST | `/api/tickets/{id}/messages` | 群内发言 |
| POST | `/api/tickets/{id}/approve-contract` | 批准协作契约 |
| POST | `/api/tickets/{id}/approve` · `/reject` | 上线审批 / 退回 |
| POST | `/api/tickets/{id}/answer` | 回填升级问题 |
| POST | `/api/webhook/alert` | 上报建单（带去重） |
| GET/PUT | `/api/config` | 平台配置 |
| GET | `/api/memory/recall?q=` | grep 记忆检索 |
| GET/POST | `/api/memory/proposals` `…/{id}/approve` | 记忆写入提案与人审 |
| GET | `/api/audit` · `/api/metrics` | 审计回放 / 平台自指标 |
| GET | `/api/events` | SSE 归一化事件流 |

---

## 适用区间

**适合**：迁移/重构、界定清晰的 bug、明确规格的中小特性、可并行独立子任务。
**不适合（回退到人）**：架构开放性决策、规格高度模糊、强跨模块耦合、安全关键变更的最终拍板。

平台对自身出指标，用成功率 / 否决率 / 漏出回归 / 单工单成本决定**是否扩大放权**。
