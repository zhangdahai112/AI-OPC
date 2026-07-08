import { useAppState } from "../context";
import { useEffect } from "react";

export default function MetricsView() {
  const { metrics, refreshMetrics } = useAppState();

  useEffect(() => {
    refreshMetrics();
  }, [refreshMetrics]);

  const m = metrics || {
    tickets_total: 0,
    by_status: {},
    gates: {},
    escalations: 0,
    approvals: 0,
  };

  const cards: [string, number][] = [
    ["工单总数", m.tickets_total],
    ["进行中", m.by_status?.working || 0],
    [
      "等你处理",
      (m.by_status?.awaiting_approval || 0) +
        (m.by_status?.blocked || 0) +
        (m.by_status?.planning || 0),
    ],
    ["已完成", m.by_status?.done || 0],
    ["门通过次数", m.gates?.pass || 0],
    ["门失败次数", m.gates?.fail || 0],
    ["升级次数", m.escalations || 0],
    ["人审次数", m.approvals || 0],
  ];

  return (
    <div className="cfgwrap">
      <h2>平台自指标</h2>
      <p className="lead">
        平台对自身出指标：成功率、升级率、门通过率、人审负载——用于调路由 /
        模板 / skill，以及判断何时可多放权。
      </p>
      <div className="metrics">
        {cards.map(([label, value]) => (
          <div key={label} className="metric">
            <div className="v">{value}</div>
            <div className="l">{label}</div>
          </div>
        ))}
      </div>
      <div className="seghd">说明</div>
      <div className="secbox">
        <div className="secrow">
          <div className="ds">
            指标实时来自审计日志与门结果，可在{" "}
            <span className="mono">/api/audit</span> 回放全链路决策。
          </div>
        </div>
      </div>
    </div>
  );
}
