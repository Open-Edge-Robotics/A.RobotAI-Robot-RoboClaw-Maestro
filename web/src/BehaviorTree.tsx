import React, { useCallback, useEffect, useState } from "react";
import type { DryRunResult } from "./Settings";

export type TreeSeed = { goal: string; result: DryRunResult };

type BTNode = {
  type: string;
  name?: string;
  children?: BTNode[];
  robot_id?: string;
  skill?: string;
  params?: Record<string, unknown>;
  timeout_sec?: number;
  max_attempts?: number;
  success_count?: number;
  condition?: string;
  interval_sec?: number;
};

type BTDocument = {
  name?: string;
  goal?: string;
  loop?: { enabled: boolean; interval_sec: number; max_cycles: number };
  root: BTNode;
};

type Issue = { path: string; level: string; message: string };
type Report = { document: BTDocument; valid: boolean; issues: Issue[] };
type Catalog = {
  robots: { robot_id: string; source: string; capabilities: string[] }[];
  conditions: string[];
};
type Saved = { name: string; updated_at: string };

type Props = {
  request: (path: string, init?: RequestInit) => Promise<any>;
  seed: TreeSeed | null;
  onSeedConsumed: () => void;
};

const COMPOSITES = ["Sequence", "Fallback", "Parallel"];
const NODE_TYPES = [...COMPOSITES, "Action", "Condition", "AlwaysSuccess", "SleepBetweenCycles"];
const ICONS: Record<string, string> = {
  Sequence: "→", Fallback: "?", Parallel: "⇉", Action: "▶", Condition: "◇", AlwaysSuccess: "✓", SleepBetweenCycles: "⏱"
};

function emptyDocument(): BTDocument {
  return { goal: "", loop: { enabled: false, interval_sec: 0, max_cycles: 0 }, root: { type: "Sequence", name: "Root", children: [] } };
}

function newNode(type: string): BTNode {
  if (COMPOSITES.includes(type)) return { type, name: type, children: [], ...(type === "Parallel" ? { success_count: 1 } : {}) };
  if (type === "Action") return { type, name: "action", robot_id: "", skill: "", params: {}, timeout_sec: 300, max_attempts: 1 };
  if (type === "Condition") return { type, name: "battery", condition: "CheckBatterySufficient", robot_id: "", params: { min_pct: 20 } };
  if (type === "SleepBetweenCycles") return { type, name: type, interval_sec: 60 };
  return { type, name: type };
}

function nodeAt(root: BTNode, path: number[]): BTNode | undefined {
  let node: BTNode | undefined = root;
  for (const index of path) node = node?.children?.[index];
  return node;
}

// Returns a copy of root with fn applied to the parent of path (or root itself for []).
function updateAt(root: BTNode, path: number[], fn: (node: BTNode) => BTNode): BTNode {
  if (path.length === 0) return fn(root);
  const [head, ...rest] = path;
  const children = [...(root.children ?? [])];
  children[head] = updateAt(children[head], rest, fn);
  return { ...root, children };
}

function pathKey(path: number[]): string {
  return ["root", ...path].join(".");
}

function defaultName(): string {
  const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
  return `plan-${stamp}`;
}

