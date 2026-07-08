import { useAppState } from "../context";
import { esc } from "../utils";
import * as api from "../api";
import { useState, useEffect } from "react";
import SkillAssist from "./SkillAssist";
import AgentStudio from "./AgentStudio";

/** Set an uncontrolled textarea's value by id and fire a native input event so
 *  React-adjacent listeners (and the eventual save) see the change. */
function fillTextarea(id: string, text: string) {
  const ta = document.getElementById(id) as HTMLTextAreaElement | null;
  if (!ta) return;
  ta.value = text;
  ta.dispatchEvent(new Event("input", { bubbles: true }));
  ta.focus();
}

export default function ProjectsView() {
  const {
    activeProject,
    toast,
    refreshActiveProject,
    refreshProjects,
  } = useAppState();

  // Listen for "open-new-project" custom event
  const [showNew, setShowNew] = useState(false);
  useEffect(() => {
    const handler = () => setShowNew(true);
    document.addEventListener("open-new-project", handler);
    return () => document.removeEventListener("open-new-project", handler);
  }, []);

  if (!activeProject) {
    return (
      <div className="cfgwrap">
        <h2>项目</h2>
        <p className="lead">
          项目 = 代码仓库（git 克隆）+ 需求文档。每个 agent
          在项目里有<b>永久不变的记忆</b>，会注入它的系统提示词，让回答稳定且贴合本项目。
        </p>
        <div className="secbox">
          <div className="secrow">
            <div className="ds">
              从左侧选择或新建一个项目。
            </div>
          </div>
        </div>
        {showNew && <NewProjectModal onClose={() => setShowNew(false)} />}
      </div>
    );
  }

  return (
    <div className="cfgwrap">
      <ProjectDetail
        project={activeProject}
        onRefresh={() => {
          if (activeProject) refreshActiveProject(activeProject.id);
        }}
      />
      {showNew && <NewProjectModal onClose={() => setShowNew(false)} />}
    </div>
  );
}

/* ─── Project Detail ─── */

function ProjectDetail({
  project,
  onRefresh,
}: {
  project: NonNullable<
    ReturnType<typeof useAppState>["activeProject"]
  >;
  onRefresh: () => void;
}) {
  const { toast } = useAppState();
  const p = project;

  const saveDoc = async () => {
    const textarea = document.getElementById(
      "proj-docs"
    ) as HTMLTextAreaElement;
    if (!textarea) return;
    try {
      await api.updateProjectDocs(p.id, textarea.value);
      toast("需求文档已保存");
      onRefresh();
    } catch {
      toast("保存失败");
    }
  };

  const handleClone = async () => {
    try {
      await api.cloneProjectRepo(p.id);
      toast("开始克隆…");
    } catch {
      toast("克隆失败");
    }
  };

  return (
    <>
      <h2>
        {esc(p.name)}{" "}
        <span className="pill" style={{ marginLeft: 6 }}>
          {p.status}
        </span>
      </h2>
      <p className="lead">
        仓库{" "}
        <span className="mono">{esc(p.repo_url || "（未配置）")}</span> @{" "}
        {esc(p.branch)} · 本地{" "}
        <span className="mono">{esc(p.local_path || "")}</span>
        {p.repo_url && (
          <button
            className="btn"
            style={{ marginLeft: 10, padding: "4px 10px" }}
            onClick={handleClone}
          >
            重新克隆
          </button>
        )}
      </p>

      <div className="seghd">需求文档（注入所有 agent）</div>
      <div className="secbox">
        <div style={{ padding: "14px 18px" }}>
          <textarea
            className="memta"
            id="proj-docs"
            rows={6}
            defaultValue={p.docs}
          />
          <div className="actions">
            <button className="btn primary" onClick={saveDoc}>
              保存需求文档
            </button>
            <SkillAssist
              target="docs"
              projectId={p.id}
              onApply={(t) => {
                fillTextarea("proj-docs", t);
                saveDoc();
              }}
            />
          </div>
        </div>
      </div>

      <AgentStudio key={p.id} projectId={p.id} />
    </>
  );
}

/* ─── New Project Modal ─── */

function NewProjectModal({ onClose }: { onClose: () => void }) {
  const { toast, refreshProjects } = useAppState();

  const handleCreate = async () => {
    const name = (
      document.getElementById("np-name") as HTMLInputElement
    )?.value.trim();
    if (!name) {
      toast("请填写项目名");
      return;
    }
    try {
      const p = await api.createProject({
        name,
        repo_url: (
          document.getElementById("np-url") as HTMLInputElement
        )?.value.trim(),
        branch: (
          document.getElementById("np-branch") as HTMLInputElement
        )?.value.trim() || "main",
        docs: (
          document.getElementById("np-docs") as HTMLTextAreaElement
        )?.value.trim(),
      });
      onClose();
      await refreshProjects();
      toast(p.repo_url ? "项目已创建 · 后台克隆中" : "项目已创建");
    } catch {
      toast("创建失败");
    }
  };

  return (
    <div className="modal" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="box">
        <h3>新建项目</h3>
        <p className="lead">
          项目定义了 agent 工作的世界：代码仓库 + 需求文档。
        </p>
        <div className="field">
          <label>项目名称</label>
          <input id="np-name" placeholder="如：导出服务" autoFocus />
        </div>
        <div className="field">
          <label>Git 仓库 URL（可选，留空则先用需求文档）</label>
          <input
            id="np-url"
            placeholder="https://github.com/owner/repo.git"
          />
        </div>
        <div className="field">
          <label>分支</label>
          <input id="np-branch" defaultValue="main" />
        </div>
        <div className="field">
          <label>
            需求文档（可选）
            <SkillAssist
              target="docs"
              onApply={(t) => fillTextarea("np-docs", t)}
            />
          </label>
          <textarea
            id="np-docs"
            placeholder="粘贴 PRD / 需求说明…，或用右上角 Skill 一键起草"
          />
        </div>
        <div className="actions">
          <button className="btn" onClick={onClose}>
            取消
          </button>
          <button className="btn primary" onClick={handleCreate}>
            创建并克隆
          </button>
        </div>
      </div>
    </div>
  );
}
