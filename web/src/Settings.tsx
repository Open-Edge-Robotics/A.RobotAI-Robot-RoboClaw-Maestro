import React, { FormEvent, useCallback, useEffect, useState } from "react";

type KnowledgeDocument = {
  name: string;
  title: string;
  content: string;
  is_default: boolean;
  updated_at: string | null;
};

type Candidate = { robot_id: string; connection_state: string };

export type DryRunTask = {
  task_id: string;
  instruction: string;
  skill_name: string;
  params: Record<string, unknown>;
  on_failure: string;
  assigned_robot_id: string;
  stage: number;
  required_capabilities: string[];
  depends_on: string[];
  preferred_robot_id: string;
  timeout_sec: number;
  max_attempts: number;
  candidates: Candidate[];
  warnings: string[];
};

export type DryRunResult = {
  valid: boolean;
  assignable: boolean;
  error: string;
  summary: string;
  system_prompt: string;
  user_prompt: string;
  raw_response: string;
  latency_sec: number;
  live_robot_ids: string[];
  profile_robot_ids: string[];
  tasks: DryRunTask[];
  repeat: { interval_sec: number; max_cycles: number } | null;
};

type Props = {
  request: (path: string, init?: RequestInit) => Promise<any>;
  onOpenBehaviorTree: (goal: string, result: DryRunResult) => void;
};

