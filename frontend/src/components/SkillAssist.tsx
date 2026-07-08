import { useEffect, useState } from "react";
import * as api from "../api";
import { mdLite } from "../utils";
import type { Skill } from "../types";

/**
 * Reusable "generate this field with a built-in expert skill" affordance.
 * Renders a small trigger button; clicking opens a modal that lists the skills
 * relevant to `target` (and `role`), lets the operator add a short brief, runs
 * generation grounded in the project, previews the Markdown result, and applies
 * it back to the field via `onApply`.
 */
export default function SkillAssist({
  target,
  role,
  projectId,
  onApply,
  label = "Skill 生成",
}: {
  target: "docs" | "memory";
  role?: string;
  projectId?: string;
  onApply: (text: string) => void;
  label?: string;
}) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        className="skill-btn"
        title="用内置的最强专家 Skill 辅助生成"
        onClick={() => setOpen(true)}
      >
        ✨ {label}
      </button>
      {open && (
        <SkillModal
          target={target}
          role={role}
          projectId={projectId}
          onClose={() => setOpen(false)}
          onApply={(t) => {
            onApply(t);
            setOpen(false);
          }}
        />
      )}
    </>
  );
}

function SkillModal({
  target,
  role,
  projectId,
  onClose,
  onApply,
}: {
  target: "docs" | "memory";
  role?: string;
  projectId?: string;
  onClose: () => void;
  onApply: (text: string) => void;
}) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [picked, setPicked] = useState<string>("");
  const [brief, setBrief] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    let alive = true;
    api
      .listSkills()
      .then((all) => {
        if (!alive) return;
        const rel = all.filter(
          (s) => s.target === target && (target !== "memory" || s.role === role)
        );
        setSkills(rel);
        setPicked(rel[0]?.id ?? "");
      })
      .catch(() => setErr("无法加载 Skill 列表"));
    return () => {
      alive = false;
    };
  }, [target, role]);

  const active = skills.find((s) => s.id === picked);

  const run = async () => {
    if (!picked) return;
    setBusy(true);
    setErr("");
    setResult("");
    try {
      const r = await api.generateWithSkill({
        skill_id: picked,
        project_id: projectId,
        role,
        brief,
      });
      if (r.error) setErr(r.error);
      else setResult(r.text || "");
    } catch {
      setErr("生成失败：请确认已在「配置 → LLM 供应商」启用并配置一个可用的供应商。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="modal"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="box skill-box">
        <h3>✨ 用最强 Skill 辅助生成</h3>
        <p className="lead">
          选择一个内置专家 Skill，结合本项目的代码与文档上下文，一键起草。
        </p>

        {err && <div className="skill-err">{err}</div>}

        <div className="field">
          <label>选择 Skill</label>
          <div className="skill-picker">
            {skills.length === 0 && !err && (
              <div className="muted">加载中…</div>
            )}
            {skills.map((s) => (
              <button
                key={s.id}
                type="button"
                className={`skill-card${picked === s.id ? " on" : ""}`}
                onClick={() => setPicked(s.id)}
              >
                <span className="sk-ic">{s.icon}</span>
                <span className="sk-txt">
                  <b>{s.name}</b>
                  <span className="sk-tag">{s.tagline}</span>
                </span>
              </button>
            ))}
          </div>
        </div>

        <div className="field">
          <label>补充要求（可选）</label>
          <textarea
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
            placeholder={active?.hint || "补充你的具体要求…"}
            rows={3}
          />
        </div>

        {(busy || result) && (
          <div className="field">
            <label>生成预览</label>
            <div className="skill-result">
              {busy && !result ? (
                <div className="skill-loading">
                  <span className="btn-spin" /> 正在结合项目上下文生成…
                </div>
              ) : (
                <div
                  className="bubble"
                  dangerouslySetInnerHTML={{ __html: mdLite(result) }}
                />
              )}
            </div>
          </div>
        )}

        <div className="actions">
          <button type="button" className="btn" onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className="btn"
            onClick={run}
            disabled={busy || !picked}
          >
            {busy ? "生成中…" : result ? "重新生成" : "生成"}
          </button>
          <button
            type="button"
            className="btn primary"
            onClick={() => onApply(result)}
            disabled={!result || busy}
          >
            应用到该字段
          </button>
        </div>
      </div>
    </div>
  );
}
