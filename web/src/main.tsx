import React, { FormEvent, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BehaviorTree, type TreeSeed } from "./BehaviorTree";
import { type DryRunResult, Settings } from "./Settings";
import "./styles.css";

type Robot = {
  robot_id: string;
  display_name: string;
  connection_state: string;
  operational_state: string;
  battery_percentage: number | null;
  current_task_id: string | null;
  capabilities: string[];
  pose: { x?: number; y?: number; frame_id?: string } | null;
  health_issues: string[];
};

type Task = {
  task_id: string;
  instruction: string;
  status: string;
  assigned_robot_id: string | null;
  depends_on: string[];
  progress: number;
};

type Mission = {
  mission_id: string;
  goal: string;
  status: string;
  plan_version: number;
  tasks: Task[];
};

type Approval = {
  approval_id: string;
  robot_id: string;
  action: string;
  reason: string;
  requested_by: string;
  status: string;
  expires_at: string | null;
};

type FleetEvent = {
  event_id: string;
  event_type: string;
  aggregate_id: string;
  occurred_at: string;
  payload: Record<string, unknown>;
};

function App() {
  const [robots, setRobots] = useState<Robot[]>([]);
  const [missions, setMissions] = useState<Mission[]>([]);
  const [events, setEvents] = useState<FleetEvent[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [goal, setGoal] = useState("");
  const [token, setToken] = useState(() => localStorage.getItem("maestro-token") ?? "");
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState<{ robotId: string; src: string } | null>(null);
  const [view, setView] = useState<"fleet" | "settings" | "bt">("fleet");
  const [treeSeed, setTreeSeed] = useState<TreeSeed | null>(null);
  const clearTreeSeed = useCallback(() => setTreeSeed(null), []);

  function openBehaviorTree(goal: string, result: DryRunResult) {
    setTreeSeed({ goal, result });
    setView("bt");
  }

  const request = useCallback(async (path: string, init: RequestInit = {}) => {
    const headers = new Headers(init.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (init.body) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...init, headers });
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    return response.json();
  }, [token]);

  const refresh = useCallback(async () => {
    try {
      const [nextRobots, nextMissions, nextApprovals] = await Promise.all([
        request("/api/v1/robots"),
        request("/api/v1/missions"),
        request("/api/v1/approvals?pending_only=true")
      ]);
      setRobots(nextRobots);
      setMissions(nextMissions);
      setApprovals(nextApprovals);
      setError("");
    } catch (cause) {
      setError(String(cause));
    }
  }, [request]);

  useEffect(() => { void refresh(); const id = setInterval(refresh, 5000); return () => clearInterval(id); }, [refresh]);
  useEffect(() => {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const query = token ? `?token=${encodeURIComponent(token)}` : "";
    const socket = new WebSocket(`${scheme}://${location.host}/api/v1/events/stream${query}`);
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data) as FleetEvent;
      setEvents((current) => [event, ...current.filter((item) => item.event_id !== event.event_id)].slice(0, 100));
      void refresh();
    };
    return () => socket.close();
  }, [token, refresh]);

  async function submitGoal(event: FormEvent) {
    event.preventDefault();
    if (!goal.trim()) return;
    try {
      await request("/api/v1/goals", { method: "POST", body: JSON.stringify({ goal, auto_start: false }) });
      setGoal("");
      await refresh();
    } catch (cause) { setError(String(cause)); }
  }

  async function runRobotTask(robotId: string) {
    const instruction = window.prompt(`${robotId}에 전달할 작업을 입력하세요.`)?.trim();
    if (!instruction) return;
    try {
      await request(`/api/v1/robots/${robotId}/tasks`, {
        method: "POST", body: JSON.stringify({ instruction })
      });
      await refresh();
    } catch (cause) { setError(String(cause)); }
  }

  async function requestSnapshot(robotId: string) {
    try {
      const image = await request(`/api/v1/robots/${robotId}/snapshot`);
      const format = String(image.image_format || "image/jpeg");
      const mime = format.startsWith("image/") ? format : `image/${format}`;
      if (!image.image_base64) throw new Error("robot returned an empty snapshot");
      setSnapshot({ robotId, src: `data:${mime};base64,${image.image_base64}` });
    } catch (cause) { setError(String(cause)); }
  }

  async function stopRobot(robotId: string) {
    if (!window.confirm(`${robotId}를 비상 정지하시겠습니까?`)) return;
    try { await request(`/api/v1/robots/${robotId}/emergency-stop`, { method: "POST" }); await refresh(); }
    catch (cause) { setError(String(cause)); }
  }

  async function missionAction(missionId: string, action: "start" | "pause" | "resume" | "cancel" | "run") {
    try { await request(`/api/v1/missions/${missionId}/${action}`, { method: "POST" }); await refresh(); }
    catch (cause) { setError(String(cause)); }
  }

  async function decideApproval(approvalId: string, decision: "approve" | "reject") {
    const actor = window.prompt("승인/거부 담당자 이름을 입력하세요.")?.trim();
    if (!actor) return;
    try {
      await request(`/api/v1/approvals/${approvalId}/${decision}`, {
        method: "POST", body: JSON.stringify({ actor })
      });
      await refresh();
    } catch (cause) { setError(String(cause)); }
  }

  async function emergencyStop() {
    if (!window.confirm("전체 로봇을 비상 정지하시겠습니까?")) return;
    try { await request("/api/v1/fleet/emergency-stop", { method: "POST" }); await refresh(); }
    catch (cause) { setError(String(cause)); }
  }

  function saveToken(value: string) {
    setToken(value);
    localStorage.setItem("maestro-token", value);
  }

  return <main>
    <header>
      <div><p className="eyebrow">FLEET ORCHESTRATION</p><h1>Robo-Claw Maestro</h1></div>
      <div className="controls">
        <input aria-label="API token" type="password" placeholder="read/control token" value={token} onChange={(e) => saveToken(e.target.value)} />
        <button onClick={() => void refresh()}>새로고침</button>
        <button className="danger" onClick={() => void emergencyStop()}>전체 비상 정지</button>
      </div>
    </header>
    <nav className="tabs">
      <button className={view === "fleet" ? "active" : ""} onClick={() => setView("fleet")}>관제</button>
      <button className={view === "settings" ? "active" : ""} onClick={() => setView("settings")}>설정</button>
      <button className={view === "bt" ? "active" : ""} onClick={() => setView("bt")}>Behavior Tree</button>
    </nav>
    {error && <div className="error">{error}</div>}
    {/* Views stay mounted and are only hidden, so dry-run results and the tree being
        edited survive tab switches until the next dry-run or conversion replaces them. */}
    <div hidden={view !== "settings"}><Settings request={request} onOpenBehaviorTree={openBehaviorTree} /></div>
    <div hidden={view !== "bt"}><BehaviorTree request={request} seed={treeSeed} onSeedConsumed={clearTreeSeed} /></div>
    <div hidden={view !== "fleet"}>
    {snapshot && <div className="snapshot-modal" role="dialog" aria-modal="true" onClick={() => setSnapshot(null)}><div><h2>{snapshot.robotId} camera</h2><img src={snapshot.src} alt={`${snapshot.robotId} snapshot`} /><button onClick={() => setSnapshot(null)}>닫기</button></div></div>}

    <section>
      <div className="section-title"><h2>Fleet Overview</h2><span>{robots.length} robots</span></div>
      <div className="robot-grid">
        {robots.map((robot) => <article className="card" key={robot.robot_id}>
          <div className="card-title"><h3>{robot.display_name}</h3><span className={`state ${robot.connection_state.toLowerCase()}`}>{robot.connection_state}</span></div>
          <dl><dt>상태</dt><dd>{robot.operational_state}</dd><dt>배터리</dt><dd>{robot.battery_percentage ?? "unknown"}%</dd><dt>현재 Task</dt><dd>{robot.current_task_id ?? "-"}</dd><dt>위치</dt><dd>{robot.pose ? `${robot.pose.x ?? "?"}, ${robot.pose.y ?? "?"}` : "stale/unknown"}</dd></dl>
          <p className="capabilities">{robot.capabilities.join(" · ") || "capability 없음"}</p>
          {robot.health_issues.length > 0 && <p className="warning">{robot.health_issues.join(", ")}</p>}
          <div className="card-actions"><button onClick={() => void runRobotTask(robot.robot_id)}>Task 실행</button><button onClick={() => void requestSnapshot(robot.robot_id)}>Snapshot</button><button className="danger ghost" onClick={() => void stopRobot(robot.robot_id)}>정지</button></div>
        </article>)}
      </div>
    </section>

    {approvals.length > 0 && <section>
      <div className="section-title"><h2>승인 대기</h2><span>{approvals.length} requests</span></div>
      <div className="robot-grid">{approvals.map((approval) => <article className="card approval" key={approval.approval_id}>
        <div className="card-title"><h3>{approval.action}</h3><span className="state degraded">PENDING</span></div>
        <p>robot: {approval.robot_id}</p><p>{approval.reason}</p><small>requested by {approval.requested_by}</small>
        <div className="card-actions"><button onClick={() => void decideApproval(approval.approval_id, "approve")}>승인 및 실행</button><button className="danger ghost" onClick={() => void decideApproval(approval.approval_id, "reject")}>거부</button></div>
      </article>)}</div>
    </section>}

    <section className="two-column">
      <div>
        <div className="section-title"><h2>Mission</h2></div>
        <form className="goal-form" onSubmit={submitGoal}>
          <textarea value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="전체 로봇이 수행할 목표를 입력합니다." />
          <button type="submit">검증된 계획 생성</button>
        </form>
        <div className="mission-list">{missions.map((mission) => <article className="card mission" key={mission.mission_id}>
          <div className="card-title"><h3>{mission.goal}</h3><span className="state">{mission.status}</span></div>
          <small>{mission.mission_id} · plan v{mission.plan_version}</small>
          <div className="card-actions"><button onClick={() => void missionAction(mission.mission_id, "start")}>시작</button><button onClick={() => void missionAction(mission.mission_id, "run")}>1회 실행</button><button onClick={() => void missionAction(mission.mission_id, mission.status === "PAUSED" ? "resume" : "pause")}>{mission.status === "PAUSED" ? "재개" : "일시정지"}</button><button className="danger ghost" onClick={() => void missionAction(mission.mission_id, "cancel")}>취소</button></div>
          <div className="dag">{mission.tasks.map((task) => <div className="task" key={task.task_id}>
            <strong>{task.task_id}</strong><span>{task.status}</span><p>{task.instruction}</p><small>robot: {task.assigned_robot_id ?? "미배정"} · deps: {task.depends_on.join(", ") || "없음"}</small>
          </div>)}</div>
        </article>)}</div>
      </div>
      <div>
        <div className="section-title"><h2>Event Timeline</h2><span>최근 {events.length}</span></div>
        <div className="timeline">{events.map((event) => <article key={event.event_id}>
          <time>{new Date(event.occurred_at).toLocaleTimeString()}</time>
          <div><strong>{event.event_type}</strong><p>{event.aggregate_id}</p></div>
        </article>)}</div>
      </div>
    </section>
    </div>
  </main>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
