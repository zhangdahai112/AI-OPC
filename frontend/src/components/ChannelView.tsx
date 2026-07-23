import { useRef, useEffect, useState, useCallback, type ReactNode } from "react";
import { useAppState } from "../context";
import { ROLES, STATE_CN } from "../constants";
import { esc, mdLite } from "../utils";
import type { TimelineStep } from "../types";
import * as api from "../api";

const SCROLL_THRESHOLD = 80;

export default function ChannelView() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const {
    activeChannel, streaming, toast, refreshActiveChannel,
    openPrivateChat, toggleCollapse, collapsed,
  } = useAppState() as any;
  const streamRef = useRef<HTMLDivElement>(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [hasNew, setHasNew] = useState(false);
  const [wsOpen, setWsOpen] = useState(false);
  const isNearBottomRef = useRef(true);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const checkNearBottom = useCallback(() => {
    const el = streamRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_THRESHOLD;
  }, []);

  const scrollToBottom = useCallback((smooth = true) => {
    const el = streamRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
    setHasNew(false);
    isNearBottomRef.current = true;
  }, []);

  const handleScroll = useCallback(() => {
    const near = checkNearBottom();
    isNearBottomRef.current = near;
    if (near) setHasNew(false);
  }, [checkNearBottom]);

  useEffect(() => {
    if (!streamRef.current) return;
    if (isNearBottomRef.current) {
      scrollToBottom(false);
    } else {
      setHasNew(true);
    }
  }, [activeChannel?.messages, streaming, scrollToBottom]);

  // auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, [text]);

  if (!activeChannel) {
    return (
      <div className="cfgwrap">
        <p className="lead">从左侧选择一个群聊开始协作</p>
      </div>
    );
  }

  const ch = activeChannel;

  const handleSend = async () => {
    const v = text.trim();
    if (!v || sending) return;
    setText("");
    setSending(true);
    try {
      await api.sendChannelMessage(ch.id, v);
      refreshActiveChannel();
    } catch {
      setText(v);
      toast("发送失败，已恢复你的输入");
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleDeleteMessage = async (id: number | string) => {
    try {
      await api.deleteChannelMessage(ch.id, id);
      refreshActiveChannel();
    } catch {
      toast("删除失败");
    }
  };

  const handleClear = async () => {
    if (!(ch.messages || []).length) return;
    if (!window.confirm("确定清空本群所有消息？此操作不可撤销。")) return;
    try {
      await api.clearChannelMessages(ch.id);
      refreshActiveChannel();
      toast("已清空群聊消息");
    } catch {
      toast("清空失败");
    }
  };

  const insertMention = (role: string) => {
    setText((t) => (t ? `${t.replace(/\s*$/, "")} @${role} ` : `@${role} `));
    textareaRef.current?.focus();
  };

  const mode: "auto" | "manual" = ch.mode || "auto";
  const handleSetMode = async (next: "auto" | "manual") => {
    if (mode === next) return;
    try {
      await api.setChannelMode(ch.id, next);
      refreshActiveChannel();
      toast(next === "manual"
        ? "已切到手动模式：触发下一个成员需你确认"
        : "已切到自动模式：成员之间可自行接力");
    } catch {
      toast("切换失败");
    }
  };

  const handleConfirm = async (choice: string) => {
    try {
      await api.confirmHandoff(ch.id, choice);
      refreshActiveChannel();
    } catch {
      toast("操作失败");
    }
  };

  return (
    <>
      {/* Channel header */}
      <div className="chead">
        <div className="chead-row">
          <h1>{esc(ch.name)}</h1>
          <span className={`badge ${ch.status}`}>
            <span className="bdot" />
            {ch.status === "active" ? "活跃" : "已结束"}
          </span>
          <div className="mode-toggle" role="group"
            title="自动：成员之间可自行接力协作；手动：每次触发下一个成员都要你点选确认">
            <button className={`mode-seg${mode === "auto" ? " on" : ""}`}
              onClick={() => handleSetMode("auto")}>⚡ 自动</button>
            <button className={`mode-seg${mode === "manual" ? " on" : ""}`}
              onClick={() => handleSetMode("manual")}>✋ 手动</button>
          </div>
          {!!(ch.projects || []).length && (
            <button className="chead-clear" onClick={() => setWsOpen(true)}
              title="查看各成员写出的代码（每个角色有独立工作副本）">
              📁 工作区
            </button>
          )}
          {!!(ch.messages || []).length && (
            <button className="chead-clear" onClick={handleClear}
              title="清空本群所有消息">
              🗑 清空
            </button>
          )}
        </div>
        <div className="sub">
          {ch.members?.length || 0} 个成员 · {ch.projects?.length || 0} 个项目
        </div>
      </div>

      {/* Message stream */}
      <div className="stream" ref={streamRef} onScroll={handleScroll}>
        {!(ch.messages || []).length && !Object.keys(streaming).length && (
          <div className="stream-empty">
            <div className="se-ic">💬</div>
            <p className="se-title">开始和你的作战群协作</p>
            <p className="se-sub">
              在下方发消息，或用 <b>@</b> 点名某个成员。绑定了项目的群里，
              成员会真正读代码、跑命令、改文件。
            </p>
          </div>
        )}
        {(ch.messages || []).map((msg: any, i: number) => (
          <MessageBubble key={msg.id || i} msg={msg}
            collapsed={!!collapsed[msg.id]}
            onToggle={() => msg.id && toggleCollapse(msg.id)}
            onAvatarClick={() => {
              if (msg.role) openPrivateChat(ch.id, msg.role);
            }}
            onDelete={msg.id ? () => handleDeleteMessage(msg.id) : undefined}
            onConfirm={handleConfirm}
          />
        ))}
        {Object.entries(streaming).map(([id, s]: [string, any]) => (
          <StreamingBubble key={id} role={s.role} text={s.text} steps={s.steps} />
        ))}
      </div>

      {hasNew && (
        <button className="new-msgs" onClick={() => scrollToBottom(true)}>
          有新消息 ↓
        </button>
      )}

      {/* Composer */}
      <div className="composer">
        {!!(ch.members || []).length && (
          <div className="mention-bar">
            <span className="mb-label">@</span>
            {(ch.members || []).map((m: any) => {
              const info = ROLES[m.role as keyof typeof ROLES] || { nm: m.role, ab: "?" };
              return (
                <button key={m.role} className={`mb-chip ${m.role}`}
                  onClick={() => insertMention(m.role)} title={`点名 ${info.nm}`}>
                  {info.nm}
                </button>
              );
            })}
          </div>
        )}
        <div className="cbox">
          <textarea
            ref={textareaRef}
            rows={1}
            placeholder="在群里说点什么…  （@ 点名成员，回车发送，Shift+回车换行）"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={sending}
            style={{ maxHeight: 120, overflowY: "auto" }}
          />
          <button className={`send${sending ? " loading" : ""}`} title="发送"
            onClick={handleSend} disabled={sending}>
            {sending ? <span className="btn-spin" /> : "↑"}
          </button>
        </div>
        <div className="chint">
          {sending ? "发送中…" : "按回车发送，群里成员会看到你的消息并接力协作"}
        </div>
      </div>

      {wsOpen && (
        <WorkspaceDrawer
          projects={(ch.projects || []).map((p: any) => p.project_id).filter(Boolean)}
          members={(ch.members || []).map((m: any) => m.role)}
          onClose={() => setWsOpen(false)}
        />
      )}
    </>
  );
}

/** Slide-in panel to browse the code each agent actually wrote. Every role has its
 *  own isolated working copy (a per-role clone), so we let the operator pick a
 *  project + role and inspect that clone's files — closing the "代码不在 workspace
 *  里" gap without touching the isolation model. */
function WorkspaceDrawer({ projects, members, onClose }: {
  projects: string[]; members: string[]; onClose: () => void;
}) {
  const roleList = members.filter((r) => r !== "reporter");
  const [pid, setPid] = useState(projects[0] || "");
  const [role, setRole] = useState(roleList[0] || "developer");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [ws, setWs] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [sel, setSel] = useState<string>("");
  const [content, setContent] = useState<string>("");

  useEffect(() => {
    if (!pid || !role) return;
    let live = true;
    setLoading(true);
    setSel("");
    setContent("");
    api.getWorkspace(pid, role)
      .then((w) => { if (live) setWs(w); })
      .catch(() => { if (live) setWs(null); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [pid, role]);

  const openFile = async (path: string) => {
    setSel(path);
    setContent("加载中…");
    try {
      const r = await api.getWorkspaceFile(pid, role, path);
      setContent(r.content);
    } catch {
      setContent("（读取失败）");
    }
  };

  return (
    <div className="ws-overlay" onClick={onClose}>
      <div className="ws-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="ws-hd">
          <b>📁 成员工作区</b>
          <button className="ws-x" onClick={onClose} title="关闭">✕</button>
        </div>
        <div className="ws-pick">
          {projects.length > 1 && (
            <select value={pid} onChange={(e) => setPid(e.target.value)}>
              {projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          )}
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            {roleList.map((r) => (
              <option key={r} value={r}>
                {ROLES[r as keyof typeof ROLES]?.nm || r}
              </option>
            ))}
          </select>
          {ws?.exists && (
            <span className="ws-meta">
              {ws.branch && <code>{ws.branch}</code>}
              {ws.head && <code>@{ws.head}</code>}
              {!!ws.dirty?.length && <span className="ws-dirty">● {ws.dirty.length} 未提交</span>}
            </span>
          )}
        </div>
        <div className="ws-body">
          <div className="ws-tree">
            {loading && <div className="ws-empty">加载中…</div>}
            {!loading && !ws?.exists && (
              <div className="ws-empty">
                该角色还没有工作副本——绑定了 git 仓库的项目里，成员第一次动手（读/写代码）后才会生成。
              </div>
            )}
            {!loading && ws?.exists && !ws.files?.length && (
              <div className="ws-empty">工作副本为空（还没有写入任何文件）。</div>
            )}
            {!loading && ws?.files?.map((f: string) => (
              <button key={f} className={`ws-file${sel === f ? " on" : ""}`}
                onClick={() => openFile(f)} title={f}>
                {f}
              </button>
            ))}
            {ws?.truncated && <div className="ws-empty">…（文件过多，已截断）</div>}
          </div>
          <div className="ws-view">
            {sel
              ? <><div className="ws-view-hd">{sel}</div><pre className="ws-code">{content}</pre></>
              : <div className="ws-empty">← 选一个文件查看内容</div>}
          </div>
        </div>
      </div>
    </div>
  );
}

interface MessageBubbleProps {
  msg: any;
  collapsed: boolean;
  onToggle: () => void;
  onAvatarClick: () => void;
  onDelete?: () => void;
  onConfirm?: (choice: string) => void;
}

function DeleteMsgBtn({ onDelete }: { onDelete?: () => void }) {
  if (!onDelete) return null;
  return (
    <button className="msg-del" title="删除这条消息"
      onClick={() => {
        if (window.confirm("删除这条消息？")) onDelete();
      }}>
      ✕
    </button>
  );
}

const GATE_CN: Record<string, string> = {
  quick: "快速检查", test: "测试", policy: "策略检查", human: "人工审批",
};
const GATE_STATUS_CN: Record<string, string> = {
  pass: "通过", fail: "未通过", skip: "跳过", pending: "待定", running: "运行中",
};

function GateCard({ msg }: { msg: any }) {
  const results: any[] = msg.results || [];
  const headline = msg.ok ? "验收通过 ✓" : "验收未通过";
  return (
    <div className="sys" style={{ display: "block" }}>
      <div style={{ fontWeight: 600, marginBottom: 6 }}>
        {esc(msg.title || "验收结果")} · {headline}
        {msg.sha && (
          <span style={{ color: "var(--tx3)", fontFamily: "var(--mono)", fontWeight: 400, marginLeft: 8 }}>
            @{esc(msg.sha)}
          </span>
        )}
      </div>
      {results.map((r, i) => (
        <div key={i} style={{ display: "flex", gap: 8, fontSize: 12, padding: "2px 0", alignItems: "baseline" }}>
          <span className={`pill ${r.status}`} style={{ minWidth: 52, textAlign: "center" }}>
            {GATE_STATUS_CN[r.status] || r.status}
          </span>
          <span style={{ color: "var(--tx)" }}>{GATE_CN[r.gate] || r.gate}</span>
          <span style={{ color: "var(--tx3)", fontFamily: "var(--mono)", fontSize: 11 }}>
            {gateEvidenceLine(r)}
          </span>
        </div>
      ))}
    </div>
  );
}

function gateEvidenceLine(r: any): string {
  const e = r.evidence || {};
  if (r.gate === "test") {
    if (r.status === "skip") return esc(e.reason || "未运行");
    const cov = e.coverage === "n/a" || e.coverage == null ? "" : ` · 覆盖率 ${e.coverage}%（下限 ${e.floor}）`;
    return `${esc(e.runner || "")}${cov}`;
  }
  if (r.gate === "policy") {
    return e.secret_scan === "LEAK" ? `泄露: ${(e.leaked || []).join(", ")}` : "密钥扫描通过";
  }
  if (r.gate === "quick") {
    return (e.errors && e.errors.length) ? `问题: ${e.errors.join(", ")}` : "构建/类型检查通过";
  }
  return "";
}

/** Manual-mode handoff gate: an agent wants to trigger the next agent(s); the
 *  human picks who (if anyone) proceeds. Nothing runs until a choice is made. */
function ConfirmCard({ msg, onConfirm }: { msg: any; onConfirm?: (c: string) => void }) {
  const opts: { role: string; name: string }[] = msg.options || [];
  const done: string | undefined = msg.done;
  const doneLabel = (c: string) =>
    c === "none" ? "不触发" : c === "all" ? "全部执行"
      : (ROLES[c as keyof typeof ROLES]?.nm || c);
  return (
    <div className="confirm-card">
      <div className="cc-hd">
        <span className="cc-ic">✋</span>
        <b>需要你确认</b>
        <span className="cc-tag">手动模式</span>
      </div>
      <div className="cc-note">{esc(msg.note || "是否触发下一个成员？")}</div>
      {done ? (
        <div className="cc-done">已选择：{doneLabel(done)}</div>
      ) : (
        <div className="cc-actions">
          {opts.map((o) => {
            const info = ROLES[o.role as keyof typeof ROLES] || { nm: o.name || o.role };
            return (
              <button key={o.role} className={`cc-btn go ${o.role}`}
                onClick={() => onConfirm?.(o.role)}>
                让 {o.name || info.nm} 接力
              </button>
            );
          })}
          {opts.length > 1 && (
            <button className="cc-btn all" onClick={() => onConfirm?.("all")}>
              全部执行
            </button>
          )}
          <button className="cc-btn none" onClick={() => onConfirm?.("none")}>
            不用了
          </button>
        </div>
      )}
    </div>
  );
}

function MessageBubble({ msg, collapsed, onToggle, onAvatarClick, onDelete, onConfirm }: MessageBubbleProps) {
  if (msg.kind === "sys") {
    return <div className="sys"><span>{esc(msg.text || "")}</span></div>;
  }

  if (msg.kind === "card" && msg.card === "gate") {
    return <GateCard msg={msg} />;
  }

  if (msg.kind === "card" && msg.card === "confirm") {
    return <ConfirmCard msg={msg} onConfirm={onConfirm} />;
  }

  if (msg.kind === "human") {
    return (
      <div className="row human enter">
        <div className="av">你</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="who">
            <b>你</b>
            <span className="t">{msg.t || "刚刚"}</span>
            <DeleteMsgBtn onDelete={onDelete} />
          </div>
          <div className="bubble" dangerouslySetInnerHTML={{ __html: mdLite(msg.html || "") }} />
        </div>
      </div>
    );
  }

  if (msg.kind === "agent") {
    const info = ROLES[msg.role as keyof typeof ROLES] || { nm: msg.role, ab: "??" };
    const hasThinking = !!msg.thinking;
    // Ordered turn timeline. New messages carry msg.steps (narration interleaved
    // with tool calls); older ones are synthesized from toolCalls + html so the
    // legacy "all tools, then answer" still renders.
    const timeline: TimelineStep[] = msg.steps?.length
      ? msg.steps
      : [
          ...((msg.toolCalls || []).map((tc: any) => ({ type: "tool", ...tc })) as TimelineStep[]),
          ...(msg.html ? [{ type: "text", text: msg.html } as TimelineStep] : []),
        ];
    // Final answer = the last non-empty text step; everything before it (the
    // narration + tool calls, in order) is the collapsible process.
    let ansIdx = -1;
    for (let i = timeline.length - 1; i >= 0; i--) {
      const s = timeline[i];
      if (s && s.type === "text" && s.text.trim()) { ansIdx = i; break; }
    }
    const answer = ansIdx >= 0 ? (timeline[ansIdx] as { text: string }).text : (msg.html || "");
    const process = timeline.filter((_, i) => i !== ansIdx);
    const stepCount = (hasThinking ? 1 : 0) + process.length;
    const expandable = stepCount > 0;

    return (
      <div className="row enter">
        <div
          className={`av ${msg.role || ""}`}
          onClick={onAvatarClick}
          style={{ cursor: "pointer" }}
          title={`点此私聊 ${info.nm}`}
        >
          {info.ab}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="who">
            <b>{info.nm}</b>
            <span className="t">{msg.t || ""}</span>
            {expandable && (
              <button
                className={`collapse-btn${collapsed ? "" : " on"}`}
                onClick={onToggle}
                style={{ marginLeft: "auto" }}
                title={collapsed ? "展开推理与工具调用过程" : "收起过程"}
              >
                {hasThinking ? "🧠" : "🔧"} 过程 · {stepCount}
                <span className="cb-caret">{collapsed ? "▸" : "▾"}</span>
              </button>
            )}
            <span style={expandable ? undefined : { marginLeft: "auto" }}>
              <DeleteMsgBtn onDelete={onDelete} />
            </span>
          </div>
          {/* Process in the middle — the model's narration and each tool call,
              nested in the real order they happened (think → verify), rendered
              ABOVE the answer so the final conclusion always sits at the bottom. */}
          {expandable && !collapsed && <AgentTrail msg={msg} steps={process} />}
          <div
            className={`bubble${expandable ? " answer" : ""}`}
            dangerouslySetInnerHTML={{ __html: mdLite(answer) }}
          />
        </div>
      </div>
    );
  }

  return null;
}

/** Vertical activity trail: the model's narration and each tool call, nested in
 *  the order they happened. Narration text shows inline (the agent "thinking out
 *  loud"); tool calls stay collapsible so the operator opens only what they want. */
function AgentTrail({ msg, steps }: { msg: any; steps?: TimelineStep[] }) {
  const items: TimelineStep[] = steps
    ?? ((msg.toolCalls || []).map((tc: any) => ({ type: "tool", ...tc })) as TimelineStep[]);
  let toolN = 0;
  return (
    <div className="trail">
      {msg.thinking && (
        <TrailItem
          kind="think"
          icon="💭"
          label="思考过程"
          body={<div className="dtl-think">{msg.thinking}</div>}
        />
      )}
      {items.map((st, i) => {
        if (st.type === "text") {
          if (!st.text.trim()) return null;
          // intermediate narration between tool calls — the connective reasoning
          return (
            <div key={i} className="trail-say"
              dangerouslySetInnerHTML={{ __html: mdLite(st.text) }} />
          );
        }
        toolN += 1;
        const ti = toolInfo(st.tool);
        return (
          <TrailItem
            key={i}
            kind="tool"
            index={toolN}
            icon={ti.icon}
            label={ti.label || st.tool}
            rawName={ti.label ? st.tool : undefined}
            meta={st.text}
            body={
              st.result
                ? <pre className="dtl-out">{st.result}</pre>
                : <div className="dtl-empty">（无返回内容）</div>
            }
          />
        );
      })}
    </div>
  );
}

function TrailItem({
  kind, icon, label, meta, body, index, rawName,
}: {
  kind: "think" | "tool";
  icon: string;
  label: string;
  meta?: string;
  body: ReactNode;
  index?: number;
  rawName?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`dtl ${kind}${open ? " open" : ""}`}>
      <button className="dtl-hd" onClick={() => setOpen((o) => !o)} title={rawName || label}>
        <span className="dtl-caret">▸</span>
        <span className="dtl-ic">{icon}</span>
        {index != null && <span className="dtl-step">{index}</span>}
        <span className="dtl-label">{label}</span>
        {meta && <span className="dtl-meta" title={meta}>{meta}</span>}
      </button>
      {open && <div className="dtl-body">{body}</div>}
    </div>
  );
}

/* ─── Tool display registry ─── */
interface ToolInfo { icon: string; label: string; desc: string; color: string }
const TOOL_REGISTRY: Record<string, ToolInfo> = {
  list_dir:    { icon: "📂", label: "列目录",   desc: "列出仓库目录内容",       color: "#3a6ea5" },
  read_file:   { icon: "📄", label: "读文件",   desc: "读取仓库中的文件",       color: "#3a6ea5" },
  grep:        { icon: "🔍", label: "搜索",     desc: "全文搜索代码仓库",       color: "#3a6ea5" },
  find_symbol: { icon: "🧭", label: "符号",     desc: "定位符号的定义与调用点（顺调用链追代码）", color: "#3a6ea5" },
  repo_map:    { icon: "🗺️", label: "仓库地图", desc: "生成仓库结构化地图（目录树+符号+依赖+git）", color: "#3a6ea5" },
  explore:     { icon: "🔭", label: "探查",     desc: "只读子代理横扫仓库调查问题并综合结论", color: "#6a5acd" },
  create_project: { icon: "🆕", label: "建项目", desc: "创建可写的本地项目工作区并绑定到群", color: "#2e8b57" },
  write_file:  { icon: "✏️", label: "写文件",   desc: "写入或覆盖仓库文件",     color: "#a95f36" },
  run_command: { icon: "▶️", label: "跑命令",   desc: "在仓库目录执行命令",     color: "#a95f36" },
};
const TOOL_DEFAULT: ToolInfo = { icon: "🔧", label: "", desc: "", color: "var(--tx3)" };

/** Resolve display info for any tool name — built-in, MCP, or unknown. */
function toolInfo(name: string): ToolInfo {
  const r = TOOL_REGISTRY[name];
  if (r) return r;
  // MCP tools: mcp__server__tool_name
  if (name.startsWith("mcp__")) {
    const parts = name.split("__");
    const server = parts[1] || "mcp";
    const tool = parts.slice(2).join("__") || "";
    return {
      icon: "🔌",
      label: tool || name,
      desc: `MCP · ${server}`,
      color: "#8a6d4f",
    };
  }
  return { ...TOOL_DEFAULT, label: name, desc: "" };
}

function StreamingBubble({ role, text, steps }: {
  role?: string; text: string; steps?: TimelineStep[];
}) {
  const info = ROLES[role as keyof typeof ROLES] || { nm: role, ab: "??" };
  // Live ordered timeline: narration and tool calls interleaved as they arrive.
  // Fall back to a lone text step for older events that only carry `text`.
  const tl: TimelineStep[] = steps?.length ? steps : text ? [{ type: "text", text }] : [];
  const last = tl[tl.length - 1];
  const working = last?.type === "tool";
  return (
    <div className="row enter">
      <div className={`av ${role || ""}`}>{info.ab}</div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="who">
          <b>{info.nm}</b>
          <span className="t">{working ? "操作中…" : "思考中…"}</span>
        </div>
        {tl.length ? (
          <div className="live-timeline">
            {tl.map((st, i) => {
              const isLast = i === tl.length - 1;
              if (st.type === "text") {
                if (!st.text) return isLast ? <div key={i} className="bubble answer"><TypingDots /></div> : null;
                return (
                  <div key={i} className="bubble answer">
                    <span className="stream-txt" dangerouslySetInnerHTML={{ __html: mdLite(st.text) }} />
                    {isLast && <span className="caret" />}
                  </div>
                );
              }
              const ti = toolInfo(st.tool);
              return (
                <div key={i} className={`live-tool${isLast ? " active" : " done"}`}
                  title={ti.label ? `${ti.label} (${st.tool})` : st.tool}>
                  <span className="lt-ic">{ti.icon}</span>
                  <span className="lt-name">{ti.label || st.tool}</span>
                  {st.text && <span className="lt-arg">{esc(st.text)}</span>}
                  {isLast ? <span className="lt-spin" /> : <span className="lt-ok">✓</span>}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="bubble answer"><TypingDots /></div>
        )}
      </div>
    </div>
  );
}

function TypingDots() {
  return (
    <span className="typing"><i /><i /><i /></span>
  );
}
