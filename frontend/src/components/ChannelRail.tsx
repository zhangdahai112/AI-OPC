import { useState } from "react";
import { useAppState } from "../context";
import { ROLES } from "../constants";
import * as api from "../api";

export default function ChannelRail() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const { channels, activeChannelId, setActiveChannelId, refreshChannels, toast } = useAppState() as any;
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const handleDelete = async (cid: string) => {
    try {
      await api.deleteChannel(cid);
      if (activeChannelId === cid) setActiveChannelId(null);
      toast("群聊已删除");
      refreshChannels();
    } catch {
      toast("删除失败");
    }
    setConfirmDelete(null);
  };

  const need = channels.filter((c: any) => c.status === "blocked" || c.status === "escalated");
  const rest = channels.filter((c: any) => c.status !== "blocked" && c.status !== "escalated");

  return (
    <>
      <button
        className="newbtn"
        onClick={() => document.dispatchEvent(new CustomEvent("open-new-channel"))}
      >
        ＋ 新建群聊
      </button>

      {channels.length === 0 && (
        <div style={{ padding: "40px 16px", textAlign: "center", color: "var(--tx3)", fontSize: 13 }}>
          暂无群聊，点击上方按钮创建一个
        </div>
      )}

      {need.map((c: any) => (
        <ChannelRow key={c.id} channel={c} active={c.id === activeChannelId}
          onSelect={() => setActiveChannelId(c.id)}
          confirmDelete={confirmDelete === c.id}
          onDeleteClick={() => setConfirmDelete(c.id)}
          onConfirmDelete={() => handleDelete(c.id)}
          onCancelDelete={() => setConfirmDelete(null)}
          attention
        />
      ))}

      {rest.map((c: any) => (
        <ChannelRow key={c.id} channel={c} active={c.id === activeChannelId}
          onSelect={() => setActiveChannelId(c.id)}
          confirmDelete={confirmDelete === c.id}
          onDeleteClick={() => setConfirmDelete(c.id)}
          onConfirmDelete={() => handleDelete(c.id)}
          onCancelDelete={() => setConfirmDelete(null)}
        />
      ))}
    </>
  );
}

interface ChannelRowProps {
  channel: any;
  active: boolean;
  onSelect: () => void;
  confirmDelete: boolean;
  onDeleteClick: () => void;
  onConfirmDelete: () => void;
  onCancelDelete: () => void;
  attention?: boolean;
}

function ChannelRow({
  channel, active, onSelect,
  confirmDelete, onDeleteClick, onConfirmDelete, onCancelDelete,
  attention,
}: ChannelRowProps) {
  const avatars = channel.members?.slice(0, 4) || [];
  return (
    <div
      className={`chan${active ? " on" : ""}${attention ? " attention" : ""}`}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 4 }} onClick={onSelect}>
        {attention && <span className="needdot" />}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="chan-top">
            <span className="ctitle">{channel.name}</span>
          </div>
          <div className="chan-meta">
            <div className="avatars">
              {avatars.map((m: any) => {
                const info = (ROLES as Record<string, { nm: string; ab: string }>)[m.role];
                return (
                  <span key={m.role} className={`av-chip ${m.role}`}
                        title={`${info?.nm || m.role} · ${m.state}`}>
                    {info?.ab || m.role?.slice(0, 2)}
                  </span>
                );
              })}
            </div>
            <span className={`badge ${channel.status}`}>
              <span className="bdot" />
              {channel.status === "active" ? "活跃" : channel.status === "done" ? "已结束" : channel.status}
            </span>
          </div>
        </div>

        {confirmDelete ? (
          <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
            <button className="btn danger" style={{ padding: "2px 6px", fontSize: 11 }}
              onClick={(e) => { e.stopPropagation(); onConfirmDelete(); }}>
              确认
            </button>
            <button className="btn" style={{ padding: "2px 6px", fontSize: 11 }}
              onClick={(e) => { e.stopPropagation(); onCancelDelete(); }}>
              取消
            </button>
          </div>
        ) : (
          <button className="chan-del" title="删除群聊"
            onClick={(e) => { e.stopPropagation(); onDeleteClick(); }}
            style={{ opacity: 0.4, fontSize: 14, padding: "2px 4px", borderRadius: 4, flexShrink: 0 }}
            onMouseEnter={(e) => (e.currentTarget.style.opacity = "1")}
            onMouseLeave={(e) => (e.currentTarget.style.opacity = "0.4")}>
            ×
          </button>
        )}
      </div>
    </div>
  );
}
