# 多 Agent Coding 协作平台 · 作战群控制台

> 一名人类操作员监督一支 AI agent 队伍，以 **工单 → 作战群 → 验收上线** 的方式自治完成开发与运维任务；人类作为升级接收点与审批权。

这是依据 `01-需求文档-PRD.md` 与 `02-技术架构设计文档.md` 落地的**可运行 MVP**。它把文档强调的"难点不在 agent，在外面那层骨架"做成了真实代码：持久编排、人类介入、验收治理、记忆与密钥姿态、可插拔执行器——而 agent 的"干活"用一个**内置 mock 执行器**模拟，使整个平台无需任何外部 CLI / API Key 即可端到端跑通。Mock 执行器操作**真实的 git worktree 并产生真实 commit/diff**，因为架构里可恢复的事实是 commit，而非 agent 的内存推理。

> 界面：左栏工单列表（升级/待审置顶）· 中栏作战群消息流（agent 协作 + 决策卡片）· 右栏上下文面板（roster 实时状态 / 验收门进度 / 契约规则 / 预算 / 记忆 grep）。顶栏可切到「指标」与「配置」。

---

> **真实 LLM 模式**：agent 由真实 Claude（`claude-opus-4-8`）驱动，回答经 **SSE 流式**实时返回。每个 agent 有按**项目**绑定的**永久记忆**；项目 = git 仓库（克隆）+ 需求文档。在「项目」页配置，在「工作台 → 新建工单/拉群」选项目并勾选要拉进群的 agent。

## 快速开始

```bash
# 1. 创建虚拟环境并安装依赖（首次）
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt # macOS/Linux

# 2. 配置 API Key（真实 LLM 必需）
cp .env.example .env      # 然后编辑 .env 填入 ANTHROPIC_API_KEY

# 3. 启动
bash run.sh          # 或见下方手动命令
```

未配置 key 时平台仍可运行：agent 会回一句「请配置 ANTHROPIC_API_KEY」的占位，配好 key 重启即获得真实流式回答。

### 使用流程（真实协作）
1. **项目** 页 → 新建项目：填 git URL（克隆代码）+ 需求文档；为每个 agent 编辑「永久记忆」（注入其系统提示词，按项目永久生效）。
2. **工作台** → 新建工单/拉群：选项目、工单类型，勾选要拉进群的 agent。项目经理会用真实 LLM 开场。
3. 在群里发言 → 引擎路由到合适的 agent → 该 agent 结合项目记忆 + 代码库上下文 **流式**真实回答。

