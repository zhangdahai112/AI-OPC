/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState } from "react";
import { useAppState } from "../context";
import { ROLES, STATE_CN, PT } from "../constants";
import { esc, fmt } from "../utils";
import * as api from "../api";

export default function ContextPanel() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const { activeChannel, projects, llm } = useAppState() as any;

  if (!activeChannel) return <div />;

  const ch = activeChannel;
  const projectIds = (ch.projects || []).map((p: any) => p.project_id);
  const chProjects = projects.filter((p: any) => projectIds.includes(p.id));

  return (
    <>
      {/* LLM status */}
      {llm?.name && (
        <div className="ctxsec" style={{ padding: "12px 18px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
            <span className={`provider-dot ${llm.available && llm.key_configured ? "on" : "off"}`} />
            <span style={{ color: "var(--tx2)" }}>LLM</span>
            <span style={{ color: "var(--tx)" }}>{llm.name}</span>
            {llm.model && (
              <span style={{ color: "var(--tx3)", fontFamily: "var(--mono)", fontSize: 11, marginLeft: "auto" }}>
                {llm.model}
              </span>
            )}
          </div>
        </div>
      )}

      <CrewSection members={ch.members || []} channelId={ch.id} />
      <ProjectsSection projects={chProjects} channelId={ch.id} />
      <MemoryRecallSection />
    </>
  );
}

function CrewSection({ members, channelId }: { members: any[]; channelId: string }) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const { openPrivateChat } = useAppState() as any;
  return (
    <div className="ctxsec">
      <div className="ctxhd">群里成员</div>
      {members.map((m: any) => {
        const info = ROLES[m.role as keyof typeof ROLES] || { ab: "?", nm: m.role };
        return (
          <div key={m.role} className="crew">
            <div
              className={`av ${m.role}`}
              style={{ cursor: "pointer" }}
              onClick={() => openPrivateChat(channelId, m.role)}
              title={`点此私聊 ${info.nm}`}
            >
              {info.ab}
            </div>
            <div className="nm">{info.nm}</div>
            <div className={`st ${m.state}`}>
              <span className="sdot" />
              {STATE_CN[m.state as keyof typeof STATE_CN] || m.state}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ProjectsSection({ projects, channelId }: { projects: any[]; channelId: string }) {
  return (
    <div className="ctxsec">
      <div className="ctxhd">关联项目 <span style={{ fontWeight: 400, color: "var(--tx3)" }}>{projects.length} 个</span></div>
      {projects.map((p: any) => (
        <div key={p.id} className="proj-row">
          <span className="proj-name">{esc(p.name)}</span>
          <span className={`pill ${p.status}`}>{p.status}</span>
        </div>
      ))}
      {projects.length === 0 && (
        <div style={{ fontSize: 12, color: "var(--tx3)" }}>暂无关联项目</div>
      )}
    </div>
  );
}

function MemoryRecallSection() {
  const [query, setQuery] = useState("");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [results, setResults] = useState<any[]>([]);
  const [searched, setSearched] = useState(false);

  const handleRecall = async () => {
    const q = query.trim() || "超时";
    try {
      const hits = await api.recallMemory(q);
      setResults(hits);
      setSearched(true);
    } catch {
      setResults([]);
      setSearched(true);
    }
  };

  return (
    <div className="ctxsec">
      <div className="ctxhd">
        记忆检索 <span style={{ color: "var(--tx3)", fontWeight: 400 }}>grep</span>
      </div>
      <div className="recall">
        <input
          placeholder="搜历史决策、踩过的坑…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") handleRecall(); }}
        />
        <button className="btn" style={{ padding: "7px 11px" }} onClick={handleRecall}>找</button>
      </div>
      {searched && results.length === 0 && (
        <div className="rres">
          <div className="rs">无命中</div>
          记忆里没有匹配「{esc(query || "超时")}」的内容
        </div>
      )}
      {results.map((h, i) => (
        <div key={i} className="rres">
          <div className="rs">{h.scope} · {esc(h.source)}:{h.line}</div>
          {esc(h.text).slice(0, 200)}
        </div>
      ))}
    </div>
  );
}
