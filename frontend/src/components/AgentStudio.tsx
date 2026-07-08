import { useEffect, useState } from "react";
import { useAppState } from "../context";
import { ROLES } from "../constants";
import * as api from "../api";
import type {
  AgentManifest, McpMount, AgentSkillRef, InstalledCatalog, InstalledMcp, InstalledSkill,
} from "../types";
import SkillAssist from "./SkillAssist";

/** Set an uncontrolled textarea's value and fire a native input event. */
function fillTextarea(id: string, text: string) {
  const ta = document.getElementById(id) as HTMLTextAreaElement | null;
  if (!ta) return;
  ta.value = text;
  ta.dispatchEvent(new Event("input", { bubbles: true }));
  ta.focus();
}

// built-in tools with short labels (order = platform default order)
const TOOLS: { key: string; label: string }[] = [
  { key: "list_dir", label: "列目录" },
  { key: "read_file", label: "读文件" },
  { key: "grep", label: "搜索" },
  { key: "write_file", label: "写文件" },
  { key: "run_command", label: "跑命令" },
];

/** Agent Studio: per-project, per-role configuration. MCP servers and skills are
 *  no longer hand-entered here — they are installed from the dedicated Market page
 *  and simply *attached* from the installed catalog (one click), which keeps this
 *  panel simple while the catalog stays central and professional. */
export default function AgentStudio({ projectId }: { projectId: string }) {
  const [agents, setAgents] = useState<AgentManifest[]>([]);
  const [catalog, setCatalog] = useState<InstalledCatalog>({ mcp: [], skills: [] });

  const load = () =>
    api.getProjectAgents(projectId).then(setAgents).catch(() => setAgents([]));
  const loadCatalog = () =>
    api.getInstalledCatalog().then(setCatalog).catch(() => {});

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [projectId]);
  useEffect(() => { loadCatalog(); }, []);

  return (
    <>
      <div className="seghd">Agent 配置（按项目 · 未改则继承平台默认）</div>
      {agents.map((m) => (
        <AgentCard key={m.role} m={m} projectId={projectId} catalog={catalog} onSaved={load} />
      ))}
    </>
  );
}

