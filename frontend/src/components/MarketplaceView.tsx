import { useEffect, useState } from "react";
import { useAppState } from "../context";
import * as api from "../api";
import type { MarketCard, InstalledCatalog } from "../types";

type Tab = "mcp" | "skills";

/** Marketplace — a dedicated, productized market page. Pulls live listings from
 *  real sources (official MCP Registry + Smithery for connectors; the skill store
 *  for skills) and installs to the platform catalog with one click. Agents then
 *  attach from the installed catalog in Agent Studio. */
export default function MarketplaceView() {
  const { toast } = useAppState();
  const [tab, setTab] = useState<Tab>("mcp");
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [cards, setCards] = useState<MarketCard[]>([]);
  const [installed, setInstalled] = useState<InstalledCatalog>({ mcp: [], skills: [] });
  const [busyId, setBusyId] = useState<string>("");

  const loadInstalled = () =>
    api.getInstalledCatalog().then(setInstalled).catch(() => {});

  const search = async (t: Tab, query: string) => {
    setLoading(true);
    try {
      const r = t === "mcp"
        ? await api.searchMarketMcp(query)
        : await api.searchMarketSkills(query);
      setCards(r);
    } catch {
      setCards([]);
      toast("市场拉取失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadInstalled(); }, []);
  useEffect(() => { search(tab, ""); setQ(""); /* eslint-disable-next-line */ }, [tab]);
  useEffect(() => {
    const h = (e: Event) => setTab((e as CustomEvent).detail === "skills" ? "skills" : "mcp");
    document.addEventListener("set-market-tab", h);
    return () => document.removeEventListener("set-market-tab", h);
  }, []);

  const mcpIds = new Set(installed.mcp.map((x) => x.id));
  const skillIds = new Set(installed.skills.map((x) => x.id));
  const isInstalled = (c: MarketCard) =>
    tab === "mcp" ? mcpIds.has(c.id) : skillIds.has(c.id);

  const install = async (c: MarketCard) => {
    setBusyId(c.id);
    try {
      if (tab === "mcp") await api.installMarketMcp(c);
      else await api.installSkill(c.id, c.source);
      toast(`已安装 ${c.name}`);
      await loadInstalled();
    } catch {
      toast("安装失败");
    } finally {
      setBusyId("");
    }
  };

  const uninstall = async (c: MarketCard) => {
    setBusyId(c.id);
    try {
      if (tab === "mcp") await api.uninstallMarketMcp(c.id);
      else await api.uninstallSkill(c.id);
      await loadInstalled();
    } catch {
      toast("移除失败");
    } finally {
      setBusyId("");
    }
  };

  const installedCount = tab === "mcp" ? installed.mcp.length : installed.skills.length;

  return (
    <div className="cfgwrap">
      <h2>市场</h2>
      <p className="lead">
        直接对接真实市场，一键安装到平台。装好的在 <b>项目 → Agent 配置</b> 里从已装目录挑选挂载。
      </p>

      {/* sub-tabs */}
      <div className="mkt-tabs">
        <button className={`seg-btn${tab === "mcp" ? " on" : ""}`} onClick={() => setTab("mcp")}>
          🔌 MCP 连接器
        </button>
        <button className={`seg-btn${tab === "skills" ? " on" : ""}`} onClick={() => setTab("skills")}>
          🧩 Skills 技能
        </button>
        <span className="ds" style={{ marginLeft: "auto", alignSelf: "center" }}>
          已安装 {installedCount}
        </span>
      </div>

      {/* search */}
      <div className="recall" style={{ marginBottom: 16 }}>
        <input
          placeholder={tab === "mcp" ? "搜索 MCP 服务器（github / postgres / 浏览器…）" : "搜索技能（PDF / 代码审查…）"}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") search(tab, q.trim()); }}
        />
        <button className="btn" onClick={() => search(tab, q.trim())} disabled={loading}>
          {loading ? "搜索中…" : "搜索"}
        </button>
      </div>

      {loading && cards.length === 0 && (
        <div className="ds" style={{ padding: "20px 0" }}>正在拉取真实市场清单…</div>
      )}
      {!loading && cards.length === 0 && (
        <div className="ds" style={{ padding: "20px 0" }}>无结果。换个关键词试试。</div>
      )}

      <div className="mkt-grid">
        {cards.map((c) => {
          const done = isInstalled(c);
          const busy = busyId === c.id;
          return (
            <div key={c.id} className="mkt-card">
              <div className="mc-top">
                <div className="mc-ic">{tab === "mcp" ? "🔌" : "🧩"}</div>
                <div className="mc-name" title={c.name}>{c.name}</div>
                {c.verified && <span className="tag" title="已验证">✓</span>}
              </div>
              <div className="mc-desc">{c.description || "（无描述）"}</div>
              <div className="mc-foot">
                <span className="tag mono">{c.source}</span>
                {c.transport && <span className="tag">{c.transport}</span>}
                {typeof c.useCount === "number" && c.useCount > 0 && (
                  <span className="ds" style={{ fontSize: 11 }}>↑{c.useCount}</span>
                )}
                {c.homepage && (
                  <a className="ds" style={{ fontSize: 11 }} href={c.homepage} target="_blank" rel="noreferrer">详情</a>
                )}
                {done ? (
                  <button className="btn" style={{ marginLeft: "auto", padding: "5px 12px" }}
                    onClick={() => uninstall(c)} disabled={busy}>
                    {busy ? "…" : "✓ 已装 · 移除"}
                  </button>
                ) : (
                  <button className="btn primary" style={{ marginLeft: "auto", padding: "5px 14px" }}
                    onClick={() => install(c)} disabled={busy}>
                    {busy ? "安装中…" : "一键安装"}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