export function Settings({ request, onOpenBehaviorTree }: Props) {
  // Local error state: the fleet view's periodic refresh clears the shared banner.
  const [error, setError] = useState("");
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [selected, setSelected] = useState("role");
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState("");
  const [goal, setGoal] = useState("");
  const [includeProfiles, setIncludeProfiles] = useState(true);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<DryRunResult | null>(null);
  const [resultGoal, setResultGoal] = useState("");

  const current = documents.find((document) => document.name === selected);
  const dirty = current !== undefined && draft !== current.content;

  const load = useCallback(async () => {
    try {
      const next: KnowledgeDocument[] = await request("/api/v1/knowledge");
      setDocuments(next);
      setDraft(next.find((document) => document.name === selected)?.content ?? "");
    } catch (cause) { setError(String(cause)); }
    // Reload only when the token changes; switching documents uses the cached list.
  }, [request]);

  useEffect(() => { void load(); }, [load]);

  function replaceDocument(document: KnowledgeDocument) {
    setDocuments((items) => items.map((item) => item.name === document.name ? document : item));
    setDraft(document.content);
  }

  function selectDocument(name: string) {
    if (dirty && !window.confirm("저장하지 않은 변경 내용을 버리시겠습니까?")) return;
    setSelected(name);
    setDraft(documents.find((document) => document.name === name)?.content ?? "");
    setNotice("");
  }

  async function save() {
    setError("");
    try {
      replaceDocument(await request(`/api/v1/knowledge/${selected}`, {
        method: "PUT", body: JSON.stringify({ content: draft })
      }));
      setNotice("저장했습니다. 다음 planning부터 반영됩니다.");
    } catch (cause) { setError(String(cause)); }
  }

  async function reset() {
    if (!window.confirm(`${current?.title ?? selected} 문서를 기본값으로 되돌리시겠습니까?`)) return;
    try {
      replaceDocument(await request(`/api/v1/knowledge/${selected}/reset`, { method: "POST" }));
      setNotice("기본값으로 되돌렸습니다.");
    } catch (cause) { setError(String(cause)); }
  }

  async function runDryRun(event: FormEvent) {
    event.preventDefault();
    if (!goal.trim()) return;
    if (dirty) {
      if (!window.confirm("저장하지 않은 문서 변경이 있습니다. 저장한 뒤 Dry-run을 실행할까요?")) return;
      await save();
    }
    setRunning(true);
    setError("");
    try {
      setResult(await request("/api/v1/planning/dry-run", {
        method: "POST", body: JSON.stringify({ goal, include_profiles: includeProfiles })
      }));
      setResultGoal(goal);
    } catch (cause) { setError(String(cause)); }
    finally { setRunning(false); }
  }

  const stages = result ? [...new Set(result.tasks.map((task) => task.stage))] : [];

  return <>
    {error && <div className="error">{error}</div>}
    <section>
      <div className="section-title"><h2>Planning 문서</h2><span>Markdown · planner system prompt에 포함됩니다</span></div>
      <div className="settings-layout">
        <nav className="doc-list">
          {documents.map((document) => <button key={document.name} className={document.name === selected ? "active" : ""} onClick={() => selectDocument(document.name)}>
            <span>{document.title}</span>
            <small>{document.is_default ? "기본값" : "수정됨"}</small>
          </button>)}
        </nav>
        <div className="editor">
          <textarea aria-label={`${current?.title ?? selected} 편집`} value={draft} onChange={(e) => { setDraft(e.target.value); setNotice(""); }} spellCheck={false} />
          <div className="editor-actions">
            <small>{current?.name}.md{current?.updated_at ? ` · ${new Date(current.updated_at).toLocaleString()} 저장` : ""}{dirty ? " · 저장 안 됨" : ""}</small>
            <span className="notice">{notice}</span>
            <button className="ghost" onClick={() => void reset()} disabled={current?.is_default}>기본값으로</button>
            <button onClick={() => void save()} disabled={!dirty}>저장</button>
          </div>
        </div>
      </div>
    </section>

    <section>
      <div className="section-title"><h2>Plan Dry-run</h2><span>Mission을 만들거나 로봇에 명령하지 않습니다</span></div>
      <form className="goal-form" onSubmit={runDryRun}>
        <textarea value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="검증할 목표를 입력합니다. 예: 내가 없는 동안 집안을 정리해줘" />
        <div className="dry-run-actions">
          <label><input type="checkbox" checked={includeProfiles} onChange={(e) => setIncludeProfiles(e.target.checked)} /> 로봇 프로필 포함</label>
          <button type="submit" disabled={running}>{running ? "planning 중…" : "Dry-run 실행"}</button>
        </div>
      </form>

      {result && <div className="dry-run-result">
        <div className="badges">
          <span className={`state ${result.valid ? "online" : "offline"}`}>{result.valid ? "검증 통과" : "검증 거부"}</span>
          {result.valid && <span className={`state ${result.assignable ? "online" : "degraded"}`}>{result.assignable ? "모든 Task 배정 가능" : "배정 불가 Task 있음"}</span>}
          {result.repeat && <span className="state">반복 {result.repeat.interval_sec}s{result.repeat.max_cycles ? ` × ${result.repeat.max_cycles}` : " · 중단 시까지"}</span>}
          <small>연결 로봇 {result.live_robot_ids.length} · 프로필 로봇 {result.profile_robot_ids.length} · {result.latency_sec}s</small>
          {result.valid && <button onClick={() => onOpenBehaviorTree(resultGoal, result)}>Behavior Tree로 변환</button>}
        </div>
        {result.error && <div className="error">{result.error}</div>}
        {result.summary && <p>{result.summary}</p>}
        {stages.map((stage) => <div key={stage} className="stage">
          <h3>단계 {stage}{result.tasks.filter((task) => task.stage === stage).length > 1 ? " · 병렬" : ""}</h3>
          <div className="robot-grid">{result.tasks.filter((task) => task.stage === stage).map((task) => <article className="card" key={task.task_id}>
            <div className="card-title"><h3>{task.task_id}</h3><small>{task.timeout_sec}s · 최대 {task.max_attempts}회</small></div>
            <p>{task.instruction}</p>
            <p className="capabilities">{task.skill_name ? <code>{task.skill_name}({Object.keys(task.params).length ? JSON.stringify(task.params) : ""})</code> : task.required_capabilities.join(" · ") || "capability 없음"}</p>
            <small>deps: {task.depends_on.join(", ") || "없음"} · 실패 시 {task.on_failure === "continue" ? "계속" : "중단"}{task.assigned_robot_id ? ` · 배정: ${task.assigned_robot_id}` : ""}{task.preferred_robot_id ? ` (지정)` : ""}</small>
            <div className="candidates">{task.candidates.map((candidate) => <span key={candidate.robot_id} className={`state ${candidate.connection_state.toLowerCase()}`}>{candidate.robot_id} {candidate.connection_state}</span>)}</div>
            {task.warnings.map((warning) => <p key={warning} className="warning">{warning}</p>)}
          </article>)}</div>
        </div>)}
        <details><summary>System prompt</summary><pre>{result.system_prompt}</pre></details>
        <details><summary>User prompt (fleet catalog 포함)</summary><pre>{result.user_prompt}</pre></details>
        <details open={!result.valid}><summary>LLM 원본 응답</summary><pre>{result.raw_response}</pre></details>
      </div>}
    </section>
  </>;
}