function AgentCard({
  m, projectId, catalog, onSaved,
}: {
  m: AgentManifest;
  projectId: string;
  catalog: InstalledCatalog;
  onSaved: () => void;
}) {
  const { toast, setView } = useAppState();
  const info = ROLES[m.role as keyof typeof ROLES] || { ab: "?", nm: m.role };

  const [tools, setTools] = useState<string[]>(m.harness.builtinTools);
  const [guard, setGuard] = useState<string>((m.prompt.guardrails || []).join("\n"));
  const [busy, setBusy] = useState(false);
  const [mounts, setMounts] = useState<McpMount[]>((m.mcp as McpMount[]) || []);
  const [skills, setSkills] = useState<AgentSkillRef[]>((m.skills as AgentSkillRef[]) || []);

  // reseed when the manifest reloads (e.g. after save / reset)
  useEffect(() => {
    setTools(m.harness.builtinTools);
    setGuard((m.prompt.guardrails || []).join("\n"));
    setMounts((m.mcp as McpMount[]) || []);
    setSkills((m.skills as AgentSkillRef[]) || []);
  }, [m]);

  const mountKey = (mt: McpMount) => `${mt.server}|${mt.url || mt.command || ""}`;

  const persistMounts = async (next: McpMount[]) => {
    setMounts(next);
    try {
      await api.saveProjectAgent(projectId, m.role, { mcp: next });
      toast(`${info.nm} MCP 已更新`);
      onSaved();
    } catch { toast("保存失败"); }
  };
  const attachMount = (im: InstalledMcp) => {
    if (mounts.some((x) => mountKey(x) === mountKey(im.mount))) { toast("已挂载"); return; }
    persistMounts([...mounts, im.mount]);
  };
  const removeMount = (i: number) => persistMounts(mounts.filter((_, idx) => idx !== i));

  const persistSkills = async (next: AgentSkillRef[]) => {
    setSkills(next);
    try {
      await api.saveProjectAgent(projectId, m.role, { skills: next });
      toast(`${info.nm} 技能已更新`);
      onSaved();
    } catch { toast("保存失败"); }
  };
  const attachSkill = (s: InstalledSkill) => {
    if (skills.some((x) => x.id === s.id)) { toast("已挂载"); return; }
    persistSkills([...skills, { id: s.id, source: s.source, version: s.version }]);
  };
  const removeSkill = (id: string) => persistSkills(skills.filter((x) => x.id !== id));

  const toggle = (t: string) =>
    setTools((ts) => (ts.includes(t) ? ts.filter((x) => x !== t) : [...ts, t]));

  const saveManifest = async () => {
    setBusy(true);
    try {
      const guardrails = guard.split("\n").map((s) => s.trim()).filter(Boolean);
      await api.saveProjectAgent(projectId, m.role, {
        harness: { builtinTools: tools, toolPolicy: m.harness.toolPolicy },
        prompt: { guardrails },
      });
      toast(`${info.nm}配置已保存`);
      onSaved();
    } catch { toast("保存失败"); } finally { setBusy(false); }
  };

  const resetDefault = async () => {
    setBusy(true);
    try {
      await api.saveProjectAgent(projectId, m.role, {});
      toast(`${info.nm}已重置为默认`);
      onSaved();
    } catch { toast("重置失败"); } finally { setBusy(false); }
  };

  const saveCharter = async () => {
    const ta = document.getElementById(`char-${m.role}`) as HTMLTextAreaElement;
    if (!ta) return;
    try {
      await api.setAgentMemory(projectId, m.role, ta.value);
      toast(`${info.nm}宪章已保存`);
      onSaved();
    } catch { toast("保存失败"); }
  };

  const availMcp = catalog.mcp.filter((im) => !mounts.some((x) => mountKey(x) === mountKey(im.mount)));
  const availSkills = catalog.skills.filter((s) => !skills.some((x) => x.id === s.id));

  return (
    <div className="secbox">
      <div className="secrow">
        <div className={`av-sm ${m.role}`}>{info.ab}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="ttl">{m.identity.name}</div>
          <div className="ds">{m.identity.focus || "本项目专属配置"}</div>
        </div>
        <div className="right">
          <span className="tag mono" title="模型（继承自 LLM 供应商）">
            {m.model.model || "默认模型"} · {m.model.effort}
          </span>
          <span
            className="pill"
            style={m.inherited ? undefined : {
              background: "var(--agent-bg)", color: "var(--agent)", borderColor: "transparent",
            }}
          >
            {m.inherited ? "继承默认" : "已自定义"}
          </span>
        </div>
      </div>

      <div style={{ padding: "4px 18px 14px" }}>
        {/* tool permissions (least privilege) */}
        <div className="ds" style={{ marginBottom: 7 }}>工具权限（点选授予 · 最小权限）</div>
        <div className="chips" style={{ marginBottom: 14 }}>
          {TOOLS.map((t) => {
            const on = tools.includes(t.key);
            return (
              <button key={t.key} className={on ? "chip" : "pill"}
                onClick={() => toggle(t.key)} title={t.key}>
                {on ? "✓ " : ""}{t.label}
              </button>
            );
          })}
        </div>

        {/* guardrails */}
        <div className="ds" style={{ marginBottom: 7 }}>质量红线（每行一条 · 注入系统提示词）</div>
        <textarea className="memta" rows={2} value={guard}
          onChange={(e) => setGuard(e.target.value)} placeholder="如：不可逆动作先请人审" />
        <div className="actions">
          <button className="btn primary" onClick={saveManifest} disabled={busy}>保存配置</button>
          {!m.inherited && (
            <button className="btn" onClick={resetDefault} disabled={busy}>重置为默认</button>
          )}
        </div>

        {/* MCP — attach from installed catalog */}
        <div className="ds" style={{ margin: "16px 0 7px", display: "flex", alignItems: "center" }}>
          <span>MCP 连接器（从已装挑选）</span>
          <button type="button" className="btn" style={{ marginLeft: "auto", padding: "3px 11px" }}
            onClick={() => setView("market")}>🛒 去市场安装</button>
        </div>
        <div className="chips" style={{ marginBottom: 8 }}>
          {mounts.length === 0 && <span className="ds">尚未挂载</span>}
          {mounts.map((mt, i) => (
            <span key={mountKey(mt) + i} className="chip" title={mt.url || mt.command || ""}>
              {mt.server}
              <button type="button" className="pill" style={{ marginLeft: 4, padding: "0 6px" }}
                onClick={() => removeMount(i)} title="移除">✕</button>
            </span>
          ))}
        </div>
        {availMcp.length > 0 && (
          <div className="picker">
            {availMcp.map((im) => (
              <button key={im.id} type="button" className="pick-chip"
                onClick={() => attachMount(im)} title={im.description}>
                ＋ {im.name}
              </button>
            ))}
          </div>
        )}

        {/* Skills — attach from installed catalog */}
        <div className="ds" style={{ margin: "16px 0 7px", display: "flex", alignItems: "center" }}>
          <span>技能（从已装挑选 · 运行时渐进式激活）</span>
          <button type="button" className="btn" style={{ marginLeft: "auto", padding: "3px 11px" }}
            onClick={() => setView("market")}>🛒 去市场安装</button>
        </div>
        <div className="chips" style={{ marginBottom: 8 }}>
          {skills.length === 0 && <span className="ds">尚未挂载技能</span>}
          {skills.map((s) => (
            <span key={s.id} className="chip">
              {s.id}{s.version ? ` v${s.version}` : ""}
              <button type="button" className="pill" style={{ marginLeft: 4, padding: "0 6px" }}
                onClick={() => removeSkill(s.id)} title="移除">✕</button>
            </span>
          ))}
        </div>
        {availSkills.length > 0 && (
          <div className="picker">
            {availSkills.map((s) => (
              <button key={s.id} type="button" className="pick-chip"
                onClick={() => attachSkill(s)} title={s.description || ""}>
                ＋ {s.name}
              </button>
            ))}
          </div>
        )}

        {/* charter / system prompt */}
        <div className="ds" style={{ margin: "16px 0 7px" }}>系统提示词 / 永久记忆（注入每次回答）</div>
        <textarea className="memta" id={`char-${m.role}`} rows={5} defaultValue={m.prompt.charter} />
        <div className="actions">
          <button className="btn primary" onClick={saveCharter}>保存宪章</button>
          <SkillAssist target="memory" role={m.role} projectId={projectId} label="生成宪章"
            onApply={(t) => { fillTextarea(`char-${m.role}`, t); saveCharter(); }} />
        </div>
      </div>
    </div>
  );
}
