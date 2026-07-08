import { type ReactNode } from "react";
import { useAppState } from "../context";
import ChannelRail from "./ChannelRail";
import ContextPanel from "./ContextPanel";
import Icon, { type IconName } from "./ui/Icon";

type View = "channels" | "projects" | "metrics" | "config" | "market";

const TABS: [View, string, IconName][] = [
  ["channels", "群聊", "chat"],
  ["projects", "项目", "folder"],
  ["market", "市场", "store"],
  ["metrics", "指标", "chart"],
  ["config", "配置", "settings"],
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
          <div className="logo">
            <Icon name="node" size={17} />
          </div>
          <b>AI-OPC</b>
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
              <span className="ic"><Icon name={icon} size={18} /></span>
              <span className="lbl">{label}</span>
            </button>
          ))}
        </div>

        {/* Rail content */}
        <div className="railscroll">
          {view === "channels" && <ChannelRail />}
          {view === "projects" && <ProjectsRail />}
          {view === "market" && <MarketRail />}
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
            <span className="ic"><Icon name="folder" size={15} /></span>
            {p.name}
            <span className="pill" style={{ marginLeft: "auto" }}>{p.status}</span>
          </div>
        ))}
      </div>
    </>
  );
}

function MarketRail() {
  const cats: [string, string][] = [
    ["mcp", "MCP 连接器"],
    ["skills", "Skills 技能"],
  ];
  return (
    <div className="cfgnav">
      <div className="grouphd">市场</div>
      {cats.map(([key, label]) => (
        <div
          key={key}
          className="item"
          onClick={() => document.dispatchEvent(new CustomEvent("set-market-tab", { detail: key }))}
        >
          <span className="ic"><Icon name={key === "mcp" ? "plug" : "layers"} size={16} /></span>
          {label}
        </div>
      ))}
    </div>
  );
}

function MetricsRail() {
  return (
    <div className="cfgnav">
      <div className="item on">
        <span className="ic"><Icon name="chart" size={15} /></span>平台自指标
      </div>
    </div>
  );
}

function ConfigRail() {
  const sections: [string, string, IconName][] = [
    ["agents", "团队成员", "users"],
    ["templates", "工单流程", "layers"],
    ["gates", "验收门", "check"],
    ["skills", "规约 / 技能", "ruler"],
    ["llm", "LLM 供应商", "cpu"],
    ["repos", "仓库与凭证", "key"],
    ["integrations", "集成", "plug"],
    ["budget", "审批与预算", "scale"],
  ];
  return (
    <div className="cfgnav">
      {sections.map(([key, label, icon]) => (
        <div
          key={key}
          className="item"
          onClick={() => document.dispatchEvent(new CustomEvent("set-cfg-section", { detail: key }))}
        >
          <span className="ic"><Icon name={icon} size={15} /></span>
          {label}
        </div>
      ))}
    </div>
  );
}
