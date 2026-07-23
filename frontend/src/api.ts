import type {
  Channel, ChannelMessage, Project, FullConfig, Metrics, LLMStatus,
  LLMProviderStatus, MemoryEntry, AgentRole, Skill, SkillGenResult,
  AgentManifest, Connection, StoreSkill, InstalledSkill,
  MarketCard, InstalledCatalog, Workspace,
} from "./types";

/* ─── Base HTTP helpers ─── */
async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(await r.text());
  return r.json() as Promise<T>;
}

async function postJSON<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json() as Promise<T>;
}

async function putJSON<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json() as Promise<T>;
}

async function delJSON<T>(url: string): Promise<T> {
  const r = await fetch(url, { method: "DELETE" });
  if (!r.ok) throw new Error(await r.text());
  return r.json() as Promise<T>;
}

/* ─── Channels ── */
export function listChannels(): Promise<Channel[]> {
  return getJSON<Channel[]>("/api/channels");
}

export function getChannel(id: string): Promise<Channel> {
  return getJSON<Channel>(`/api/channels/${id}`);
}

export function createChannel(payload: {
  name: string;
  project_ids?: string[];
  roster?: string[];
}): Promise<Channel> {
  return postJSON<Channel>("/api/channels", payload);
}

export function updateChannel(
  id: string,
  payload: { name?: string; status?: string; mode?: "auto" | "manual" }
): Promise<Channel> {
  return putJSON<Channel>(`/api/channels/${id}`, payload);
}

export function setChannelMode(
  id: string,
  mode: "auto" | "manual"
): Promise<Channel> {
  return putJSON<Channel>(`/api/channels/${id}`, { mode });
}

/** Manual-mode: answer a pending handoff confirm card. `choice` is a role key to
 *  run, "all" to run every pending option, or "none" to stop the chain. */
export function confirmHandoff(
  id: string,
  choice: string
): Promise<{ ok: boolean; choice: string }> {
  return postJSON<{ ok: boolean; choice: string }>(
    `/api/channels/${id}/confirm`,
    { choice }
  );
}

export function deleteChannel(id: string): Promise<{ ok: boolean }> {
  return delJSON<{ ok: boolean }>(`/api/channels/${id}`);
}

export function getChannelMessages(id: string): Promise<ChannelMessage[]> {
  return getJSON<ChannelMessage[]>(`/api/channels/${id}/messages`);
}

export function requestReview(
  channelId: string,
  role = "developer"
): Promise<{ ok: boolean; role: string }> {
  return postJSON<{ ok: boolean; role: string }>(
    `/api/channels/${channelId}/review`,
    { role }
  );
}

export function sendChannelMessage(
  channelId: string,
  text: string
): Promise<Channel> {
  return postJSON<Channel>(`/api/channels/${channelId}/messages`, { text });
}

export function deleteChannelMessage(
  channelId: string,
  messageId: number | string
): Promise<{ ok: boolean }> {
  return delJSON<{ ok: boolean }>(
    `/api/channels/${channelId}/messages/${messageId}`
  );
}

export function clearChannelMessages(
  channelId: string
): Promise<{ ok: boolean; removed: number }> {
  return delJSON<{ ok: boolean; removed: number }>(
    `/api/channels/${channelId}/messages`
  );
}

export function getChannelProjects(id: string): Promise<{ project_id: string }[]> {
  return getJSON<{ project_id: string }[]>(`/api/channels/${id}/projects`);
}

export function addChannelProject(
  channelId: string,
  projectId: string
): Promise<{ project_id: string }[]> {
  return postJSON<{ project_id: string }[]>(
    `/api/channels/${channelId}/projects`,
    { project_id: projectId }
  );
}

export function removeChannelProject(
  channelId: string,
  projectId: string
): Promise<{ ok: boolean }> {
  return delJSON<{ ok: boolean }>(
    `/api/channels/${channelId}/projects/${projectId}`
  );
}

export function getChannelMembers(
  channelId: string
): Promise<{ role: AgentRole; state: string }[]> {
  return getJSON<{ role: AgentRole; state: string }[]>(
    `/api/channels/${channelId}/members`
  );
}

export function addChannelMember(
  channelId: string,
  role: string
): Promise<Channel> {
  return postJSON<Channel>(`/api/channels/${channelId}/members`, { role });
}

export function removeChannelMember(
  channelId: string,
  role: string
): Promise<{ ok: boolean }> {
  return delJSON<{ ok: boolean }>(
    `/api/channels/${channelId}/members/${role}`
  );
}

/* ─── Projects ── */
export function listProjects(): Promise<Project[]> {
  return getJSON<Project[]>("/api/projects");
}
export function getProject(id: string): Promise<Project> {
  return getJSON<Project>(`/api/projects/${id}`);
}
export function createProject(payload: {
  name: string; repo_url?: string; branch?: string; docs?: string;
}): Promise<Project> {
  return postJSON<Project>("/api/projects", payload);
}
export function updateProjectDocs(projectId: string, docs: string): Promise<{ ok: boolean }> {
  return putJSON(`/api/projects/${projectId}/docs`, { docs });
}
export function cloneProjectRepo(projectId: string): Promise<{ ok: boolean }> {
  return postJSON(`/api/projects/${projectId}/clone`);
}
export function setAgentMemory(
  projectId: string, role: string, text: string
): Promise<{ ok: boolean }> {
  return putJSON(`/api/projects/${projectId}/memory`, { role, text });
}
export function getProjectChannels(projectId: string): Promise<Channel[]> {
  return getJSON<Channel[]>(`/api/projects/${projectId}/channels`);
}
export function getWorkspace(projectId: string, role: string): Promise<Workspace> {
  return getJSON<Workspace>(`/api/projects/${projectId}/workspace/${role}`);
}
export function getWorkspaceFile(
  projectId: string, role: string, path: string
): Promise<{ path: string; content: string }> {
  return getJSON(`/api/projects/${projectId}/workspace/${role}/file?path=${encodeURIComponent(path)}`);
}

