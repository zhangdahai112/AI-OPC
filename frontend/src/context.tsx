import {
  createContext, useContext, useState, useCallback,
  useEffect, useRef, type ReactNode,
} from "react";
import type {
  Channel, ChannelMessage, ChannelMember, Project,
  FullConfig, Metrics, LLMStatus, SSEEvent, AgentRole, TimelineStep,
} from "./types";
import * as api from "./api";

/* ─── State ─── */
export interface AppState {
  view: "channels" | "projects" | "metrics" | "config" | "market";
  channels: Channel[];
  activeChannelId: string | null;
  activeChannel: Channel | null;
  projects: Project[];
  activeProject: Project | null;
  config: FullConfig | null;
  metrics: Metrics | null;
  llm: LLMStatus | null;
  // streaming — `steps` is the ordered timeline (text/tool interleaved); `text`
  // and `tools` are kept as flat mirrors for the older split renderer.
  streaming: Record<number, { role?: AgentRole; text: string; tools?: { tool: string; text: string }[]; steps?: TimelineStep[] }>;
  live: Record<string, { agent: string; text: string; tool?: string }>;
  // private 1:1 chat
  privateChat: { channelId: string; role: AgentRole } | null;
  privateChatMessages: ChannelMessage[];
  // collapsed threads per message id
  collapsed: Record<number, boolean>;
}

export interface AppActions {
  setView: (v: AppState["view"]) => void;
  setActiveChannelId: (id: string | null) => void;
  setActiveProject: (p: Project | null) => void;
  setConfig: (c: FullConfig) => void;
  refreshChannels: () => Promise<void>;
  refreshActiveChannel: () => Promise<void>;
  refreshProjects: () => Promise<void>;
  refreshActiveProject: (id: string) => Promise<void>;
  refreshConfig: () => Promise<void>;
  refreshMetrics: () => Promise<void>;
  refreshLLM: () => Promise<void>;
  toggleCollapse: (mid: number) => void;
  openPrivateChat: (channelId: string, role: AgentRole) => void;
  closePrivateChat: () => void;
  refreshPrivateChat: () => Promise<void>;
  toast: (msg: string) => void;
}

const AppCtx = createContext<(AppState & AppActions) | null>(null);