export function BehaviorTree({ request, seed, onSeedConsumed }: Props) {
  const [catalog, setCatalog] = useState<Catalog>({ robots: [], conditions: [] });
  const [saved, setSaved] = useState<Saved[]>([]);
  const [name, setName] = useState("");
  const [doc, setDoc] = useState<BTDocument>(emptyDocument);
  const [jsonText, setJsonText] = useState(() => JSON.stringify(emptyDocument(), null, 2));
  const [jsonError, setJsonError] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [addType, setAddType] = useState("Action");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [nextCatalog, nextSaved] = await Promise.all([
        request("/api/v1/behavior-trees/catalog"),
        request("/api/v1/behavior-trees")
      ]);
      setCatalog(nextCatalog);
      setSaved(nextSaved);
    } catch (cause) { setError(String(cause)); }
  }, [request]);

  useEffect(() => { void load(); }, [load]);

  function replaceDocument(next: BTDocument) {
    setDoc(next);
    setJsonText(JSON.stringify(next, null, 2));
    setJsonError("");
  }

  // Convert a dry-run plan handed over from the settings screen.
  useEffect(() => {
    if (!seed) return;
    onSeedConsumed();
    const treeName = defaultName();
    void (async () => {
      try {
        const converted: Report = await request("/api/v1/behavior-trees/from-plan", {
          method: "POST",
          body: JSON.stringify({ name: treeName, goal: seed.goal, tasks: seed.result.tasks, repeat: seed.result.repeat })
        });
        setName(treeName);
        replaceDocument(converted.document);
        setReport(converted);
        setSelected([]);
        setNotice("Dry-run 계획을 변환했습니다. 저장하려면 이름을 확인하고 저장하세요.");
      } catch (cause) { setError(String(cause)); }
    })();
  }, [seed, onSeedConsumed, request]);

  // Re-validate shortly after each edit so issues track the tree as it changes.
  useEffect(() => {
    const id = setTimeout(async () => {
      try {
        setReport(await request("/api/v1/behavior-trees/validate", { method: "POST", body: JSON.stringify({ document: doc }) }));
      } catch (cause) { setError(String(cause)); }
    }, 400);
    return () => clearTimeout(id);
  }, [doc, request]);

  function editNode(fn: (node: BTNode) => BTNode) {
    replaceDocument({ ...doc, root: updateAt(doc.root, selected, fn) });
  }

  function addChild() {
    const node = nodeAt(doc.root, selected);
    if (!node || !COMPOSITES.includes(node.type)) return;
    const index = node.children?.length ?? 0;
    editNode((current) => ({ ...current, children: [...(current.children ?? []), newNode(addType)] }));
    setSelected([...selected, index]);
  }

  function removeSelected() {
    if (selected.length === 0) return;
    const parent = selected.slice(0, -1);
    const index = selected[selected.length - 1];
    replaceDocument({ ...doc, root: updateAt(doc.root, parent, (node) => ({ ...node, children: (node.children ?? []).filter((_, i) => i !== index) })) });
    setSelected(parent);
  }

  function moveSelected(offset: number) {
    if (selected.length === 0) return;
    const parent = selected.slice(0, -1);
    const index = selected[selected.length - 1];
    const siblings = nodeAt(doc.root, parent)?.children ?? [];
    const target = index + offset;
    if (target < 0 || target >= siblings.length) return;
    replaceDocument({ ...doc, root: updateAt(doc.root, parent, (node) => {
      const children = [...(node.children ?? [])];
      [children[index], children[target]] = [children[target], children[index]];
      return { ...node, children };
    }) });
    setSelected([...parent, target]);
  }

  function editJson(text: string) {
    setJsonText(text);
    try {
      const parsed = JSON.parse(text) as BTDocument;
      if (!parsed || typeof parsed !== "object" || !parsed.root) throw new Error("root 노드가 필요합니다.");
      setDoc(parsed);
      setJsonError("");
      if (!nodeAt(parsed.root, selected)) setSelected([]);
    } catch (cause) { setJsonError(String(cause)); }
  }

  async function save() {
    setError("");
    try {
      const result: Report = await request(`/api/v1/behavior-trees/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify({ document: doc }) });
      replaceDocument(result.document);
      setReport(result);
      setNotice(result.valid ? "저장했습니다." : "저장했지만 검증 오류가 남아 있습니다.");
      await load();
    } catch (cause) { setError(String(cause)); }
  }

  async function open(treeName: string) {
    setError("");
    try {
      const result: Report = await request(`/api/v1/behavior-trees/${encodeURIComponent(treeName)}`);
      setName(treeName);
      replaceDocument(result.document);
      setReport(result);
      setSelected([]);
      setNotice("");
    } catch (cause) { setError(String(cause)); }
  }

  async function remove() {
    if (!saved.some((item) => item.name === name) || !window.confirm(`${name}을 삭제하시겠습니까?`)) return;
    try {
      await request(`/api/v1/behavior-trees/${encodeURIComponent(name)}`, { method: "DELETE" });
      setNotice("삭제했습니다.");
      await load();
    } catch (cause) { setError(String(cause)); }
  }

  function startNew() {
    setName(defaultName());
    replaceDocument(emptyDocument());
    setSelected([]);
    setNotice("");
  }

  const issuesFor = (path: number[]) => (report?.issues ?? []).filter((issue) => issue.path.startsWith(`${pathKey(path)}(`));
  const current = nodeAt(doc.root, selected);
  const robot = catalog.robots.find((item) => item.robot_id === current?.robot_id);
  const loop = doc.loop ?? { enabled: false, interval_sec: 0, max_cycles: 0 };

  function renderNode(node: BTNode, path: number[]): React.ReactNode {
    const issues = issuesFor(path);
    const isSelected = pathKey(path) === pathKey(selected);
    const detail = node.type === "Action" ? `${node.robot_id || "?"} · ${node.skill || "?"}`
      : node.type === "Condition" ? `${node.robot_id || "fleet"} · ${node.condition}`
      : node.type === "Parallel" ? `success ${node.success_count ?? 1}/${node.children?.length ?? 0}`
      : node.type === "SleepBetweenCycles" ? `${node.interval_sec}s` : "";
    return <li key={pathKey(path)}>
      <button className={`bt-node bt-${node.type.toLowerCase()}${isSelected ? " active" : ""}${issues.some((issue) => issue.level === "error") ? " invalid" : ""}`} onClick={() => setSelected(path)} title={issues.map((issue) => issue.message).join("\n")}>
        <span className="bt-icon">{ICONS[node.type] ?? "•"}</span>
        <strong>{node.type}</strong> <span>{node.name}</span> {detail && <small>{detail}</small>}
      </button>
      {node.children && node.children.length > 0 && <ul>{node.children.map((child, index) => renderNode(child, [...path, index]))}</ul>}
    </li>;
  }

  const numberField = (label: string, key: "timeout_sec" | "max_attempts" | "success_count" | "interval_sec") =>
    <label>{label}<input type="number" value={current?.[key] ?? ""} onChange={(e) => editNode((node) => ({ ...node, [key]: Number(e.target.value) }))} /></label>;

  return <>
    {error && <div className="error">{error}</div>}
    <section>
      <div className="section-title"><h2>Behavior Tree</h2><span>Robo-Claw BT 노드 체계 · 실행 전 구성/검증용</span></div>
      <div className="bt-toolbar">
        <select aria-label="저장된 Behavior Tree" value="" onChange={(e) => e.target.value && void open(e.target.value)}>
          <option value="">저장된 BT 열기…</option>
          {saved.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
        </select>
        <button onClick={startNew}>새 BT</button>
        <input aria-label="BT 이름" placeholder="이름 (소문자, 숫자, -, _)" value={name} onChange={(e) => setName(e.target.value)} />
        <button onClick={() => void save()} disabled={!name || Boolean(jsonError)}>저장</button>
        <button className="danger ghost" onClick={() => void remove()} disabled={!saved.some((item) => item.name === name)}>삭제</button>
        {report && <span className={`state ${report.valid ? "online" : "offline"}`}>{report.valid ? "검증 통과" : `오류 ${report.issues.filter((issue) => issue.level === "error").length}`}</span>}
        <span className="notice">{notice}</span>
      </div>
      <div className="bt-loop">
        <label><input type="checkbox" checked={loop.enabled} onChange={(e) => replaceDocument({ ...doc, loop: { ...loop, enabled: e.target.checked } })} /> 주기 반복</label>
        <label>간격(초)<input type="number" value={loop.interval_sec} onChange={(e) => replaceDocument({ ...doc, loop: { ...loop, interval_sec: Number(e.target.value) } })} /></label>
        <label>최대 사이클(0=무제한)<input type="number" value={loop.max_cycles} onChange={(e) => replaceDocument({ ...doc, loop: { ...loop, max_cycles: Number(e.target.value) } })} /></label>
        {doc.goal && <small>목표: {doc.goal}</small>}
      </div>
    </section>

    <section className="bt-layout">
      <div className="card">
        <ul className="bt-tree">{renderNode(doc.root, [])}</ul>
      </div>
      <div className="card bt-inspector">
        <h3>선택 노드: {current ? `${current.type} ${current.name ?? ""}` : "-"}</h3>
        {current && <>
          <div className="bt-actions">
            {COMPOSITES.includes(current.type) && <>
              <select aria-label="추가할 노드 타입" value={addType} onChange={(e) => setAddType(e.target.value)}>{NODE_TYPES.map((type) => <option key={type}>{type}</option>)}</select>
              <button onClick={addChild}>자식 추가</button>
            </>}
            <button onClick={() => moveSelected(-1)} disabled={selected.length === 0}>위로</button>
            <button onClick={() => moveSelected(1)} disabled={selected.length === 0}>아래로</button>
            <button className="danger ghost" onClick={removeSelected} disabled={selected.length === 0}>삭제</button>
          </div>
          <div className="bt-form">
            <label>이름<input value={current.name ?? ""} onChange={(e) => editNode((node) => ({ ...node, name: e.target.value }))} /></label>
            {current.type === "Parallel" && numberField("success_count", "success_count")}
            {(current.type === "Action" || current.type === "Condition") && <label>로봇<select value={current.robot_id ?? ""} onChange={(e) => editNode((node) => ({ ...node, robot_id: e.target.value }))}>
              <option value="">{current.type === "Condition" ? "(fleet)" : "선택…"}</option>
              {catalog.robots.map((item) => <option key={item.robot_id} value={item.robot_id}>{item.robot_id} ({item.source})</option>)}
            </select></label>}
            {current.type === "Action" && <>
              <label>스킬<select value={current.skill ?? ""} onChange={(e) => editNode((node) => ({ ...node, skill: e.target.value }))}>
                <option value="">선택…</option>
                {current.skill && !robot?.capabilities.includes(current.skill) && <option value={current.skill}>{current.skill} (로봇에 없음)</option>}
                {(robot?.capabilities ?? []).map((skill) => <option key={skill}>{skill}</option>)}
              </select></label>
              {numberField("timeout_sec", "timeout_sec")}
              {numberField("max_attempts", "max_attempts")}
            </>}
            {current.type === "Condition" && <label>조건<select value={current.condition ?? ""} onChange={(e) => editNode((node) => ({ ...node, condition: e.target.value }))}>{catalog.conditions.map((item) => <option key={item}>{item}</option>)}</select></label>}
            {(current.type === "Action" || current.type === "Condition") && <ParamsField key={pathKey(selected)} value={current.params ?? {}} onChange={(params) => editNode((node) => ({ ...node, params }))} />}
            {current.type === "SleepBetweenCycles" && numberField("interval_sec", "interval_sec")}
          </div>
          {issuesFor(selected).map((issue) => <p key={issue.message} className={issue.level === "error" ? "warning" : "notice"}>{issue.message}</p>)}
        </>}
        {report && report.issues.length > 0 && <details open={!report.valid}><summary>전체 검증 결과 ({report.issues.length})</summary>
          <ul className="bt-issues">{report.issues.map((issue) => <li key={`${issue.path}-${issue.message}`}><code>{issue.path}</code> {issue.message}</li>)}</ul>
        </details>}
      </div>
    </section>

    <section>
      <details><summary>JSON 편집</summary>
        <textarea className="bt-json" aria-label="Behavior Tree JSON" value={jsonText} onChange={(e) => editJson(e.target.value)} spellCheck={false} />
        {jsonError && <p className="warning">{jsonError}</p>}
      </details>
    </section>
  </>;
}

function ParamsField({ value, onChange }: { value: Record<string, unknown>; onChange: (params: Record<string, unknown>) => void }) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2));
  const [invalid, setInvalid] = useState(false);
  return <label>params (JSON)
    <textarea value={text} spellCheck={false} onChange={(e) => {
      setText(e.target.value);
      try {
        const parsed = JSON.parse(e.target.value);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
        setInvalid(false);
        onChange(parsed as Record<string, unknown>);
      } catch { setInvalid(true); }
    }} />
    {invalid && <small className="warning">올바른 JSON 객체가 아닙니다.</small>}
  </label>;
}