/* ─── Agent Manifests (Agent Studio) ── */
export function getProjectAgents(projectId: string): Promise<AgentManifest[]> {
  return getJSON<{ agents: AgentManifest[] }>(`/api/projects/${projectId}/agents`)
    .then((r) => r.agents);
}
export function saveProjectAgent(
  projectId: string, role: string, manifest: Partial<AgentManifest> | Record<string, unknown>
): Promise<AgentManifest> {
  return putJSON<AgentManifest>(`/api/projects/${projectId}/agents/${role}`, { manifest });
}

/* ─── Config ── */
export function getConfig(): Promise<FullConfig> {
  return getJSON<FullConfig>("/api/config");
}
export function saveConfig(config: FullConfig): Promise<{ ok: boolean }> {
  return putJSON("/api/config", config);
}

/* ─── Memory ── */
export function recallMemory(
  q: string, scope?: string
): Promise<MemoryEntry[]> {
  const params = new URLSearchParams({ q });
  if (scope) params.set("scope", scope);
  return getJSON<MemoryEntry[]>(`/api/memory/recall?${params}`);
}

/* ─── Generation skills ── */
export function listSkills(): Promise<Skill[]> {
  return getJSON<{ skills: Skill[] }>("/api/skills").then((r) => r.skills);
}
export function generateWithSkill(payload: {
  skill_id: string; project_id?: string; role?: string; brief?: string;
}): Promise<SkillGenResult> {
  return postJSON<SkillGenResult>("/api/skills/generate", payload);
}

/* ─── Connections（凭证/出网连接器） ── */
export function listConnections(): Promise<Connection[]> {
  return getJSON<Connection[]>("/api/connections");
}
export function saveConnection(data: Partial<Connection> | Record<string, unknown>): Promise<Connection> {
  return postJSON<Connection>("/api/connections", data);
}
export function deleteConnection(id: string): Promise<{ ok: boolean }> {
  return delJSON<{ ok: boolean }>(`/api/connections/${id}`);
}
export function testConnection(id: string): Promise<{ ok: boolean; detail?: string; error?: string }> {
  return postJSON(`/api/connections/${id}/test`);
}

/* ─── Skill store（技能市场） ── */
export function searchSkillStore(q = "", source = ""): Promise<StoreSkill[]> {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (source) params.set("source", source);
  const qs = params.toString();
  return getJSON<StoreSkill[]>(`/api/skills/store${qs ? `?${qs}` : ""}`);
}
export function installSkill(id: string, source: string): Promise<InstalledSkill> {
  return postJSON<InstalledSkill>("/api/skills/store/install", { id, source });
}
export function listInstalledSkills(): Promise<InstalledSkill[]> {
  return getJSON<InstalledSkill[]>("/api/skills/installed");
}
export function uninstallSkill(id: string): Promise<{ ok: boolean }> {
  return delJSON<{ ok: boolean }>(`/api/skills/installed/${id}`);
}

/* ─── Marketplace（独立市场页：真实 MCP + 技能，一键安装） ── */
export function searchMarketMcp(q = ""): Promise<MarketCard[]> {
  const qs = q ? `?q=${encodeURIComponent(q)}` : "";
  return getJSON<MarketCard[]>(`/api/market/mcp${qs}`);
}
export function searchMarketSkills(q = ""): Promise<MarketCard[]> {
  const qs = q ? `?q=${encodeURIComponent(q)}` : "";
  return getJSON<MarketCard[]>(`/api/market/skills${qs}`);
}
export function installMarketMcp(card: MarketCard): Promise<{ ok?: boolean }> {
  return postJSON("/api/market/mcp/install", { card });
}
export function uninstallMarketMcp(id: string): Promise<{ ok: boolean }> {
  return postJSON("/api/market/mcp/uninstall", { id });
}
export function getInstalledCatalog(): Promise<InstalledCatalog> {
  return getJSON<InstalledCatalog>("/api/market/installed");
}

/* ─── Metrics & LLM ── */
export function getMetrics(): Promise<Metrics> {
  return getJSON<Metrics>("/api/metrics");
}
export function getLLMStatus(): Promise<LLMStatus> {
  return getJSON<LLMStatus>("/api/llm");
}
export function getLLMProviders(): Promise<{ providers: LLMProviderStatus[] }> {
  return getJSON("/api/llm/providers");
}
export function testLLMProvider(providerId: string): Promise<{ ok: boolean; model?: string; error?: string }> {
  return postJSON("/api/llm/test", { provider_id: providerId });
}
export function setLLMProviderKey(
  providerId: string, apiKey: string
): Promise<{ ok: boolean }> {
  return putJSON("/api/llm/keys", { provider_id: providerId, api_key: apiKey });
}
