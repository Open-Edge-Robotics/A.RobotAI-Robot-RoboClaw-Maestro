"""Behavior tree documents built from single-skill plans.

Node types mirror the Robo-Claw BT engine (``skills/autonomous_skill/core.py``):
Sequence/Fallback/Parallel composites, Condition/Action leaves, ``AlwaysSuccess``
for the ``CycleDone`` idiom, and ``SleepBetweenCycles`` for recurring loops.
Documents are JSON and are stored, converted, validated, and split into per-robot
views here. Mission trees are executed by ``application.bt_runner``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

COMPOSITES = {"Sequence", "Fallback", "Parallel"}
LEAVES = {"Action", "Condition", "AlwaysSuccess", "SleepBetweenCycles"}
CONDITIONS = {"CheckBatterySufficient"}
MAX_NODES = 500
MAX_DEPTH = 32
MAX_DOCUMENT_BYTES = 256 * 1024
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class UnknownTree(KeyError):
    pass


@dataclass(frozen=True, slots=True)
class TreeIssue:
    path: str
    level: str  # "error" | "warning"
    message: str


def _action(task: dict[str, Any]) -> dict[str, Any]:
    leaf = {
        "type": "Action",
        "name": task["task_id"],
        "robot_id": task.get("assigned_robot_id", ""),
        "skill": task.get("skill_name", ""),
        "params": dict(task.get("params", {})),
        "timeout_sec": task.get("timeout_sec", 300),
        "max_attempts": task.get("max_attempts", 1),
    }
    if task.get("on_failure") == "continue":
        # Robo-Claw idiom for "never fail the parent": Fallback[work, AlwaysSuccess].
        return {
            "type": "Fallback",
            "name": f"{task['task_id']}-optional",
            "children": [leaf, {"type": "AlwaysSuccess", "name": "Skipped"}],
        }
    return leaf


def _chains(tasks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group tasks into weakly connected dependency components, in input order."""
    parent = {str(task["task_id"]): str(task["task_id"]) for task in tasks}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for task in tasks:
        for dependency in task.get("depends_on") or []:
            if str(dependency) in parent:
                parent[find(str(dependency))] = find(str(task["task_id"]))
    groups: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        groups.setdefault(find(str(task["task_id"])), []).append(task)
    return list(groups.values())


