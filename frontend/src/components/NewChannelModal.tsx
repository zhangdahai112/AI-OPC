import { useState } from "react";
import { useAppState } from "../context";
import * as api from "../api";

export default function NewChannelModal({ onClose }: { onClose: () => void }) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const { projects, toast, setActiveChannelId, refreshChannels } = useAppState() as any;
  const [name, setName] = useState("");
  const [selectedProjects, setSelectedProjects] = useState<Set<string>>(new Set());
  const [selectedMembers, setSelectedMembers] = useState<Set<string>>(
    new Set(["coordinator", "developer"])
  );

  const toggleProject = (pid: string) => {
    const next = new Set(selectedProjects);
    next.has(pid) ? next.delete(pid) : next.add(pid);
    setSelectedProjects(next);
  };

  const toggleMember = (role: string) => {
    const next = new Set(selectedMembers);
    next.has(role) ? next.delete(role) : next.add(role);
    setSelectedMembers(next);
  };

  const handleCreate = async () => {
    if (!name.trim()) { toast("请填写群名"); return; }
    try {
      const ch = await api.createChannel({
        name: name.trim(),
        project_ids: [...selectedProjects],
        roster: [...selectedMembers],
      });
      onClose();
      setActiveChannelId(ch.id);
      refreshChannels();
      toast("群聊已创建");
    } catch {
      toast("创建失败");
    }
  };

  const memberOptions: [string, string][] = [
    ["coordinator", "项目经理"],
    ["analyst", "需求分析"],
    ["developer", "开发"],
    ["tester", "测试"],
    ["devops", "运维"],
    ["reporter", "上报"],
  ];

  return (
    <div className="modal" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="box">
        <h3>新建群聊</h3>
        <p className="lead">创建协作群聊，可关联多个项目，并选择参与成员。</p>

        <div className="field">
          <label>群聊名称</label>
          <input
            autoFocus
            placeholder="如：导出服务优化作战群"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); }}
          />
        </div>

        <div className="field">
          <label>关联项目（可多选）</label>
          <div className="seg" style={{ flexWrap: "wrap", gap: 6 }}>
            {projects.length === 0 && (
              <div className="ds">还没有项目，先去项目页创建一个</div>
            )}
            {projects.map((p: any) => (
              <div
                key={p.id}
                className={`opt${selectedProjects.has(p.id) ? " on" : ""}`}
                onClick={() => toggleProject(p.id)}
              >
                {p.name}
              </div>
            ))}
          </div>
        </div>

        <div className="field">
          <label>参与成员</label>
          <div className="seg" style={{ flexWrap: "wrap", gap: 6 }}>
            {memberOptions.map(([key, label]) => (
              <div
                key={key}
                className={`opt${selectedMembers.has(key) ? " on" : ""}`}
                onClick={() => toggleMember(key)}
              >
                {label}
              </div>
            ))}
          </div>
        </div>

        <div className="actions">
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn primary" onClick={handleCreate}>创建群聊</button>
        </div>
      </div>
    </div>
  );
}
