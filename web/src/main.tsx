import React, { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { BehaviorTree, type TreeSeed } from "./BehaviorTree";
import { MissionTree } from "./MissionTree";
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
  skill_name?: string;
  params?: Record<string, unknown>;
};

type Mission = {
  mission_id: string;
  goal: string;
  status: string;
  plan_version: number;
  created_at?: string;
  tasks: Task[];
  behavior_tree_run?: { status: string; active: boolean; paused: boolean; cycle: number; message: string } | null;
};

function sortMissionsNewestFirst(items: Mission[]): Mission[] {
  return [...items].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
}

function formatCreatedAt(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

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

// The console polls on a fixed interval instead of refreshing on every stream event:
// telemetry arrives several times per second and carries little operator value.
const REFRESH_INTERVAL_MS = 10_000;
const NOISY_EVENT_TYPES = new Set(["robot.telemetry_updated", "robot.heartbeat"]);

// What each mission button does, shown as a tooltip and in the help panel.
const MISSION_BUTTON_HELP = {
  rerunFailed: "FAILED·PARTIALLY_SUCCEEDED·CANCELLED에서 동작합니다. 성공한 Task는 그대로 두고 나머지를 다시 CREATED로 돌려 BT를 실행합니다. 트리는 성공한 Action을 건너뛰므로 실패한 단계부터 이어집니다.",
  rerunAll: "끝난 모든 Mission에서 동작합니다. 모든 Task를 다시 CREATED로 돌려 BT를 처음부터 실행합니다.",
  tree: "DRAFT·READY·RUNNING에서 동작합니다. Maestro가 Behavior Tree를 끝까지 자동 실행합니다. 각 Action은 지정 로봇에 ExecuteSkill로 보내고, 단계 순서·실패 처리·반복을 트리대로 지킵니다. 실행 중에는 1회 실행이 잠깁니다.",
  start: "DRAFT에서만 동작합니다. Mission을 READY(실행 대기)로 바꿀 뿐 로봇에 명령을 보내지 않습니다.",
  run: "DRAFT·READY·RUNNING에서 동작합니다. 의존 관계가 풀린 Task를 로봇에 배정·실행하고 그 결과를 기다립니다. 단계마다 다시 눌러야 다음 Task가 실행됩니다.",
  pause: "READY·RUNNING에서 동작합니다. 1회 실행과 BT의 다음 노드 진행을 멈춥니다. 이미 로봇이 수행 중인 Action은 멈추지 않습니다.",
  resume: "PAUSED에서 동작합니다. RUNNING으로 되돌려 BT를 이어서 실행하거나 1회 실행을 할 수 있게 합니다.",
  cancel: "끝나지 않은 모든 상태에서 동작합니다. 남은 Task를 취소합니다. 로봇이 수행 중인 Task는 원격 취소가 지원되지 않아 끝까지 진행될 수 있습니다."
};

const FINISHED_MISSION_STATUSES = new Set([
  "SUCCEEDED",
  "PARTIALLY_SUCCEEDED",
  "FAILED",
  "CANCELLED"
]);

// Mirrors the orchestrator state machine so the console never posts a transition
// the backend will reject with 409.
function missionActions(status: string, treeActive = false) {
  const finished = FINISHED_MISSION_STATUSES.has(status);
  return {
    start: status === "DRAFT" && !treeActive,
    run: !finished && !treeActive && status !== "PAUSED" && status !== "CANCEL_REQUESTED",
    tree: !treeActive && (status === "DRAFT" || status === "READY" || status === "RUNNING"),
    rerunFailed: !treeActive && (status === "FAILED" || status === "PARTIALLY_SUCCEEDED" || status === "CANCELLED"),
    rerunAll: !treeActive && finished,
    pause: status === "READY" || status === "RUNNING",
    resume: status === "PAUSED",
    cancel: !finished
  };
}

function toMission(payload: unknown): Mission | null {
  const candidate = payload as { mission_id?: unknown; mission?: unknown } | null;
  const mission = (candidate && "mission" in candidate ? candidate.mission : candidate) as
    | Mission
    | null;
  return mission && typeof mission.mission_id === "string" ? mission : null;
}

function App() {
  const [robots, setRobots] = useState<Robot[]>([]);
  const [missions, setMissions] = useState<Mission[]>([]);
  const [events, setEvents] = useState<FleetEvent[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [goal, setGoal] = useState("");
  const [token, setToken] = useState(() => localStorage.getItem("maestro-token") ?? "");
  const [error, setError] = useState("");
  const [refreshTick, setRefreshTick] = useState(0);
  const [planning, setPlanning] = useState<{ goal: string; startedAt: number } | null>(null);
  const [planElapsed, setPlanElapsed] = useState(0);
  const [planResult, setPlanResult] = useState<{ ok: boolean; message: string } | null>(null);
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
    if (!response.ok) {
      const body = await response.text();
      let detail = body;
      try {
        const parsed = JSON.parse(body) as { detail?: unknown };
        if (typeof parsed.detail === "string") detail = parsed.detail;
      } catch {
        // keep the raw response body
      }
      throw new Error(`${response.status}: ${detail}`);
    }
    return response.json();
  }, [token]);

  const refreshing = useRef(false);
  const refreshQueued = useRef(false);
  const refresh = useCallback(async () => {
    if (refreshing.current) {
      // Queue instead of dropping: a dropped refresh left the mission card stale
      // right after a control action until the next poll.
      refreshQueued.current = true;
      return;
    }
    refreshing.current = true;
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
    } finally {
      refreshing.current = false;
      if (refreshQueued.current) {
        refreshQueued.current = false;
        void refresh();
      }
    }
  }, [request]);

  // Stream events are buffered and flushed together with the 10s poll.
  const pendingEvents = useRef<FleetEvent[]>([]);
  const flushEvents = useCallback(() => {
    if (pendingEvents.current.length === 0) return;
    const incoming = pendingEvents.current;
    pendingEvents.current = [];
    setEvents((current) => {
      const seen = new Set<string>();
      return [...incoming.reverse(), ...current].filter((item) => !seen.has(item.event_id) && seen.add(item.event_id)).slice(0, 100);
    });
  }, []);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => { void refresh(); flushEvents(); setRefreshTick((value) => value + 1); }, REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refresh, flushEvents]);
  useEffect(() => {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const query = token ? `?token=${encodeURIComponent(token)}` : "";
    const socket = new WebSocket(`${scheme}://${location.host}/api/v1/events/stream${query}`);
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data) as FleetEvent;
      if (!NOISY_EVENT_TYPES.has(event.event_type)) pendingEvents.current.push(event);
    };
    return () => socket.close();
  }, [token]);

  useEffect(() => {
    if (!planning) return;
    setPlanElapsed(0);
    const id = setInterval(() => setPlanElapsed(Math.floor((Date.now() - planning.startedAt) / 1000)), 1000);
    return () => clearInterval(id);
  }, [planning]);

  async function submitGoal(event: FormEvent) {
    event.preventDefault();
    if (!goal.trim() || planning) return;
    const startedAt = Date.now();
    setPlanning({ goal, startedAt });
    setPlanResult(null);
    try {
      const created = await request("/api/v1/goals", { method: "POST", body: JSON.stringify({ goal, auto_start: false }) });
      const seconds = Math.round((Date.now() - startedAt) / 1000);
      const taskCount = Array.isArray(created?.tasks) ? created.tasks.length : 0;
      setPlanResult({ ok: true, message: `계획 생성 완료 · Task ${taskCount}개 · ${seconds}초` });
      setGoal("");
      applyMission(created);
      await refresh();
    } catch (cause) {
      setPlanResult({ ok: false, message: `계획 생성 실패: ${String(cause)}` });
      setError(String(cause));
    } finally {
      setPlanning(null);
    }
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

  function applyMission(payload: unknown) {
    const mission = toMission(payload);
    if (!mission) return;
    setMissions((current) => current.some((item) => item.mission_id === mission.mission_id)
      ? current.map((item) => (item.mission_id === mission.mission_id ? { ...item, ...mission } : item))
      : [mission, ...current]);
  }

  async function missionAction(missionId: string, action: "start" | "pause" | "resume" | "cancel" | "run" | "behavior-tree/run" | "rerun", body?: unknown) {
    try {
      const result = await request(`/api/v1/missions/${missionId}/${action}`, {
        method: "POST", ...(body === undefined ? {} : { body: JSON.stringify(body) })
      });
      applyMission(result);
      await refresh();
      setRefreshTick((value) => value + 1);
    }
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

    <section>
      <div>
        <div className="section-title"><h2>Mission</h2><span>{REFRESH_INTERVAL_MS / 1000}초마다 갱신</span></div>
        <form className="goal-form" onSubmit={submitGoal}>
          <textarea value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="전체 로봇이 수행할 목표를 입력합니다." />
          <button type="submit" disabled={Boolean(planning)} title="LLM이 목표를 Task로 나누고 형식·capability를 검사한 뒤 DRAFT Mission으로 저장합니다. 로봇은 움직이지 않습니다.">{planning ? "생성 중…" : "계획 생성"}</button>
        </form>
        {planning && <div className="plan-progress" role="status">
          <div className="plan-progress-bar"><span /></div>
          <p>LLM이 계획을 만들고 있습니다 · {planElapsed}초 경과</p>
          <small>LLM 호출 → Plan 형식·capability 검사 → DRAFT Mission 저장. 서버가 단계별 진행률을 보내지 않아 경과 시간만 표시합니다.</small>
        </div>}
        {!planning && planResult && <div className={`plan-progress ${planResult.ok ? "done" : "failed"}`} role="status">
          <p>{planResult.message}</p>
        </div>}
        <details className="mission-help"><summary>버튼 설명</summary><dl>
          <dt>BT 실행</dt><dd>{MISSION_BUTTON_HELP.tree}</dd>
          <dt>실패부터 재실행</dt><dd>{MISSION_BUTTON_HELP.rerunFailed}</dd>
          <dt>처음부터 재실행</dt><dd>{MISSION_BUTTON_HELP.rerunAll}</dd>
          <dt>시작</dt><dd>{MISSION_BUTTON_HELP.start}</dd>
          <dt>1회 실행</dt><dd>{MISSION_BUTTON_HELP.run}</dd>
          <dt>일시정지</dt><dd>{MISSION_BUTTON_HELP.pause}</dd>
          <dt>재개</dt><dd>{MISSION_BUTTON_HELP.resume}</dd>
          <dt>취소</dt><dd>{MISSION_BUTTON_HELP.cancel}</dd>
        </dl></details>
        <div className="mission-list">{sortMissionsNewestFirst(missions).map((mission) => <article className="card mission" key={mission.mission_id}>
          <div className="card-title"><h3>{mission.goal}</h3><span className="state">{mission.status}</span></div>
          <small>{formatCreatedAt(mission.created_at)}{mission.created_at ? " · " : ""}{mission.mission_id} · plan v{mission.plan_version}</small>
          <div className="card-actions">{(() => {
            const treeActive = Boolean(mission.behavior_tree_run?.active);
            const allowed = missionActions(mission.status, treeActive);
            const paused = mission.status === "PAUSED";
            return <>
              <button title={MISSION_BUTTON_HELP.tree} disabled={!allowed.tree} onClick={() => void missionAction(mission.mission_id, "behavior-tree/run")}>{treeActive ? "BT 실행 중…" : "BT 실행"}</button>
              {allowed.rerunAll && <>
                <button title={MISSION_BUTTON_HELP.rerunFailed} disabled={!allowed.rerunFailed} onClick={() => void missionAction(mission.mission_id, "rerun", { failed_only: true })}>실패부터 재실행</button>
                <button title={MISSION_BUTTON_HELP.rerunAll} onClick={() => { if (window.confirm("모든 Task를 처음부터 다시 실행할까요? 성공한 Task도 로봇이 다시 수행합니다.")) void missionAction(mission.mission_id, "rerun", { failed_only: false }); }}>처음부터 재실행</button>
              </>}
              <button title={MISSION_BUTTON_HELP.start} disabled={!allowed.start} onClick={() => void missionAction(mission.mission_id, "start")}>시작</button>
              <button title={MISSION_BUTTON_HELP.run} disabled={!allowed.run} onClick={() => void missionAction(mission.mission_id, "run")}>1회 실행</button>
              <button title={paused ? MISSION_BUTTON_HELP.resume : MISSION_BUTTON_HELP.pause} disabled={paused ? !allowed.resume : !allowed.pause} onClick={() => void missionAction(mission.mission_id, paused ? "resume" : "pause")}>{paused ? "재개" : "일시정지"}</button>
              <button title={MISSION_BUTTON_HELP.cancel} className="danger ghost" disabled={!allowed.cancel} onClick={() => void missionAction(mission.mission_id, "cancel")}>취소</button>
            </>;
          })()}</div>
          <MissionTree missionId={mission.mission_id} request={request} active={Boolean(mission.behavior_tree_run?.active)} refreshKey={refreshTick} />
          <div className="dag">{mission.tasks.map((task) => <div className="task" key={task.task_id}>
            <strong>{task.task_id}</strong><span>{task.status}</span><p>{task.instruction}</p><small>{task.skill_name ? `skill: ${task.skill_name} · ` : ""}{task.params?.preferred_robot_id ? `지정: ${String(task.params.preferred_robot_id)} · ` : ""}robot: {task.assigned_robot_id ?? "미배정"} · deps: {task.depends_on.join(", ") || "없음"}</small>
          </div>)}</div>
        </article>)}</div>
      </div>
      <details className="event-timeline">
        <summary>Event Timeline · 최근 {events.length} (telemetry 제외, {REFRESH_INTERVAL_MS / 1000}초마다 갱신)</summary>
        <div className="timeline">{events.map((event) => <article key={event.event_id}>
          <time>{new Date(event.occurred_at).toLocaleTimeString()}</time>
          <div><strong>{event.event_type}</strong><p>{event.aggregate_id}</p></div>
        </article>)}</div>
      </details>
    </section>
    </div>
  </main>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
