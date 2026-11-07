import React, { useCallback, useEffect, useState } from "react";

type TreeNode = {
  type: string;
  name?: string;
  path: string;
  robot_id?: string;
  skill?: string;
  params?: Record<string, unknown>;
  condition?: string;
  success_count?: number;
  interval_sec?: number;
  children?: TreeNode[];
};

type NodeState = { status: string; message?: string; robot_id?: string; cycle?: number; task_id?: string };

type TreeRun = {
  status: string;
  active: boolean;
  paused: boolean;
  cycle: number;
  message: string;
  nodes: Record<string, NodeState>;
};

type MissionTreeView = {
  stored: boolean;
  tree: { loop?: { enabled?: boolean; interval_sec?: number; max_cycles?: number }; root: TreeNode };
  robots: Record<string, TreeNode>;
  run: TreeRun | null;
};

type Request = (path: string, init?: RequestInit) => Promise<any>;

const ACTIVE_POLL_MS = 2_000;

function statusClass(status?: string) {
  if (status === "SUCCESS") return "ok";
  if (status === "FAILURE") return "fail";
  if (status === "RUNNING") return "run";
  if (status === "WAITING_APPROVAL") return "wait";
  return "idle";
}

/** Peer work a robot reported to Maestro instead of calling the peer itself. */
function PeerFollowups({ path, states }: { path: string; states: Record<string, NodeState> }) {
  const entries = Object.entries(states)
    .filter(([key]) => key.startsWith(`${path}.peer`))
    .sort(([a], [b]) => a.localeCompare(b));
  if (!entries.length) return null;
  return <ul>{entries.map(([key, state]) => <li key={key}>
    <div className={`mt-node ${statusClass(state.status)}`}>
      <span className="bt-type">동료 작업</span>
      <strong>{state.robot_id || "?"}</strong>
      {state.task_id && <small>{state.task_id}</small>}
      <span className="mt-status">{state.status}</span>
      {state.message && <small className="mt-message">{state.message}</small>}
    </div>
  </li>)}</ul>;
}

function NodeRow({ node, states }: { node: TreeNode; states: Record<string, NodeState> }) {
  const state = states[node.path];
  const params = node.params && Object.keys(node.params).length ? JSON.stringify(node.params) : "";
  return <li>
    <div className={`mt-node ${statusClass(state?.status)}`}>
      <span className="bt-type">{node.type}</span>
      <strong>{node.skill || node.condition || node.name}</strong>
      {node.type === "Action" && node.name && <small>{node.name}</small>}
      {params && <small className="mt-params">{params}</small>}
      {node.type === "Parallel" && <small>전체 {node.success_count}개 성공 필요</small>}
      {node.type === "SleepBetweenCycles" && <small>{node.interval_sec}s</small>}
      {state && <span className="mt-status">{state.status}</span>}
      {state?.robot_id && node.robot_id !== state.robot_id && <small>실행: {state.robot_id}</small>}
      {state?.message && <small className="mt-message">{state.message}</small>}
    </div>
    {node.type === "Action" && <PeerFollowups path={node.path} states={states} />}
    {node.children && node.children.length > 0 && <ul>{node.children.map((child) => <NodeRow key={child.path} node={child} states={states} />)}</ul>}
  </li>;
}

export function MissionTree({ missionId, request, active, refreshKey }: {
  missionId: string;
  request: Request;
  active: boolean;
  refreshKey: number;
}) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<MissionTreeView | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setView(await request(`/api/v1/missions/${missionId}/behavior-tree`));
      setError("");
    } catch (cause) { setError(String(cause)); }
  }, [missionId, request]);

  useEffect(() => { if (open) void load(); }, [open, load, refreshKey]);
  useEffect(() => {
    if (!open || !active) return;
    const id = setInterval(() => void load(), ACTIVE_POLL_MS);
    return () => clearInterval(id);
  }, [open, active, load]);

  const states = view?.run?.nodes ?? {};
  const loop = view?.tree.loop;
  return <details className="mission-tree" onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}>
    <summary>Behavior Tree (로봇별){view?.run ? ` · ${view.run.status}${view.run.paused ? " (일시정지)" : ""} · cycle ${view.run.cycle}` : ""}</summary>
    {error && <p className="error">{error}</p>}
    {view && <>
      <small className="mt-note">
        Maestro가 전체 트리를 실행하고, 각 Action은 지정 로봇에 ExecuteSkill로 전달됩니다. 로봇별 보기는 전체 트리에서 그 로봇의 노드만 남긴 것이며, 단계 순서는 로봇 사이에도 지켜집니다.
        {!view.stored && " 이 Mission에는 계획 시 저장된 트리가 없어 Task 의존 관계로 만든 트리를 표시합니다."}
        {loop?.enabled && ` 반복: ${loop.interval_sec}초 간격, ${loop.max_cycles ? `${loop.max_cycles}회` : "취소할 때까지"}.`}
      </small>
      {view.run?.message && <p className="mt-run-message">{view.run.message}</p>}
      <div className="mt-lanes">{Object.entries(view.robots).map(([robotId, root]) => <section className="mt-lane" key={robotId}>
        <h4>{robotId}</h4>
        <ul className="mt-tree"><NodeRow node={root} states={states} /></ul>
      </section>)}</div>
    </>}
  </details>;
}