/* ─── Provider ─── */
export function AppProvider({ children }: { children: ReactNode }) {
  const [view, setView] = useState<AppState["view"]>("channels");
  const [channels, setChannels] = useState<Channel[]>([]);
  const [activeChannelId, setActiveChannelId] = useState<string | null>(null);
  const [activeChannel, setActiveChannel] = useState<Channel | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProject, setActiveProject] = useState<Project | null>(null);
  const [config, setConfig] = useState<FullConfig | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [llm, setLlm] = useState<LLMStatus | null>(null);
  const [streaming, setStreaming] = useState<Record<number, { role?: AgentRole; text: string; tools?: { tool: string; text: string }[]; steps?: TimelineStep[] }>>({});
  const [live, setLive] = useState<Record<string, { agent: string; text: string; tool?: string }>>({});
  const [privateChat, setPrivateChat] = useState<{ channelId: string; role: AgentRole } | null>(null);
  const [privateChatMessages, setPrivateChatMessages] = useState<ChannelMessage[]>([]);
  const [collapsed, setCollapsed] = useState<Record<number, boolean>>({});

  const viewRef = useRef(view);
  viewRef.current = view;
  const activeChannelIdRef = useRef(activeChannelId);
  activeChannelIdRef.current = activeChannelId;
  const privateChatRef = useRef(privateChat);
  privateChatRef.current = privateChat;

  /* ─── Data fetching ─── */
  const refreshChannels = useCallback(async () => {
    try { setChannels(await api.listChannels()); } catch {}
  }, []);

  const refreshActiveChannel = useCallback(async () => {
    if (!activeChannelIdRef.current) return;
    try {
      const ch = await api.getChannel(activeChannelIdRef.current);
      setActiveChannel(ch);
    } catch { setActiveChannel(null); }
  }, []);

  const refreshProjects = useCallback(async () => {
    try { setProjects(await api.listProjects()); } catch {}
  }, []);

  const refreshActiveProject = useCallback(async (id: string) => {
    try { setActiveProject(await api.getProject(id)); } catch {}
  }, []);

  const refreshConfig = useCallback(async () => {
    try { setConfig(await api.getConfig()); } catch {}
  }, []);

  const refreshMetrics = useCallback(async () => {
    try { setMetrics(await api.getMetrics()); } catch {}
  }, []);

  const refreshLLM = useCallback(async () => {
    try { setLlm(await api.getLLMStatus()); } catch {}
  }, []);

  /* ─── Boot ─── */
  useEffect(() => {
    refreshChannels();
    refreshProjects();
    refreshConfig();
    refreshLLM();
  }, []);

  /* ─── Active channel loading ─── */
  useEffect(() => {
    if (activeChannelId) {
      setStreaming({});
      api.getChannel(activeChannelId).then(setActiveChannel).catch(() => setActiveChannel(null));
    } else {
      setActiveChannel(null);
    }
  }, [activeChannelId]);

  /* ─── SSE ─── */
  const scheduleTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const scheduleRefresh = useCallback(() => {
    clearTimeout(scheduleTimer.current);
    scheduleTimer.current = setTimeout(() => {
      refreshChannels();
      if (viewRef.current === "channels") refreshActiveChannel();
      if (viewRef.current === "metrics") refreshMetrics();
    }, 260);
  }, [refreshChannels, refreshActiveChannel, refreshMetrics]);

  const handleSSEEvent = useCallback((m: SSEEvent) => {
    // delta streaming
    if (m.type === "delta" && m.ticket === activeChannelIdRef.current && m.payload?.message_id) {
      const mid = m.payload.message_id;
      const chunk = m.payload?.text || "";
      setStreaming((prev) => {
        const ex = prev[mid] || { role: m.agent, text: "" };
        // append the chunk onto the trailing text step (or open a new one) so the
        // timeline keeps narration and tool calls in their real order.
        const steps = [...(ex.steps || [])];
        const last = steps[steps.length - 1];
        if (last && last.type === "text") steps[steps.length - 1] = { type: "text", text: last.text + chunk };
        else steps.push({ type: "text", text: chunk });
        return { ...prev, [mid]: { ...ex, text: ex.text + chunk, steps } };
      });
      return;
    }
    // new streaming bubble
    if (m.type === "message" && m.payload?.streaming && m.ticket === activeChannelIdRef.current && m.payload?.message_id) {
      const mid = m.payload.message_id;
      setStreaming((prev) => ({
        ...prev, [mid]: { role: m.agent, text: "", steps: [] },
      }));
      return;
    }
    // live tool call on a streaming agent message
    if (m.type === "tool_call" && m.ticket === activeChannelIdRef.current && m.payload?.message_id) {
      const mid = m.payload.message_id;
      const tool = m.payload.tool || "";
      const text = m.payload.text || "";
      setStreaming((prev) => {
        const ex = prev[mid] || { role: m.agent, text: "" };
        return {
          ...prev,
          [mid]: {
            ...ex,
            tools: [...(ex.tools || []), { tool, text }],
            steps: [...(ex.steps || []), { type: "tool", tool, text }],
          },
        };
      });
      return;
    }
    // final message — remove streaming bubble
    if (m.type === "message" && m.payload?.final && m.ticket === activeChannelIdRef.current && m.payload?.message_id) {
      const mid = m.payload.message_id;
      setStreaming((prev) => {
        const n = { ...prev };
        delete n[mid];
        return n;
      });
      scheduleRefresh();
      return;
    }
    // private chat delta
    if (m.type === "delta" && privateChatRef.current && m.payload?.message_id) {
      const mid = m.payload.message_id;
      setPrivateChatMessages((prev) => {
        const idx = prev.findIndex((x) => x.id === mid);
        if (idx < 0) return prev;
        const cur = prev[idx]!;
        const updated = [...prev];
        updated[idx] = { ...cur, html: (cur.html || "") + (m.payload?.text || "") };
        return updated;
      });
      return;
    }
    // progress / tool_call
    if (m.ticket && (m.type === "tool_call" || m.type === "progress") && m.payload) {
      setLive((prev) => ({
        ...prev,
        [m.ticket!]: { agent: m.agent || "", text: m.payload?.text || m.payload?.tool || "", tool: m.payload?.tool },
      }));
    }
    // result — clear live
    if (m.type === "result" && m.ticket) {
      setLive((prev) => { const n = { ...prev }; delete n[m.ticket!]; return n; });
    }
    // project clone done
    if (m.type === "project_status" && m.payload) {
      refreshProjects();
      if (viewRef.current === "projects") {
        const pid = (m.payload as { project_id?: string }).project_id;
        if (pid) refreshActiveProject(pid);
      }
    }
    scheduleRefresh();
  }, [scheduleRefresh]);

  /* ─── Toast ─── */
  const [toastMsg, setToastMsg] = useState("");
  const toastTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const toast = useCallback((msg: string) => {
    setToastMsg(msg);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToastMsg(""), 1900);
  }, []);

  /* ─── View setter ─── */
  const handleSetView = useCallback((v: AppState["view"]) => {
    setView(v);
    if (v === "metrics") refreshMetrics();
    if (v === "config" && !config) refreshConfig();
    if (v === "projects") refreshProjects();
  }, [config, refreshConfig, refreshMetrics, refreshProjects]);

  /* ─── Collapse ─── */
  const toggleCollapse = useCallback((mid: number) => {
    setCollapsed((prev) => ({ ...prev, [mid]: !prev[mid] }));
  }, []);

  /* ─── Private chat ─── */
  const openPrivateChat = useCallback(async (channelId: string, role: AgentRole) => {
    setPrivateChat({ channelId, role });
    setPrivateChatMessages([]);
    // TODO: load private history
  }, []);

  const closePrivateChat = useCallback(() => {
    setPrivateChat(null);
    setPrivateChatMessages([]);
  }, []);

  const refreshPrivateChat = useCallback(async () => {
    if (!privateChat) return;
    // placeholder — real implementation fetches 1:1 history
  }, [privateChat]);

  const value: AppState & AppActions = {
    view, channels, activeChannelId, activeChannel,
    projects, activeProject,
    config, metrics, llm, streaming, live,
    privateChat, privateChatMessages, collapsed,
    setView: handleSetView, setActiveChannelId, setActiveProject, setConfig,
    refreshChannels, refreshActiveChannel,
    refreshProjects, refreshActiveProject,
    refreshConfig, refreshMetrics, refreshLLM,
    toggleCollapse,
    openPrivateChat, closePrivateChat, refreshPrivateChat,
    toast,
  };

  return (
    <AppCtx.Provider value={value}>
      {children}
      <Toast msg={toastMsg} />
      <SSELayer onEvent={handleSSEEvent} />
    </AppCtx.Provider>
  );
}

export function useAppState() {
  const ctx = useContext(AppCtx);
  if (!ctx) throw new Error("useAppState must be inside AppProvider");
  return ctx;
}

/* ─── Internal: SSE hook runner ─── */
import { useSSE } from "./useSSE";
function SSELayer({ onEvent }: { onEvent: (ev: SSEEvent) => void }) {
  useSSE(onEvent);
  return null;
}

/* ─── Toast ─── */
function Toast({ msg }: { msg: string }) {
  if (!msg) return null;
  return <div className="toast show">{msg}</div>;
}