def _chain_steps(chain: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stages: dict[int, list[dict[str, Any]]] = {}
    for task in chain:
        stages.setdefault(int(task.get("stage", 1)), []).append(task)
    steps: list[dict[str, Any]] = []
    for number in sorted(stages):
        members = [_action(task) for task in stages[number]]
        if len(members) == 1:
            steps.append(members[0])
        else:
            steps.append(
                {
                    "type": "Parallel",
                    "name": f"Stage {number}",
                    "success_count": len(members),
                    "children": members,
                }
            )
    return steps


def _chain_node(chain: list[dict[str, Any]]) -> dict[str, Any]:
    steps = _chain_steps(chain)
    if len(steps) == 1:
        return steps[0]
    robots = sorted({task.get("assigned_robot_id") or "?" for task in chain})
    return {"type": "Sequence", "name": f"Chain {'+'.join(robots)}", "children": steps}


def plan_to_tree(
    *,
    name: str,
    goal: str,
    tasks: list[dict[str, Any]],
    repeat: dict[str, Any] | None,
    battery_guard_pct: int | None = 20,
) -> dict[str, Any]:
    """Convert dry-run tasks into a tree.

    Tasks connected by ``depends_on`` form one chain: a Sequence of its stages, parallel
    within a stage. Independent chains (for example one per robot) run side by side in a
    Parallel, so a failure aborts only its own chain and never cancels unrelated robots.
    """
    chains = _chains(tasks)
    if len(chains) == 1:
        work = _chain_steps(chains[0])
    else:
        work = [
            {
                "type": "Parallel",
                "name": "Independent chains",
                "success_count": len(chains),
                "children": [_chain_node(chain) for chain in chains],
            }
        ]
    robots = sorted({task.get("assigned_robot_id", "") for task in tasks} - {""})
    guards = (
        [
            {
                "type": "Condition",
                "name": f"{robot_id} battery",
                "condition": "CheckBatterySufficient",
                "robot_id": robot_id,
                "params": {"min_pct": battery_guard_pct},
            }
            for robot_id in robots
        ]
        if battery_guard_pct
        else []
    )
    cycle = {"type": "Sequence", "name": "CycleWork", "children": work}
    if repeat:
        children = [
            *guards,
            {
                "type": "Fallback",
                "name": "CycleGuarded",
                "children": [cycle, {"type": "AlwaysSuccess", "name": "CycleDone"}],
            },
            {
                "type": "SleepBetweenCycles",
                "name": "SleepBetweenCycles",
                "interval_sec": repeat.get("interval_sec", 60),
            },
        ]
    else:
        children = [*guards, *work]
    return {
        "name": name,
        "goal": goal,
        "loop": {
            "enabled": bool(repeat),
            "interval_sec": (repeat or {}).get("interval_sec", 0),
            "max_cycles": (repeat or {}).get("max_cycles", 0),
        },
        "root": {"type": "Sequence", "name": "Root", "children": children},
    }


UNASSIGNED = "(미배정)"


def with_paths(node: dict[str, Any], path: str = "root") -> dict[str, Any]:
    """Return a copy where every node carries its ``path`` (``root.0.1``) for status lookup."""
    copy = {key: value for key, value in node.items() if key != "children"}
    copy["path"] = path
    if isinstance(node.get("children"), list):
        copy["children"] = [
            with_paths(child, f"{path}.{index}") for index, child in enumerate(node["children"])
        ]
    return copy


def tree_robots(node: Any) -> set[str]:
    """Robot ids referenced by Action/Condition leaves; unassigned actions use UNASSIGNED."""
    if not isinstance(node, dict):
        return set()
    found: set[str] = set()
    if node.get("type") == "Action":
        found.add(node.get("robot_id") or UNASSIGNED)
    elif node.get("type") == "Condition" and node.get("robot_id"):
        found.add(node["robot_id"])
    for child in node.get("children") or []:
        found |= tree_robots(child)
    return found


def _prune(node: dict[str, Any], robot_id: str) -> dict[str, Any] | None:
    kind = node.get("type")
    if kind == "Action":
        return node if (node.get("robot_id") or UNASSIGNED) == robot_id else None
    if kind == "Condition":
        return node if node.get("robot_id") in {robot_id, "", None} else None
    if kind in {"AlwaysSuccess", "SleepBetweenCycles"}:
        return node
    kept = [
        pruned
        for child in node.get("children") or []
        if (pruned := _prune(child, robot_id)) is not None
    ]
    if not any(child.get("type") not in {"AlwaysSuccess", "SleepBetweenCycles"} for child in kept):
        return None
    return {**node, "children": kept}


def split_by_robot(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-robot views of one fleet tree: each keeps only that robot's nodes, in order.

    The views are for display; Maestro executes the full tree so cross-robot ordering
    (a stage waiting for another robot) is preserved.
    """
    root = document.get("root")
    if not isinstance(root, dict):
        return {}
    views = {}
    for robot_id in sorted(tree_robots(root)):
        pruned = _prune(root, robot_id)
        if pruned is not None:
            views[robot_id] = pruned
    return views


def validate_tree(document: dict[str, Any], robot_skills: dict[str, set[str]]) -> list[TreeIssue]:
    """Check structure and that every Action robot exists and has the skill."""
    issues: list[TreeIssue] = []
    root = document.get("root")
    if not isinstance(root, dict):
        return [TreeIssue("root", "error", "root 노드가 없습니다.")]
    loop = document.get("loop") or {}
    if loop.get("enabled") and not float(loop.get("interval_sec") or 0) > 0:
        issues.append(TreeIssue("loop", "error", "반복 실행에는 interval_sec > 0이 필요합니다."))
    count = 0

    def visit(node: Any, path: str, depth: int) -> None:
        nonlocal count
        count += 1
        if count > MAX_NODES:
            if count == MAX_NODES + 1:
                issues.append(TreeIssue(path, "error", f"노드가 {MAX_NODES}개를 넘습니다."))
            return
        if depth > MAX_DEPTH:
            issues.append(TreeIssue(path, "error", f"깊이가 {MAX_DEPTH}를 넘습니다."))
            return
        if not isinstance(node, dict):
            issues.append(TreeIssue(path, "error", "노드는 객체여야 합니다."))
            return
        kind = node.get("type")
        label = f"{path}({node.get('name') or kind})"
        children = node.get("children")
        if kind in COMPOSITES:
            if not isinstance(children, list) or not children:
                issues.append(TreeIssue(label, "error", f"{kind}에는 자식 노드가 필요합니다."))
                return
            if kind == "Parallel":
                needed = node.get("success_count", 1)
                if not isinstance(needed, int) or not 1 <= needed <= len(children):
                    issues.append(
                        TreeIssue(label, "error", "success_count는 1 이상 자식 수 이하여야 합니다.")
                    )
            for index, child in enumerate(children):
                visit(child, f"{path}.{index}", depth + 1)
            return
        if kind not in LEAVES:
            issues.append(TreeIssue(label, "error", f"알 수 없는 노드 타입: {kind}"))
            return
        if children:
            issues.append(TreeIssue(label, "error", f"{kind}는 자식 노드를 가질 수 없습니다."))
        if kind == "Action":
            robot_id, skill = node.get("robot_id", ""), node.get("skill", "")
            if not skill:
                issues.append(TreeIssue(label, "error", "skill이 비어 있습니다."))
            if not robot_id:
                issues.append(TreeIssue(label, "error", "robot_id가 비어 있습니다."))
            elif robot_id not in robot_skills:
                issues.append(TreeIssue(label, "error", f"알 수 없는 로봇: {robot_id}"))
            elif skill and skill not in robot_skills[robot_id]:
                issues.append(
                    TreeIssue(label, "error", f"{robot_id}에 {skill} capability가 없습니다.")
                )
            if not isinstance(node.get("params", {}), dict):
                issues.append(TreeIssue(label, "error", "params는 객체여야 합니다."))
        elif kind == "Condition":
            if node.get("condition") not in CONDITIONS:
                issues.append(
                    TreeIssue(label, "error", f"알 수 없는 조건: {node.get('condition')}")
                )
            if node.get("robot_id") and node["robot_id"] not in robot_skills:
                issues.append(TreeIssue(label, "error", f"알 수 없는 로봇: {node['robot_id']}"))
        elif kind == "SleepBetweenCycles" and not float(node.get("interval_sec") or 0) > 0:
            issues.append(TreeIssue(label, "error", "interval_sec > 0이 필요합니다."))

    visit(root, "root", 1)
    if loop.get("enabled") and not _contains(root, "SleepBetweenCycles"):
        issues.append(
            TreeIssue("root", "warning", "반복 실행인데 SleepBetweenCycles 노드가 없습니다.")
        )
    return issues


def _contains(node: Any, kind: str) -> bool:
    if not isinstance(node, dict):
        return False
    return node.get("type") == kind or any(
        _contains(child, kind) for child in node.get("children") or []
    )


class BehaviorTreeStore:
    """Stores trees as ``<directory>/<name>.json``; ``directory=None`` keeps them in memory."""

    def __init__(self, directory: str | Path | None) -> None:
        self._directory = Path(directory) if directory else None
        self._memory: dict[str, tuple[dict[str, Any], datetime]] = {}
        if self._directory is not None:
            self._directory.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[dict[str, Any]]:
        if self._directory is None:
            items = [(name, saved) for name, (_, saved) in self._memory.items()]
        else:
            items = [
                (path.stem, datetime.fromtimestamp(path.stat().st_mtime, tz=UTC))
                for path in self._directory.glob("*.json")
            ]
        return [{"name": name, "updated_at": saved.isoformat()} for name, saved in sorted(items)]

    def get(self, name: str) -> dict[str, Any]:
        self._check(name)
        if self._directory is None:
            if name not in self._memory:
                raise UnknownTree(name)
            return json.loads(json.dumps(self._memory[name][0]))
        path = self._directory / f"{name}.json"
        if not path.is_file():
            raise UnknownTree(name)
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, name: str, document: dict[str, Any]) -> dict[str, Any]:
        self._check(name)
        document = {**document, "name": name}
        encoded = json.dumps(document, ensure_ascii=False, indent=2)
        if len(encoded.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise ValueError(f"behavior tree exceeds {MAX_DOCUMENT_BYTES} bytes")
        if self._directory is None:
            self._memory[name] = (json.loads(encoded), datetime.now(UTC))
        else:
            handle, temporary = tempfile.mkstemp(dir=self._directory, suffix=".tmp")
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(encoded)
            os.replace(temporary, self._directory / f"{name}.json")
        return document

    def delete(self, name: str) -> None:
        self._check(name)
        if self._directory is None:
            if self._memory.pop(name, None) is None:
                raise UnknownTree(name)
            return
        path = self._directory / f"{name}.json"
        if not path.is_file():
            raise UnknownTree(name)
        path.unlink()

    @staticmethod
    def _check(name: str) -> None:
        if not _NAME.fullmatch(name):
            raise ValueError("name must be 1-64 chars of lowercase letters, digits, - or _")
