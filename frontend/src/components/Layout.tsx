import { type ReactNode } from "react";
import { useAppState } from "../context";
import ChannelRail from "./ChannelRail";
import ContextPanel from "./ContextPanel";

type View = "channels" | "projects" | "metrics" | "config";

const TABS: [View, string, string][] = [
  ["channels", "群聊", "💬"],
  ["projects", "项目", "📁"],
  ["metrics", "指标", "📊"],
  ["config", "配置", "⚙"],
];

export default function Layout({ center }: { center: ReactNode }) {
  const { view, setView, llm } = useAppState();

  const activeProvider = llm?.name || null;
  const providerOk = llm?.available && llm?.key_configured;

  const providerLabel =
    activeProvider === "Anthropic Claude" ? "An"
    : activeProvider === "DeepSeek" ? "DS"
    : activeProvider === "OpenAI 兼容" ? "AI"
    : (activeProvider?.slice(0, 2) ?? "??");
  const wsTitle = providerOk ? `已连 · ${activeProvider}` : "未接 LLM";

  return (
    <div className={`app${view !== "channels" ? " app-wide" : ""}`}>
      {/* Left rail */}
      <aside className="rail">
        <div className="brand">
          <div className="logo">群</div>
          <b>作战群</b>
          <span className="wsdot" title={wsTitle} />
          {activeProvider && (
            <span
              className={`provider-badge ${providerOk ? "on" : "off"}`}
              onClick={() => setView("config")}
            >
              {providerLabel}
            </span>
          )}
        </div>

        {/* Nav tabs */}
        <div className="tabs">
          {TABS.map(([key, label, icon]) => (
            <button
              key={key}
              className={`tab${view === key ? " on" : ""}`}
              onClick={() => setView(key)}
            >
              <span className="ic">{icon}</span>
              {label}
            </button>
          ))}
        </div>

        {/* Rail content */}
        <div className="railscroll">
          {view === "channels" && <ChannelRail />}
          {view === "projects" && <ProjectsRail />}
          {view === "metrics" && <MetricsRail />}
          {view === "config" && <ConfigRail />}
        </div>
      </aside>

      {/* Center */}
      <main className="center">{center}</main>

      {/* Right context panel (channels view only) */}
      {view === "channels" && (
        <aside className="ctx">
          <ContextPanel />
        </aside>
      )}
    </div>
  );
}

/* ─── Rail sub-components ─── */
function ProjectsRail() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const { projects, activeProject, setActiveProject } = useAppState() as any;
  return (
    <>
      <button className="newbtn" onClick={() => document.dispatchEvent(new CustomEvent("open-new-project"))}>
        ＋ 新建项目
      </button>
      <div className="cfgnav">
        {projects.length === 0 && <div className="item">还没有项目</div>}
        {projects.map((p: any) => (
          <div
            key={p.id}
            className={`item${activeProject?.id === p.id ? " on" : ""}`}
            onClick={() => setActiveProject(p)}
          >
            <span className="ic">📁</span>
            {p.name}
            <span className="pill" style={{ marginLeft: "auto" }}>{p.status}</span>
          </div>
        ))}
      </div>
    </>
  );
}

function MetricsRail() {
  return (
    <div className="cfgnav">
      <div className="item on">
        <span className="ic">📊</span>平台自指标
      </div>
    </div>
  );
}

function ConfigRail() {
  const sections: [string, string, string][] = [
    ["agents", "团队成员", "👥"],
    ["templates", "工单流程", "🗂"],
    ["gates", "验收门", "✓"],
    ["skills", "规约 / 技能", "📐"],
    ["llm", "LLM 供应商", "🤖"],
    ["repos", "仓库与凭证", "🔑"],
    ["integrations", "集成", "🔌"],
    ["budget", "审批与预算", "⚖"],
  ];
  return (
    <div className="cfgnav">
      {sections.map(([key, label, icon]) => (
        <div
          key={key}
          className="item"
          onClick={() => document.dispatchEvent(new CustomEvent("set-cfg-section", { detail: key }))}
        >
          <span className="ic">{icon}</span>
          {label}
        </div>
      ))}
    </div>
  );
}
