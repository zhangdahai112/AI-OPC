import { useRef, useEffect, useState, useCallback, type ReactNode } from "react";
import { useAppState } from "../context";
import { ROLES, STATE_CN } from "../constants";
import { esc, mdLite } from "../utils";
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
          />
        ))}
        {Object.entries(streaming).map(([id, s]: [string, any]) => (
          <StreamingBubble key={id} role={s.role} text={s.text} tools={s.tools} />
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
    </>
  );
}

interface MessageBubbleProps {
  msg: any;
  collapsed: boolean;
  onToggle: () => void;
  onAvatarClick: () => void;
  onDelete?: () => void;
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

function MessageBubble({ msg, collapsed, onToggle, onAvatarClick, onDelete }: MessageBubbleProps) {
  if (msg.kind === "sys") {
    return <div className="sys"><span>{esc(msg.text || "")}</span></div>;
  }

  if (msg.kind === "human") {
    return (
      <div className="row human enter">
        <div className="av">你</div>
        <div style={{ flex: 1 }}>
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
    const toolCount = msg.toolCalls?.length || 0;
    const stepCount = (hasThinking ? 1 : 0) + toolCount;
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
        <div style={{ flex: 1 }}>
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
          <div
            className={`bubble${expandable ? " answer" : ""}`}
            dangerouslySetInnerHTML={{ __html: mdLite(msg.html || "") }}
          />
          {/* Reasoning + tool-call trail — each sub-step independently expandable */}
          {expandable && !collapsed && <AgentTrail msg={msg} />}
        </div>
      </div>
    );
  }

  return null;
}

/** Vertical activity trail: the model's thinking and each tool call become
 *  their own collapsible row, so the operator opens only what they care about
 *  instead of one wall-of-text box. */
function AgentTrail({ msg }: { msg: any }) {
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
      {msg.toolCalls?.map((tc: any, i: number) => {
        const ti = toolInfo(tc.tool);
        return (
          <TrailItem
            key={i}
            kind="tool"
            index={i + 1}
            icon={ti.icon}
            label={ti.label || tc.tool}
            rawName={ti.label ? tc.tool : undefined}
            meta={tc.text}
            body={
              tc.result
                ? <pre className="dtl-out">{tc.result}</pre>
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
  repo_map:    { icon: "🗺️", label: "仓库地图", desc: "生成仓库结构化地图（目录树+符号+依赖+git）", color: "#3a6ea5" },
  explore:     { icon: "🔭", label: "探查",     desc: "只读子代理横扫仓库调查问题并综合结论", color: "#6a5acd" },
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

function StreamingBubble({ role, text, tools }: {
  role?: string; text: string; tools?: { tool: string; text: string }[];
}) {
  const info = ROLES[role as keyof typeof ROLES] || { nm: role, ab: "??" };
  return (
    <div className="row enter">
      <div className={`av ${role || ""}`}>{info.ab}</div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="who">
          <b>{info.nm}</b>
          <span className="t">{tools?.length ? "操作中…" : "思考中…"}</span>
        </div>
        {/* 工具调用 = 过程区，和「思考」同一套视觉语言，与下方 LLM 回答明确区分 */}
        {!!tools?.length && (
          <div className="proc live">
            <div className="proc-hd">
              <span className="proc-ic">🔧</span>
              <span>工具调用 · 真实执行</span>
              <span className="proc-tag">过程</span>
            </div>
            <div className="proc-tools">
              {tools.map((tc, i) => {
                const last = i === tools.length - 1;
                const ti = toolInfo(tc.tool);
                return (
                  <div key={i} className={`live-tool${last ? " active" : " done"}`} title={ti.label ? `${ti.label} (${tc.tool})` : tc.tool}>
                    <span className="lt-ic">{ti.icon}</span>
                    <span className="lt-name">{ti.label || tc.tool}</span>
                    {tc.text && <span className="lt-arg">{esc(tc.text)}</span>}
                    {last ? <span className="lt-spin" /> : <span className="lt-ok">✓</span>}
                  </div>
                );
              })}
            </div>
          </div>
        )}
        {/* LLM 输出 = 回答区。工具执行阶段（有工具、暂无正文）先不显示空气泡 */}
        {(text || !tools?.length) && (
          <div className="bubble answer">
            {text ? (
              <span className="stream-txt" dangerouslySetInnerHTML={{ __html: mdLite(text) }} />
            ) : (
              !tools?.length && <TypingDots />
            )}
            {text && <span className="caret" />}
          </div>
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
