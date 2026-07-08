/* ───── Types from the backend data model ───── */

export type AgentRole =
  | "coordinator"
  | "analyst"
  | "developer"
  | "tester"
  | "devops"
  | "reporter";
export type RosterState =
  | "idle" | "working" | "blocked" | "escalated" | "done";
export type PipelineState = "pending" | "running" | "pass" | "fail";
export type CardKind =
  | "contract" | "handoff" | "gate" | "gate-running"
  | "approval" | "escalation" | "closed" | "done";
export type MessageKind = "sys" | "agent" | "human" | "card";
export type ChannelStatus = "idle" | "active" | "done";

/* ── Channel (group chat) ── */
export interface ChannelMember {
  role: AgentRole;
  state: RosterState;
}

export interface ChannelProject {
  project_id: string;
}

export interface ChannelMessage {
  id?: number;
  kind: MessageKind;
  role?: AgentRole;
  text?: string;
  html?: string;
  t?: string;
  streaming?: boolean;
  done?: string;
  card?: CardKind;
  from?: AgentRole;
  to?: AgentRole;
  pt?: string;
  note?: string;
  q?: string;
  // collapsed detail
  thinking?: string;
  toolCalls?: ToolCall[];
}

export interface ToolCall {
  tool: string;
  text: string;
  result?: string;
}

export interface Channel {
  id: string;
  name: string;
  status: ChannelStatus;
  projects: ChannelProject[];
  members: ChannelMember[];
  messages: ChannelMessage[];
  created_at: number;
  updated_at: number;
}

/* ── Project ── */
export interface Project {
  id: string;
  name: string;
  repo_url: string;
  branch: string;
  docs: string;
  status: string;
  local_path: string;
  memory?: Record<AgentRole, string>;
  clone_log?: string;
}

/* ── Legacy ticket types (for migration compat) ── */
export type TicketType = "bug" | "feature" | "incident";
export type TicketStatus =
  | "new" | "planning" | "working" | "awaiting_approval"
  | "blocked" | "done" | "rejected";
export interface Ticket {
  id: string;
  title: string;
  type: TicketType;
  description: string;
  repo: string;
  source: string;
  lane: "fast" | "warroom";
  status: TicketStatus;
  needs: boolean;
  trusted: boolean;
  project_id: string;
  contract: Record<string, unknown> | null;
  pipeline: { steps: { id: string; label: string; state: PipelineState }[] };
  budget: { max_tokens: number; max_cost_usd: number; spent_tokens: number; spent_cost: number };
  roster: { role: AgentRole; state: RosterState }[];
  stream: ChannelMessage[];
  gate_results: Record<string, { gate_id: string; status: PipelineState; evidence: Record<string, unknown> }>;
  created_at: number;
  updated_at: number;
}

/* ── LLM ── */
export interface LLMStatus {
  available: boolean;
  id?: string;
  name?: string;
  type?: string;
  model?: string;
  effort?: string;
  key_configured?: boolean;
  error?: string;
}

export interface LLMProvider {
  id: string;
  name: string;
  type: "anthropic" | "openai";
  api_key_env: string;
  api_key?: string;
  base_url: string;
  model: string;
  enabled: boolean;
  max_tokens: number;
  effort?: string;
}

export interface LLMProviderStatus extends LLMProvider {
  active: boolean;
  key_configured: boolean;
}

export interface LLMConfig {
  active_provider: string;
  providers: LLMProvider[];
}

/* ── Config ── */
export interface AgentConfig { role: AgentRole; exec: string; on: boolean; }
export interface TemplateConfig { type: TicketType; lane: string; roster: AgentRole[]; }
export interface GateConfig { name: string; desc: string; thr?: number; on: boolean; }
export interface SkillConfig { name: string; ver: string; desc: string; roles: AgentRole[]; on: boolean; }
export interface RepoConfig { name: string; url: string; branch: string; }
export interface IntegrationConfig { name: string; on: boolean; }
export interface BudgetConfig { autoLow: boolean; tokens: number; cost: number; steps: number; }
export interface ApproveConfig { name: string; on: boolean; }
export interface FullConfig {
  agents: AgentConfig[];
  templates: TemplateConfig[];
  gates: GateConfig[];
  skills: SkillConfig[];
  repos: RepoConfig[];
  integrations: IntegrationConfig[];
  budget: BudgetConfig;
  approve: ApproveConfig[];
  llm: LLMConfig;
}