手动启动：

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8777
```

浏览器打开 <http://127.0.0.1:8777> 。控制台初次加载即有 4 个不同状态的种子工单（等你审批 / 等你回复 / 进行中 / 已完成）。

> 重置数据：删除 `data/warroom.db*` 后重启即可重新播种。

---

## 它做了什么（按 PRD/架构映射）

| 能力 | 实现 | 对应 |
|---|---|---|
| **分诊：快车道 / 作战群** | 按工单类型模板判定 lane；小工单单 agent 直跑，大工单建群签契约 | FR-2 / arch §2 |
| **协作契约** | coordinator 草拟机器可读契约（roster/routing/escalation/gates），发群**待人审批准**后生效，引擎按表路由 | FR-3 / arch 3.4 |
| **多 agent 协作与路由** | 每个子 agent 只拿"它那一片"指令；按契约 routing 流转；非自身职责按路由转交 | FR-4 / arch 3.2 |
| **升级 + 外部卡死检测** | 子 agent 主动升级 → 人；**不只靠举手**：后台 `stuck_monitor` 按无进展超时 / 连续门失败 / 预算耗尽判定升级 | FR-5 / arch 3.10 |
| **验收门（完成的判定）** | 分层短路 快门→测试门→策略门→人审门；前层全绿才进后层；**完成 = 当前 HEAD 上所有必需门=pass**，HEAD 变更使旧结果失效 | FR-6 / arch 3.5 |
| **防作弊** | 门命令来自版本化模板（不接受 agent 临时传入）；覆盖率/测试数**只升不降**（ratchet）；策略门扫密钥/新增 skip | FR-6.4 |
| **人类审批** | 仅在自动门全绿且动作落在 gates 集合时触发；"一眼可批"决策包（diff 摘要 + 绿门证据 + 动作 + 回滚预案）；approve/reject/补充 | FR-7 / arch 3.8 |
| **记忆子系统** | 五作用域目录即索引（channels<agents<projects<history<permanent）；检索 = ripgrep 优先、跨平台降级、本地高级 grep；永久层主键精确取；写永久/项目层**propose→人审→落库**（防投毒）；关单**蒸馏**入记忆 | FR-8 / arch 3.6 |
| **执行器抽象（可插拔）** | `Executor` 协议：任务信封进 / 结构化结果 + 归一化事件出；内置 mock 实现，Claude Code / OpenCode 可同契约接入；治理始终在平台层 | FR-9 / arch 3.3 |
| **集成与上报闭环** | `/api/webhook/alert` 上报建单，**告警去重**（fingerprint）；上报工单标记**不可信** | FR-1 / FR-10 |
| **可观测与平台自指标** | 全链路 `audit` 表可回放（决策/路由/门/升级/审批/记忆）；`/api/metrics` 出成功率、升级率、门通过、人审负载 | FR-11 |
| **前端控制台** | 三栏：工单列表（升级/待审置顶）· 作战群消息流 · 上下文面板（roster live / 验收门 / 契约规则 / 预算 / 记忆检索）；归一化事件经 WebSocket 实时驱动 | FR-12 |
| **成本与预算** | 每工单硬预算（token/费用/步数）+ 计费累加 + 熔断升级 | NFR-3 |
| **密钥姿态** | 凭证为 broker 句柄、绝不进 instruction/上下文/日志；仓库页明示"密钥托管、运行时注入" | NFR-1 / arch 3.7 |

---

## 架构

```
┌──────────────────────────────────────────────────────────────┐
│  前端    web/  三栏控制台 + WebSocket 实时事件                  │
├──────────────────────────────────────────────────────────────┤
│  API     backend/app/main.py   REST + /ws                      │
├──────────────────────────────────────────────────────────────┤
│  编排     engine.py  分诊·契约·路由·门编排·升级·卡死检测·生命周期 │
│  执行     executors/ Executor 协议 + 内置 mock（真实 git）       │
│  治理     gates.py 验收门 · memory.py 记忆 · db.py 审计/持久化    │
├──────────────────────────────────────────────────────────────┤
│  持久     SQLite（WAL）= Temporal 的可恢复脊柱替身              │
└──────────────────────────────────────────────────────────────┘
```

**为什么用 SQLite 替代 Temporal**：MVP 优先在窄任务上证明价值（PRD §8）。SQLite 提供持久、崩溃可恢复、可无限期挂起等人的状态机，足以演示编排骨架；生产化时 `engine.py` 的工作流入口可平移到 Temporal workflow，执行器调用入 Activity，确定性边界不变（事实落 git）。

### 后端模块

| 文件 | 职责 |
|---|---|
| `config.py` | 路径、记忆作用域、卡死/预算/仿真 阈值 |
| `db.py` | SQLite schema、KV 配置、审计日志 |
| `events.py` | 归一化 AgentEvent 总线 + WebSocket 广播 + 跨线程 `spawn` |
| `executors/base.py` | `Executor` 协议、`TaskEnvelope`、`ExecResult`、`CapProfile` |
| `executors/mock.py` | 内置 mock 执行器（真实 worktree/commit，发全量归一化事件） |
| `memory.py` | 五作用域 grep 记忆：`recall / lookup / remember`（含写治理） |
| `gates.py` | 验收门流水线 + 防作弊 ratchet |
| `engine.py` | 编排引擎：triage / contract / dispatch / gate / escalate / 卡死检测 |
| `seed.py` | 默认配置 + 种子工单 |
| `main.py` | FastAPI：REST + WebSocket + 静态控制台 |

---

## API 速览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/tickets` · `/api/tickets/{id}` | 工单列表 / 详情 |
| POST | `/api/tickets` | 新建工单（自动分诊） |
| POST | `/api/tickets/{id}/messages` | 群内发言（blocked 时即为回复升级） |
| POST | `/api/tickets/{id}/approve-contract` | 批准协作契约 |
| POST | `/api/tickets/{id}/approve` · `/reject` | 上线审批 / 退回 |
| POST | `/api/tickets/{id}/answer` | 回填升级问题 |
| POST | `/api/webhook/alert` | 上报建单（带去重） |
| GET/PUT | `/api/config` | 平台配置 |
| GET | `/api/memory/recall?q=` | grep 记忆检索 |
| GET/POST | `/api/memory/proposals` `…/{id}/approve` | 记忆写入提案与人审 |
| GET | `/api/audit` · `/api/metrics` | 审计回放 / 平台自指标 |
| WS | `/ws` | 归一化事件流 |

---

## 接入真实执行器（下一步）

`executors/base.py` 的 `Executor` 协议即接入点。新增 `executors/claude_code.py`：

```python
class ClaudeCodeExecutor:
    name = "claude-code"
    caps = CapProfile(streams_events=True, mcp=True, hooks=True, ...)
    async def run(self, env: TaskEnvelope, emit) -> ExecResult:
        # claude -p --output-format stream-json --allowedTools ...
        # 解析 stream-json → emit("tool_call"/"message"/...)
        # 结果取 git diff + 退出码
```

在 `executors/mock.build_registry()` 把对应名称指向新实现即可——治理（路由/门/密钥/人审/审计）无需改动，安全姿态不随执行器改变（FR-9.5）。

---

## 适用区间（诚实记录）

**适合**：迁移/重构、界定清晰的 bug、明确规格的中小特性、可并行独立子任务。
**不适合（回退到人/快车道）**：架构开放性决策、规格高度模糊、强跨模块耦合、安全关键变更的最终拍板。

平台对自身出指标，用成功率 / 否决率 / 漏出回归 / 单工单成本决定**是否扩大放权**。

# AI-OPC
