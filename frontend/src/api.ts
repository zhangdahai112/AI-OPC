import type {
  Channel, ChannelMessage, Project, FullConfig, Metrics, LLMStatus,
  LLMProviderStatus, MemoryEntry, AgentRole, Skill, SkillGenResult,
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
  payload: { name?: string; status?: string }
): Promise<Channel> {
  return putJSON<Channel>(`/api/channels/${id}`, payload);
}

export function deleteChannel(id: string): Promise<{ ok: boolean }> {
  return delJSON<{ ok: boolean }>(`/api/channels/${id}`);
}

export function getChannelMessages(id: string): Promise<ChannelMessage[]> {
  return getJSON<ChannelMessage[]>(`/api/channels/${id}/messages`);
}

export function sendChannelMessage(
  channelId: string,
  text: string
): Promise<Channel> {
  return postJSON<Channel>(`/api/channels/${channelId}/messages`, { text });
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