/* ── Metrics ── */
export interface Metrics {
  tickets_total: number;
  by_status: Partial<Record<TicketStatus, number>>;
  gates: Record<string, number>;
  escalations: number;
  approvals: number;
}

/* ── Memory ── */
export interface MemoryEntry {
  scope: string; source: string; line: number; text: string;
}

/* ── Agent Manifest (per-project, per-role config) ── */
export interface AgentManifest {
  apiVersion: string;
  kind: string;
  role: AgentRole;
  identity: { role: AgentRole; name: string; avatar: string; focus?: string };
  model: { provider: string; model: string; maxTokens: number; effort: string };
  prompt: { charter: string; guardrails: string[] };
  harness: { builtinTools: string[]; toolPolicy: Record<string, string> };
  mcp: unknown[];
  skills: unknown[];
  memory: { scopes: string[] };
  budget: { maxTokens: number; maxCostUsd: number };
  enabled: boolean;
  inherited: boolean;
}

/* ── Connections (credential vault / egress) ── */
export interface Connection {
  id: string;
  type: string;               // github / http / postgres
  name?: string;
  key_configured?: boolean;   // 是否已配置密钥（不含明文）
  [k: string]: unknown;       // 其它非密元数据（host/url/...）
}

/* ── Skill store (Agent Skills / MCP market) ── */
export interface StoreSkill {
  id: string;
  name: string;
  description: string;
  source: string;             // anthropic / smithery / builtin
  version?: string;
  permissions: string[];      // 安装所需权限
}

export interface InstalledSkill {
  id: string;
  name: string;
  description?: string;
  source: string;
  version?: string;
  permissions?: string[];
}

/* ── MCP mount (manifest.mcp 每一项) ── */
export interface McpMount {
  server: string;
  transport: "http" | "stdio";
  url?: string;
  command?: string;
  ref?: string;
  tools?: string[];
}

/* ── Agent-attached skill (manifest.skills 每一项) ── */
export interface AgentSkillRef {
  id: string;
  source: string;
  version?: string;
}

/* ── Marketplace ── */
export interface MarketCard {
  id: string;
  source: string;             // official / smithery / anthropic / builtin
  name: string;
  description: string;
  homepage?: string;
  icon?: string;
  transport?: "http" | "stdio";
  mount?: McpMount;           // MCP 卡片携带的挂载
  verified?: boolean;
  useCount?: number;
  permissions?: string[];     // skill 卡片
  kind?: string;              // "skill" for skill cards
  version?: string;
}

export interface InstalledMcp {
  id: string;
  name: string;
  description?: string;
  source: string;
  homepage?: string;
  icon?: string;
  mount: McpMount;
  needs_key?: boolean;
  installed_at?: number;
}

export interface InstalledCatalog {
  mcp: InstalledMcp[];
  skills: InstalledSkill[];
}

/* ── Generation skills ── */
export interface Skill {
  id: string;
  name: string;
  tagline: string;
  icon: string;
  target: "docs" | "memory";
  role: AgentRole | null;
  hint: string;
}

export interface SkillGenResult {
  skill: string;
  text: string;
  stop_reason?: string;
  error?: string;
}

/* ── SSE ── */
export interface SSEEvent {
  type: string;
  ticket?: string;
  agent?: AgentRole;
  payload?: {
    message_id?: number;
    text?: string;
    tool?: string;
    streaming?: boolean;
    final?: boolean;
    msg?: string;
    thinking?: string;
    toolCalls?: ToolCall[];
  };
}
