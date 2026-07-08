import { useAppState } from "../context";
import { ROLES, ROLES_DESC, TEMPLATE_NAMES, EXECUTORS } from "../constants";
import { CFG_SECTIONS } from "../constants";
import { useState, useEffect, useCallback, useRef } from "react";
import * as api from "../api";
import type { FullConfig, LLMProvider } from "../types";
import Select from "./ui/Select";

export default function ConfigView() {
  const { config, toast, refreshConfig } = useAppState();
  const [section, setSection] = useState("agents");

  // Listen for custom event from Layout
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (typeof detail === "string") setSection(detail);
    };
    document.addEventListener("set-cfg-section", handler);
    return () => document.removeEventListener("set-cfg-section", handler);
  }, []);

  if (!config) {
    return (
      <div className="cfgwrap">
        <p className="lead">加载配置中…</p>
      </div>
    );
  }

  return (
    <div className="cfgwrap" style={{ overflowY: "auto" }}>
      <ConfigSection
        section={section}
        config={config}
        toast={toast}
      />
    </div>
  );
}

/* ─── Config section router + save ─── */

function ConfigSection({
  section,
  config,
  toast,
}: {
  section: string;
  config: FullConfig;
  toast: (msg: string) => void;
}) {
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const scheduleSave = useCallback(
    (cfg: FullConfig) => {
      clearTimeout(saveTimerRef.current);
      saveTimerRef.current = setTimeout(async () => {
        try {
          await api.saveConfig(cfg);
          toast("已保存");
        } catch {
          toast("保存失败");
        }
      }, 300);
    },
    [toast]
  );

  const [localConfig, setLocalConfig] = useState(config);

  useEffect(() => {
    setLocalConfig(config);
  }, [config]);

  const update = useCallback(
    (path: string[], val: unknown) => {
      const newCfg = structuredClone(localConfig);
      let o: Record<string, unknown> = newCfg as unknown as Record<string, unknown>;
      for (let i = 0; i < path.length - 1; i++) {
        const key = path[i]!;
        if (o[key] == null || typeof o[key] !== "object") {
          o[key] = {};
        }
        o = o[key] as Record<string, unknown>;
      }
      o[path[path.length - 1]!] = val;
      setLocalConfig(newCfg);
      scheduleSave(newCfg);
    },
    [localConfig, scheduleSave]
  );

  const toggle = useCallback(
    (path: string[]) => {
      const newCfg = structuredClone(localConfig);
      let o: Record<string, unknown> = newCfg as unknown as Record<string, unknown>;
      for (let i = 0; i < path.length - 1; i++) {
        const key = path[i]!;
        if (o[key] == null || typeof o[key] !== "object") {
          o[key] = {};
        }
        o = o[key] as Record<string, unknown>;
      }
      o[path[path.length - 1]!] = !o[path[path.length - 1]!];
      setLocalConfig(newCfg);
      scheduleSave(newCfg);
    },
    [localConfig, scheduleSave]
  );

  switch (section) {
    case "agents":
      return <AgentsSection config={localConfig} update={update} />;
    case "templates":
      return <TemplatesSection config={localConfig} update={update} />;
    case "gates":
      return <GatesSection config={localConfig} update={update} toggle={toggle} />;
    case "skills":
      return <SkillsSection config={localConfig} toggle={toggle} />;
    case "repos":
      return <ReposSection config={localConfig} />;
    case "integrations":
      return <IntegrationsSection config={localConfig} toggle={toggle} />;
    case "llm":
      return <LlmSection config={localConfig} update={update} toast={toast} />;
    case "budget":
      return <BudgetSection config={localConfig} update={update} toggle={toggle} />;
    default:
      return null;
  }
}

/* ─── Toggle component ─── */

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return <div className={`tg${on ? " on" : ""}`} onClick={onClick} />;
}

/* ─── Agents ─── */

