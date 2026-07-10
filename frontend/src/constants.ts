import type { AgentRole, TicketStatus, RosterState } from "./types";

export const ROLES: Record<
  AgentRole,
  { nm: string; ab: string }
> = {
  coordinator: { nm: "项目经理", ab: "PM" },
  analyst: { nm: "需求分析", ab: "AN" },
  developer: { nm: "开发", ab: "DE" },
  tester: { nm: "测试", ab: "QA" },
  devops: { nm: "运维", ab: "OP" },
  reporter: { nm: "上报", ab: "RP" },
};

export const ROLES_DESC: Record<AgentRole, string> = {
  coordinator: "纯路由与管控：拆解分派、汇总进度、处理升级",
  analyst: "把需求理清成规格",
  developer: "写代码、改 bug、提交",
  tester: "写用例、跑测试",
  devops: "部署、上线、回滚",
  reporter: "盯监控、自动建工单",
};

export const STATUS: Record<TicketStatus, string> = {
  new: "排队中",
  planning: "待批契约",
  working: "进行中",
  awaiting_approval: "等你审批",
  blocked: "等你回复",
  done: "已完成",
  rejected: "已退回",
};

export const STATE_CN: Record<RosterState, string> = {
  idle: "空闲",
  working: "工作中",
  blocked: "受阻",
  escalated: "已升级",
  done: "完成",
};

export const PT: Record<string, string> = {
  requirement_ambiguity: "需求不清楚",
  root_cause: "已定位根因",
  spec_ready: "规格已定",
  build_failure: "构建失败",
  no_progress: "无进展卡死",
  gate_stuck: "验收门反复失败",
  budget_exhausted: "预算耗尽",
};

export const TYPE_CN: Record<string, string> = {
  bug: "缺陷",
  feature: "需求",
  incident: "故障",
};

export const TEMPLATE_NAMES: Record<string, string> = {
  bug: "缺陷 / Bug",
  feature: "需求 / 功能",
  incident: "故障 / 事故",
};

export const EXECUTORS = [
  "内置 Agent SDK",
  "Claude Code",
  "OpenCode",
  "通用 CLI",
];

export const DEFAULT_ROSTER: AgentRole[] = [
  "coordinator",
  "analyst",
  "developer",
  "tester",
  "devops",
];

export const CFG_SECTIONS: [string, string, string][] = [
  ["agents", "团队成员", "👥"],
  ["templates", "工单流程", "🗂"],
  ["gates", "验收门", "✓"],
  ["skills", "规约 / 技能", "📐"],
  ["llm", "LLM 供应商", "🤖"],
  ["repos", "仓库与凭证", "🔑"],
  ["integrations", "集成", "🔌"],
  ["budget", "审批与预算", "⚖"],
];
