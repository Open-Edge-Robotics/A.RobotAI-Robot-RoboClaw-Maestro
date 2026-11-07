import React, { useMemo, useState } from "react";

/**
 * Robots report capabilities as a flat list of skill names, so they are grouped here
 * by Robo-Claw naming conventions. The first matching group wins; the rest go to "기타".
 */
const GROUPS: { label: string; match: (name: string) => boolean }[] = [
  { label: "정지·안전", match: (n) => /(^|_)stop|emergency|reset_emergency/.test(n) },
  { label: "이동·내비게이션", match: (n) => /navigate|move_relative|^rotate$|face_direction|follow_waypoints|patrol|explore|approach|localize|initial_pose|reachable|virtual_obstacle|get_location|identify_location|get_distance/.test(n) },
  { label: "자율·정리", match: (n) => /autonomous|tidy|cleanup|monitor/.test(n) },
  { label: "동료 협업", match: (n) => /peer|delegate|broadcast|yield|coordinate/.test(n) },
  { label: "인지·비전", match: (n) => /detect|scene|surroundings|camera|image|observe|find_object|search_object|scan|annotate|grasp_pose|object_pose|head_pan|vlm/.test(n) },
  { label: "지도", match: (n) => /map/.test(n) },
  { label: "조작·팔", match: (n) => /grip|grasp|arm_|joints|move_pose|place|pick|cloid_motion|packing_pose|cloid_/.test(n) },
  { label: "기억·RAG", match: (n) => /^rag_|memory|reflect/.test(n) },
  { label: "대화·알림", match: (n) => /message|speech|express|weather|web_search|datetime/.test(n) },
  { label: "IoT", match: (n) => /iot/.test(n) },
  { label: "파일·시스템", match: (n) => /file|script|ros_command|topics|upload|get_status|log_/.test(n) },
];

function groupCapabilities(names: string[]) {
  const groups = new Map<string, string[]>();
  for (const name of [...names].sort()) {
    const label = GROUPS.find((group) => group.match(name))?.label ?? "기타";
    groups.set(label, [...(groups.get(label) ?? []), name]);
  }
  const order = [...GROUPS.map((group) => group.label), "기타"];
  return order.filter((label) => groups.has(label)).map((label) => ({ label, names: groups.get(label)! }));
}

export function CapabilityTree({ capabilities }: { capabilities: string[] }) {
  const [filter, setFilter] = useState("");
  const [openAll, setOpenAll] = useState<boolean | null>(null);
  const query = filter.trim().toLowerCase();
  const groups = useMemo(() => groupCapabilities(capabilities), [capabilities]);
  if (!capabilities.length) return <p className="capabilities">capability 없음</p>;
  const visible = groups
    .map((group) => ({ ...group, names: query ? group.names.filter((name) => name.includes(query)) : group.names }))
    .filter((group) => group.names.length);
  return <div className="cap-tree">
    <div className="cap-tree-head">
      <span>capability {capabilities.length}개</span>
      <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="검색" aria-label="capability 검색" />
      <button type="button" className="ghost" onClick={() => setOpenAll(!(openAll ?? false))}>{openAll ? "모두 접기" : "모두 펼치기"}</button>
    </div>
    <ul className="cap-tree-body">
      {visible.map((group) => <li key={`${group.label}-${openAll}-${query ? "q" : ""}`}>
        <details open={Boolean(query) || Boolean(openAll)}>
          <summary><span className="cap-group">{group.label}</span><span className="cap-count">{group.names.length}</span></summary>
          <ul>{group.names.map((name) => <li key={name}><span className="cap-name">{name}</span></li>)}</ul>
        </details>
      </li>)}
      {!visible.length && <li className="cap-empty">일치하는 capability가 없습니다.</li>}
    </ul>
  </div>;
}