function AgentsSection({
  config,
  update,
}: {
  config: FullConfig;
  update: (path: string[], val: unknown) => void;
}) {
  return (
    <>
      <h2>团队成员</h2>
      <p className="lead">
        这些是参与工作的 agent。每个 agent 由哪种执行器来干活可以单独选——换执行器不影响安全规则。
      </p>
      <div className="secbox">
        {config.agents.map((a, i) => (
          <div key={a.role} className="secrow">
            <div className={`av-sm ${a.role}`}>{ROLES[a.role].ab}</div>
            <div>
              <div className="ttl">{ROLES[a.role].nm}</div>
              <div className="ds">{ROLES_DESC[a.role]}</div>
            </div>
            <div className="right">
              <Select
                size="sm"
                align="right"
                style={{ width: 148 }}
                value={a.exec}
                onChange={(v) => update(["agents", String(i), "exec"], v)}
                options={[...EXECUTORS]}
              />
              <Toggle on={a.on} onClick={() => update(["agents", String(i), "on"], !a.on)} />
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

/* ─── Templates ─── */

function TemplatesSection({
  config,
  update,
}: {
  config: FullConfig;
  update: (path: string[], val: unknown) => void;
}) {
  return (
    <>
      <h2>工单流程</h2>
      <p className="lead">
        不同类型的工单走不同流程。小工单走快车道（单个 agent 直接干），大工单才建作战群、多角色协作。
      </p>
      {config.templates.map((t, i) => (
        <div key={t.type} className="secbox">
          <div className="secrow">
            <div>
              <div className="ttl">{TEMPLATE_NAMES[t.type]}</div>
              <div className="ds">默认拉入 {t.roster.length} 个角色</div>
            </div>
            <div className="right">
              <span className="pill lane">{t.lane}</span>
              <Select
                size="sm"
                align="right"
                style={{ width: 120 }}
                value={t.lane}
                onChange={(v) => update(["templates", String(i), "lane"], v)}
                options={["快车道", "作战群"]}
              />
            </div>
          </div>
          <div className="secrow">
            <div className="chips">
              {t.roster.map((r) => (
                <span key={r} className={`chip ${r}`}>{ROLES[r].nm}</span>
              ))}
            </div>
          </div>
        </div>
      ))}
    </>
  );
}

/* ─── Gates ─── */

function GatesSection({
  config,
  update,
  toggle,
}: {
  config: FullConfig;
  update: (path: string[], val: unknown) => void;
  toggle: (path: string[]) => void;
}) {
  return (
    <>
      <h2>验收门</h2>
      <p className="lead">
        “完成”不靠 agent 自己说，而靠这些检查全过。检查由系统在干净环境里跑，agent 改不了判定标准。
      </p>
      <div className="secbox">
        {config.gates.map((g, i) => (
          <div key={g.name} className="secrow">
            <div>
              <div className="ttl">{g.name}</div>
              <div className="ds">{g.desc}</div>
            </div>
            <div className="right">
              {g.thr !== undefined && (
                <>
                  覆盖率不低于{" "}
                  <input
                    className="num"
                    value={String(g.thr)}
                    onChange={(e) => update(["gates", String(i), "thr"], Number(e.target.value))}
                  />%
                </>
              )}
              <Toggle on={g.on} onClick={() => toggle(["gates", String(i), "on"])} />
            </div>
          </div>
        ))}
      </div>
      <div className="seghd">以下动作必须先经你审批</div>
      <div className="secbox">
        {config.approve.map((a, i) => (
          <div key={a.name} className="secrow">
            <div className="ttl">{a.name}</div>
            <div className="right">
              <Toggle on={a.on} onClick={() => toggle(["approve", String(i), "on"])} />
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

/* ─── Skills ─── */

function SkillsSection({
  config,
  toggle,
}: {
  config: FullConfig;
  toggle: (path: string[]) => void;
}) {
  return (
    <>
      <h2>规约 / 技能</h2>
      <p className="lead">
        团队的通用规约做成技能包，agent 干相关活时自动加载。改一次，所有相关 agent 下次就用新版。
      </p>
      <div className="secbox">
        {config.skills.map((s, i) => (
          <div key={s.name} className="secrow">
            <div>
              <div className="ttl">
                {s.name} <span className="pill" style={{ marginLeft: 6 }}>{s.ver}</span>
              </div>
              <div className="ds">
                {s.desc} · 用于 {s.roles.map((r) => ROLES[r].nm).join("、")}
              </div>
            </div>
            <div className="right">
              <Toggle on={s.on} onClick={() => toggle(["skills", String(i), "on"])} />
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

/* ─── Repos ─── */

function ReposSection({ config }: { config: FullConfig }) {
  return (
    <>
      <h2>仓库与凭证</h2>
      <p className="lead">
        agent 能动哪些仓库在这里管。<b>密码和密钥不在这里存</b>——它们托管在密钥库，运行时才注入，系统和界面都看不到明文。
      </p>
      <div className="secbox">
        {config.repos.map((r) => (
          <div key={r.name} className="secrow">
            <div>
              <div className="ttl">{r.name}</div>
              <div className="ds mono">{r.url} · {r.branch}</div>
            </div>
            <div className="right">
              <span className="lock">🔒 凭证已托管</span>
            </div>
          </div>
        ))}
        <div className="secrow">
          <div className="ds">+ 添加仓库</div>
        </div>
      </div>
    </>
  );
}

/* ─── Integrations ─── */

function IntegrationsSection({
  config,
  toggle,
}: {
  config: FullConfig;
  toggle: (path: string[]) => void;
}) {
  return (
    <>
      <h2>集成</h2>
      <p className="lead">连上这些，工单能从监控自动进来、状态能发到群聊、改动能进 CI。</p>
      <div className="secbox">
        {config.integrations.map((g, i) => (
          <div key={g.name} className="secrow">
            <div className="ttl">{g.name}</div>
            <div className="right">
              <span className="muted">{g.on ? "已连接" : "未连接"}</span>
              <Toggle on={g.on} onClick={() => toggle(["integrations", String(i), "on"])} />
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

/* ─── Budget ─── */

function BudgetSection({
  config,
  update,
  toggle,
}: {
  config: FullConfig;
  update: (path: string[], val: unknown) => void;
  toggle: (path: string[]) => void;
}) {
  const b = config.budget;
  return (
    <>
      <h2>审批与预算</h2>
      <p className="lead">谁来接升级和审批，以及每个工单的花费上限——超了就停下来，避免 agent 空转烧钱。</p>
      <div className="seghd">谁来审批</div>
      <div className="secbox">
        <div className="secrow">
          <div>
            <div className="ttl">你</div>
            <div className="ds">主审批人，接收所有升级</div>
          </div>
          <div className="right"><span className="pill">主</span></div>
        </div>
        <div className="secrow">
          <div>
            <div className="ttl">On-call 轮值</div>
            <div className="ds">你不在时接管，避免卡住</div>
          </div>
          <div className="right"><span className="pill">备用</span></div>
        </div>
        <div className="secrow">
          <div>
            <div className="ttl">低风险动作自动放行</div>
            <div className="ds">非敏感动作不必每次等你，限时自动通过</div>
          </div>
          <div className="right">
            <Toggle on={b.autoLow} onClick={() => toggle(["budget", "autoLow"])} />
          </div>
        </div>
      </div>
      <div className="seghd">每个工单的上限</div>
      <div className="secbox">
        <div className="secrow">
          <div className="ttl">Token 上限</div>
          <div className="right">
            <input className="num" style={{ width: 90 }} value={String(b.tokens)}
              onChange={(e) => update(["budget", "tokens"], Number(e.target.value))} />
          </div>
        </div>
        <div className="secrow">
          <div className="ttl">花费上限</div>
          <div className="right">
            $ <input className="num" value={String(b.cost)}
              onChange={(e) => update(["budget", "cost"], Number(e.target.value))} />
          </div>
        </div>
        <div className="secrow">
          <div className="ttl">最多步数</div>
          <div className="right">
            <input className="num" value={String(b.steps)}
              onChange={(e) => update(["budget", "steps"], Number(e.target.value))} />
          </div>
        </div>
      </div>
    </>
  );
}

/* ══════════════════════════════════════════════════════════════
   LLM 供应商配置 — 完整版
   支持：预设模板 / 模型建议 / 连接测试 / Key 管理
   ══════════════════════════════════════════════════════════════ */

/* ─── 预设模板 ─── */
const PROVIDER_PRESETS: Record<
  string,
  { name: string; base_url: string; models: string[] }
> = {
  openai: {
    name: "OpenAI",
    base_url: "https://api.openai.com/v1",
    models: ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
  },
  deepseek: {
    name: "DeepSeek",
    base_url: "https://api.deepseek.com",
    models: ["deepseek-chat", "deepseek-reasoner"],
  },
  ollama: {
    name: "Ollama（本地）",
    base_url: "http://localhost:11434/v1",
    models: ["llama3", "qwen2", "mistral", "deepseek-r1"],
  },
  together: {
    name: "Together AI",
    base_url: "https://api.together.xyz/v1",
    models: ["meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"],
  },
  siliconflow: {
    name: "SiliconFlow",
    base_url: "https://api.siliconflow.cn/v1",
    models: ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct"],
  },
};

function LlmSection({
  config,
  update,
  toast,
}: {
  config: FullConfig;
  update: (path: string[], val: unknown) => void;
  toast: (msg: string) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [showPresets, setShowPresets] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const presetsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!showPresets) return;
    const handler = (e: MouseEvent) => {
      if (!presetsRef.current?.contains(e.target as Node)) {
        setShowPresets(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showPresets]);

  const providers = config.llm?.providers || [];
  const activeId = config.llm?.active_provider || "";

  const setActive = (id: string) => update(["llm", "active_provider"], id);

  const toggleEnabled = (idx: number) => {
    const p = providers[idx];
    if (!p) return;
    const nextProviders = providers.map((x, i) =>
      i === idx ? { ...x, enabled: !x.enabled } : x
    );
    const nowEnabled = nextProviders[idx]!.enabled;
    let nextActive = activeId;
    if (nowEnabled && !activeId) {
      nextActive = p.id;
    } else if (!nowEnabled && p.id === activeId) {
      nextActive = nextProviders.find((x, i) => i !== idx && x.enabled)?.id || "";
    }
    update(["llm"], { active_provider: nextActive, providers: nextProviders });
  };

  const removeProvider = (idx: number) => {
    const p = providers[idx];
    const nextProviders = providers.filter((_, i) => i !== idx);
    let nextActive = activeId;
    if (p?.id === activeId) {
      nextActive = nextProviders.find((x) => x.enabled)?.id || "";
    }
    update(["llm"], { active_provider: nextActive, providers: nextProviders });
    setDeleteConfirm(null);
  };

  /* 从预设模板添加供应商 */
  const addFromPreset = (key: string) => {
    const preset = PROVIDER_PRESETS[key];
    if (!preset) return;
    update(["llm", "providers"], [
      ...providers,
      {
        id: key + "-" + Date.now(),
        name: preset.name,
        type: "openai" as const,
        api_key_env: (key.toUpperCase() + "_API_KEY").replace("-", "_"),
        api_key: "",
        base_url: preset.base_url,
        model: preset.models[0] || "gpt-4o",
        enabled: false,
        max_tokens: 4096,
        effort: "",
      },
    ]);
    setShowPresets(false);
    toast("已添加 " + preset.name);
  };

  /* 手动添加空供应商 */
  const addBlank = () => {
    update(["llm", "providers"], [
      ...providers,
      {
        id: "custom-" + Date.now(),
        name: "自定义",
        type: "openai" as const,
        api_key_env: "CUSTOM_API_KEY",
        api_key: "",
        base_url: "https://api.openai.com/v1",
        model: "gpt-4o",
        enabled: false,
        max_tokens: 4096,
        effort: "",
      },
    ]);
  };

  /* 连接测试 */
  const handleTest = async (idx: number) => {
    const p = providers[idx];
    if (!p) return;
    setTestingId(p.id);
    setTestResult((prev) => ({ ...prev, [p.id]: "testing…" }));
    try {
      const res = await api.testLLMProvider(p.id);
      setTestResult((prev) => ({
        ...prev,
        [p.id]: res.ok
          ? `✅ ${res.model || "ok"}`
          : `❌ ${res.error || "fail"}`,
      }));
    } catch (e: unknown) {
      setTestResult((prev) => ({
        ...prev,
        [p.id]: `❌ ${String(e)}`,
      }));
    }
    setTestingId(null);
  };

  /* 根据类型获取 icon */
  const providerIcon = (p: (typeof providers)[0]) => {
    if (p.type === "anthropic") return "An";
    const name = (p.name || "").toLowerCase();
    if (name.includes("deepseek")) return "DS";
    if (name.includes("ollama")) return "Ol";
    if (name.includes("together")) return "To";
    if (name.includes("silicon")) return "Si";
    return "AI";
  };

  return (
    <>
      <h2>LLM 供应商</h2>
      <p className="lead">
        配置 AI 供应商用于驱动 agent。API Key 通过 UI 设置后存入数据库，
        <b>不会出现在 .env 文件中</b>。可以同时配置多个供应商并自由切换。
      </p>

      {/* ─── 供应商列表 ─── */}
      {providers.length === 0 && (
        <div className="secbox">
          <div className="secrow">
            <div className="ds" style={{ textAlign: "center", width: "100%", padding: "20px 0" }}>
              还没有配置 LLM 供应商。点击下方按钮添加。
            </div>
          </div>
        </div>
      )}

      {providers.map((p, i) => {
        const isActive = p.id === activeId && p.enabled;
        const isEditing = editing === p.id;
        const isDeleting = deleteConfirm === p.id;
        const testRes = testResult[p.id];
        const isTesting = testingId === p.id;

        return (
          <div
            key={p.id}
            className="secbox"
            style={{
              borderColor: isDeleting ? "var(--bad)" : isActive ? "var(--agent)" : undefined,
              opacity: p.enabled ? 1 : 0.55,
              transition: ".2s",
            }}
          >
            {/* ── Header row ── */}
            <div className="secrow">
              <div className={`av-sm ${p.enabled ? "coordinator" : ""}`}
                style={{
                  background: p.enabled ? undefined : "var(--elev2)",
                  color: p.enabled ? undefined : "var(--tx3)",
                }}>
                {providerIcon(p)}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="ttl" style={{ minWidth: 0 }}>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "100%", display: "inline-block", verticalAlign: "bottom" }}>{p.name}</span>
                  {isActive && (
                    <span className="pill" style={{
                      marginLeft: 8, background: "var(--agent-bg)",
                      color: "var(--agent)", borderColor: "transparent",
                    }}>当前使用</span>
                  )}
                  {!p.enabled && (
                    <span className="pill" style={{ marginLeft: 6 }}>已停用</span>
                  )}
                </div>
                <div className="ds" style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", minWidth: 0 }}>
                  <span style={{ flexShrink: 0 }}>{p.type === "anthropic" ? "Anthropic" : "OpenAI 兼容"}</span>
                  <span className="mono" style={{ fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "100%" }}>{p.model}</span>
                  {p.base_url && (
                    <span className="muted" style={{ fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "100%" }}>{p.base_url}</span>
                  )}
                  {/* 连接状态指示 */}
                  {testRes && testRes !== "testing…" && (
                    <span style={{
                      fontSize: 11,
                      color: testRes.startsWith("✅") ? "var(--ok)" : "var(--bad)",
                      marginLeft: 4,
                    }}>
                      {testRes}
                    </span>
                  )}
                  {isTesting && (
                    <span style={{ fontSize: 11, color: "var(--agent)" }}>测试中…</span>
                  )}
                </div>
              </div>
              <div className="right" style={{ gap: 6 }}>
                <button
                  className="btn"
                  style={{ padding: "4px 8px", fontSize: 11 }}
                  onClick={() => handleTest(i)}
                  disabled={isTesting}
                >
                  {isTesting ? "…" : "测试"}
                </button>
                <Toggle on={p.enabled} onClick={() => toggleEnabled(i)} />
              </div>
            </div>

            {/* ── Actions / Edit ── */}
            <div style={{ padding: "0 18px 14px" }}>
              {isEditing ? (
                <ProviderForm
                  provider={p}
                  toast={toast}
                  onSave={(updated) => {
                    update(["llm", "providers", String(i)], updated);
                    setEditing(null);
                    toast("供应商配置已保存");
                  }}
                  onSaveKey={async (pid, key) => {
                    try {
                      await api.setLLMProviderKey(pid, key);
                      toast("API Key 已保存");
                    } catch {
                      toast("保存失败");
                    }
                  }}
                  onCancel={() => setEditing(null)}
                />
              ) : isDeleting ? (
                <div className="actions" style={{ marginTop: 4 }}>
                  <span style={{ fontSize: 13, color: "var(--bad)" }}>
                    确认删除供应商「{p.name}」？
                  </span>
                  <button className="btn danger" style={{ padding: "4px 10px", fontSize: 12 }}
                    onClick={() => removeProvider(i)}>确认删除</button>
                  <button className="btn" style={{ padding: "4px 10px", fontSize: 12 }}
                    onClick={() => setDeleteConfirm(null)}>取消</button>
                </div>
              ) : (
                <div className="actions" style={{ marginTop: 4 }}>
                  {!isActive && p.enabled && (
                    <button className="btn primary" style={{ padding: "4px 10px", fontSize: 12 }}
                      onClick={() => { setActive(p.id); toast("已切换到 " + p.name); }}>
                      设为当前使用
                    </button>
                  )}
                  <button className="btn" style={{ padding: "4px 10px", fontSize: 12 }}
                    onClick={() => {
                      setEditing(p.id);
                      setTestResult((prev) => {
                        const next = { ...prev };
                        delete next[p.id];
                        return next;
                      });
                    }}>编辑</button>
                  <button className="btn danger" style={{ padding: "4px 10px", fontSize: 12 }}
                    onClick={() => setDeleteConfirm(p.id)}>删除</button>
                </div>
              )}
            </div>
          </div>
        );
      })}

      {/* ─── 添加供应商 ─── */}
      <div className="actions" style={{ gap: 8, padding: "0 0 16px" }}>
        <button className="btn" onClick={addBlank}>＋ 自定义</button>
        <div style={{ position: "relative" }} ref={presetsRef}>
          <button className="btn primary" onClick={() => setShowPresets(!showPresets)}>
            ＋ 从预设添加 ▾
          </button>
          {showPresets && (
            <div
              style={{
                position: "absolute",
                top: "100%",
                left: 0,
                marginTop: 4,
                background: "var(--elev)",
                border: "1px solid var(--line2)",
                borderRadius: 10,
                padding: 6,
                zIndex: 50,
                minWidth: 220,
                boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
              }}
            >
              {Object.entries(PROVIDER_PRESETS).map(([key, preset]) => (
                <div
                  key={key}
                  onClick={() => addFromPreset(key)}
                  style={{
                    padding: "8px 12px",
                    borderRadius: 6,
                    cursor: "pointer",
                    fontSize: 13,
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "var(--elev2)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
                >
                  <span style={{ fontWeight: 600 }}>{preset.name}</span>
                  <span className="muted" style={{ fontSize: 11 }}>
                    {preset.models[0]}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ─── 当前激活供应商摘要 ─── */}
      {providers.length > 0 && (
        <>
          <div className="seghd">供应商状态</div>
          <div className="secbox">
            <div className="secrow">
              <div className="ds" style={{ lineHeight: 1.8 }}>
                已配置 {providers.length} 个供应商，
                {providers.filter((p) => p.enabled).length} 个已启用，
                当前使用：<b>{providers.find((p) => p.id === activeId)?.name || "无"}</b>
                {providers.find((p) => p.id === activeId)?.type === "anthropic"
                  ? "（Anthropic SDK）"
                  : "（OpenAI 兼容）"}
              </div>
            </div>
          </div>
        </>
      )}
    </>
  );
}

/* ══════════════════════════════════════════════════════════════
   Provider 编辑表单
   ══════════════════════════════════════════════════════════════ */

/* 常用模型建议 */
const MODEL_SUGGESTIONS: Record<string, string[]> = {
  anthropic: [
    "claude-sonnet-4-6",
    "claude-opus-4-8",
    "claude-haiku-4-5",
  ],
  openai: [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "gpt-3.5-turbo",
  ],
  deepseek: ["deepseek-chat", "deepseek-reasoner"],
  ollama: ["llama3", "qwen2", "mistral", "deepseek-r1"],
};

function ProviderForm({
  provider,
  onSave,
  onSaveKey,
  onCancel,
  toast,
}: {
  provider: LLMProvider;
  onSave: (p: LLMProvider) => void;
  onSaveKey?: (id: string, key: string) => Promise<void>;
  onCancel: () => void;
  toast: (msg: string) => void;
}) {
  const [draft, setDraft] = useState<LLMProvider>({ ...provider });
  const [keyValue, setKeyValue] = useState("");
  const [saving, setSaving] = useState(false);

  const set = (key: keyof LLMProvider, val: unknown) => {
    setDraft((prev) => ({ ...prev, [key]: val }));
  };

  const datalistId = `model-suggestions-${provider.id}`;

  const setDefaultsForType = (type: string) => {
    if (type === "anthropic") {
      set("model", (MODEL_SUGGESTIONS["anthropic"] ?? ["claude-sonnet-4-6"])[0]!);
      set("base_url", "");
    } else {
      set("model", (MODEL_SUGGESTIONS["openai"] ?? ["gpt-4o"])[0]!);
      set("base_url", "https://api.openai.com/v1");
    }
  };

  /* 模型建议 - 根据名称自动匹配 */
  const suggestions: string[] = (() => {
    const name = (draft.name || "").toLowerCase();
    if (draft.type === "anthropic") return MODEL_SUGGESTIONS["anthropic"] ?? [];
    if (name.includes("deepseek")) return MODEL_SUGGESTIONS["deepseek"] ?? [];
    if (name.includes("ollama")) return MODEL_SUGGESTIONS["ollama"] ?? [];
    return MODEL_SUGGESTIONS["openai"] ?? [];
  })();

  const handleSave = async () => {
    if (!draft.name.trim()) {
      toast("请填写供应商名称");
      return;
    }
    if (!draft.model.trim()) {
      toast("请填写模型名称");
      return;
    }
    if (draft.type === "openai" && !draft.base_url.trim()) {
      toast("OpenAI 兼容供应商需要填写 Base URL");
      return;
    }
    if (!draft.max_tokens || draft.max_tokens < 256) {
      toast("输出上限至少为 256");
      return;
    }
    setSaving(true);
    try {
      onSave(draft);
      if (keyValue.trim() && onSaveKey) {
        await onSaveKey(draft.id, keyValue.trim());
        setKeyValue("");
      }
    } finally {
      setSaving(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") onCancel();
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) handleSave();
  };

  return (
    <div
      style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}
      onKeyDown={handleKeyDown}
    >
      {/* 名称 + 类型 */}
      <div style={{ display: "flex", gap: 10 }}>
        <div className="field" style={{ flex: 1, marginBottom: 0 }}>
          <label>名称</label>
          <input value={draft.name} onChange={(e) => set("name", e.target.value)}
            autoFocus style={{ width: "100%" }} />
        </div>
        <div className="field" style={{ flex: 1, marginBottom: 0 }}>
          <label>类型</label>
          <Select
            value={draft.type}
            onChange={(v) => { set("type", v); setDefaultsForType(v); }}
            options={[
              { value: "anthropic", label: "Anthropic" },
              { value: "openai", label: "OpenAI 兼容" },
            ]}
          />
        </div>
      </div>

      {/* 模型 + Max Tokens */}
      <div style={{ display: "flex", gap: 10 }}>
        <div className="field" style={{ flex: 2, marginBottom: 0 }}>
          <label>模型</label>
          <div style={{ position: "relative" }}>
            <input
              value={draft.model}
              onChange={(e) => set("model", e.target.value)}
              list={datalistId}
              placeholder={draft.type === "anthropic" ? "claude-sonnet-4-6" : "gpt-4o"}
              style={{ width: "100%" }}
            />
            <datalist id={datalistId}>
              {suggestions.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </div>
        </div>
        <div className="field" style={{ flex: 1, marginBottom: 0 }}>
          <label title="单次回复最多生成多少 token，不是上下文长度">输出上限</label>
          <input type="number" value={draft.max_tokens}
            onChange={(e) => set("max_tokens", Number(e.target.value))}
            min={256} max={128000} step={1024}
            style={{ width: "100%" }} />
        </div>
      </div>

      {/* Base URL（仅 OpenAI 兼容） */}
      {draft.type === "openai" && (
        <div className="field" style={{ marginBottom: 0 }}>
          <label>Base URL</label>
          <input value={draft.base_url}
            onChange={(e) => set("base_url", e.target.value)}
            placeholder="https://api.openai.com/v1"
            style={{ width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
        </div>
      )}

      {/* API Key */}
      <div className="field" style={{ marginBottom: 0 }}>
        <label>
          API Key
          {provider.api_key && (
            <span className="muted" style={{ marginLeft: 8, color: "var(--ok)" }}>
              ✓ 已配置
            </span>
          )}
        </label>
        <input type="password" value={keyValue}
          onChange={(e) => setKeyValue(e.target.value)}
          placeholder={provider.api_key ? "（留空则不修改）" : "输入 API Key"}
          style={{ width: "100%", fontFamily: "var(--mono)" }} />
      </div>

      {/* Actions */}
      <div className="actions" style={{ marginTop: 4 }}>
        <button className="btn" onClick={onCancel} disabled={saving}>
          取消 <span className="muted">(Esc)</span>
        </button>
        <button className="btn primary" onClick={handleSave} disabled={saving}>
          {saving ? "保存中…" : "保存"} <span className="muted">(⌘+↵)</span>
        </button>
      </div>
    </div>
  );
}
