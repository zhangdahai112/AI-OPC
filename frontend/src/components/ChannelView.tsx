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
}

function MessageBubble({ msg, collapsed, onToggle, onAvatarClick }: MessageBubbleProps) {
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
          </div>
          <div
            className="bubble"
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
      {msg.toolCalls?.map((tc: any, i: number) => (
        <TrailItem
          key={i}
          kind="tool"
          index={i + 1}
          icon={TOOL_ICON[tc.tool] || "🔧"}
          label={tc.tool}
          meta={tc.text}
          body={
            tc.result
              ? <pre className="dtl-out">{tc.result}</pre>
              : <div className="dtl-empty">（无返回内容）</div>
          }
        />
      ))}
    </div>
  );
}

function TrailItem({
  kind, icon, label, meta, body, index,
}: {
  kind: "think" | "tool";
  icon: string;
  label: string;
  meta?: string;
  body: ReactNode;
  index?: number;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`dtl ${kind}${open ? " open" : ""}`}>
      <button className="dtl-hd" onClick={() => setOpen((o) => !o)}>
        <span className="dtl-caret">▸</span>
        <span className="dtl-ic">{icon}</span>
        {index != null && <span className="dtl-step">{index}</span>}
        <span className="dtl-label">{label}</span>
        {meta && <span className="dtl-meta">{meta}</span>}
      </button>
      {open && <div className="dtl-body">{body}</div>}
    </div>
  );
}

const TOOL_ICON: Record<string, string> = {
  read_file: "📄", list_dir: "📂", grep: "🔍",
  write_file: "✏️", run_command: "▶️",
};

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
        {!!tools?.length && (
          <div className="live-tools">
            {tools.map((tc, i) => {
              const last = i === tools.length - 1;
              return (
                <div key={i} className={`live-tool${last ? " active" : " done"}`}>
                  <span className="lt-ic">{TOOL_ICON[tc.tool] || "🔧"}</span>
                  <span className="lt-name">{tc.tool}</span>
                  {tc.text && <span className="lt-arg">{esc(tc.text)}</span>}
                  {last ? <span className="lt-spin" /> : <span className="lt-ok">✓</span>}
                </div>
              );
            })}
          </div>
        )}
        <div className="bubble">
          {text ? (
            <span className="stream-txt" dangerouslySetInnerHTML={{ __html: mdLite(text) }} />
          ) : (
            !tools?.length && <TypingDots />
          )}
          {text && <span className="caret" />}
        </div>
      </div>
    </div>
  );
}

function TypingDots() {
  return (
    <span className="typing"><i /><i /><i /></span>
  );
}
