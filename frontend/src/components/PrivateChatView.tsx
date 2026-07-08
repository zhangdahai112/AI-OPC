import { useRef, useEffect, useState } from "react";
import { useAppState } from "../context";
import { ROLES } from "../constants";
import { esc, mdLite } from "../utils";
import * as api from "../api";

/* 1:1 private chat with a single agent, scoped to the channel's project(s) */
export default function PrivateChatView() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const {
    privateChat, privateChatMessages, toast, closePrivateChat,
    refreshPrivateChat,
  } = useAppState() as any;
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const streamRef = useRef<HTMLDivElement>(null);

  const { channelId, role } = privateChat || {};
  const info = ROLES[role as keyof typeof ROLES] || { nm: role, ab: "??" };

  useEffect(() => {
    if (streamRef.current) {
      streamRef.current.scrollTop = streamRef.current.scrollHeight;
    }
  }, [privateChatMessages]);

  if (!privateChat) return null;

  const handleSend = async () => {
    const v = text.trim();
    if (!v || !channelId) return;
    setText("");
    setSending(true);
    try {
      // send to channel messages (agent receives it via chat routing)
      await api.sendChannelMessage(channelId, v);
      setTimeout(() => refreshPrivateChat(), 500);
    } catch {
      toast("发送失败");
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

  return (
    <div className="private-chat">
      {/* Header */}
      <div className="pchat-hd">
        <button className="btn" onClick={closePrivateChat}>← 返回群聊</button>
        <div className={`av ${role}`}>{info.ab}</div>
        <span>{info.nm} · 私聊</span>
      </div>

      {/* Messages */}
      <div className="pchat-msgs" ref={streamRef}>
        {privateChatMessages.length === 0 && (
          <div className="pchat-empty">
            这是你和 {info.nm} 的私聊记录（来自该群聊的上下文）。
          </div>
        )}
        {privateChatMessages.map((msg: any, i: number) => (
          <div key={msg.id || i} className={`row ${msg.kind === "human" ? "human" : ""}`}>
            <div className={`av ${msg.role || ""}`}>
              {msg.kind === "human" ? "你" : (ROLES[msg.role as keyof typeof ROLES] || { ab: "??" }).ab}
            </div>
            <div style={{ flex: 1 }}>
              <div className="who">
                <b>{msg.kind === "human" ? "你" : (ROLES[msg.role as keyof typeof ROLES] || { nm: msg.role }).nm}</b>
                <span className="t">{msg.t || ""}</span>
              </div>
              <div className="bubble"
                   dangerouslySetInnerHTML={{ __html: mdLite(msg.html || msg.text || "") }} />
            </div>
          </div>
        ))}
      </div>

      {/* Composer */}
      <div className="composer">
        <div className="cbox">
          <textarea
            rows={1}
            placeholder={`私信 ${info.nm}…`}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={sending}
          />
          <button className="send" onClick={handleSend} disabled={sending}>↑</button>
        </div>
        <div className="chint">发送给 {info.nm}，该 agent 在本群上下文中回复</div>
      </div>
    </div>
  );
}
