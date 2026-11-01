import asyncio

import pytest

from robo_claw_maestro.agent.behavior_tree import (
    BehaviorTreeStore,
    UnknownTree,
    plan_to_tree,
    validate_tree,
)
from robo_claw_maestro.agent.knowledge import KnowledgeBase
from robo_claw_maestro.agent.maestro_agent import MaestroAgent
from robo_claw_maestro.agent.planner import MissionPlanParser, PlanValidationError
from robo_claw_maestro.agent.provider import FakeLlmProvider

pytestmark = pytest.mark.unit

PROFILES = """## r1
- capabilities: get_status, navigate_to, scan_room, tidy_home

## r2
- capabilities: get_status, navigate_to, scan_room
"""

PLAN = """{"summary": "tidy", "repeat": {"interval_sec": 3600, "max_cycles": 0}, "tasks": [
  {"task_id": "status-a", "instruction": "a", "skill_name": "get_status"},
  {"task_id": "status-b", "instruction": "b", "skill_name": "get_status"},
  {"task_id": "go", "instruction": "go", "skill_name": "navigate_to",
   "params": {"target_name": "거실"}, "depends_on": ["status-a", "status-b"]},
  {"task_id": "scan", "instruction": "scan", "skill_name": "scan_room",
   "depends_on": ["go"], "on_failure": "continue"},
  {"task_id": "tidy", "instruction": "tidy", "skill_name": "tidy_home",
   "params": {"scope": "room"}, "depends_on": ["scan"]}
]}"""


def single_skill_parser():
    return MissionPlanParser({"get_status", "inspect"}, single_skill=True)


def test_single_skill_mode_requires_exactly_one_skill_per_task():
    ok = '{"tasks":[{"task_id":"t","instruction":"x","skill_name":"get_status"}]}'
    task = single_skill_parser().parse(ok, goal="g", created_by="o").tasks["t"]
    assert task.required_capabilities == {"get_status"}
    assert task.task_type == "skill"

    missing = '{"tasks":[{"task_id":"t","instruction":"x","required_capabilities":["inspect"]}]}'
    with pytest.raises(PlanValidationError, match="exactly one skill_name"):
        single_skill_parser().parse(missing, goal="g", created_by="o")
    extra = (
        '{"tasks":[{"task_id":"t","instruction":"x","skill_name":"get_status",'
        '"required_capabilities":["get_status","inspect"]}]}'
    )
    with pytest.raises(PlanValidationError, match="only its skill_name"):
        single_skill_parser().parse(extra, goal="g", created_by="o")


def test_repeat_metadata_is_validated():
    parser = single_skill_parser()
    assert parser.metadata(PLAN)[0] == {"interval_sec": 3600.0, "max_cycles": 0}
    assert parser.metadata(PLAN)[1]["scan"] == "continue"
    with pytest.raises(PlanValidationError, match="interval_sec"):
        parser.metadata('{"repeat": {"interval_sec": 0}, "tasks": []}')


def dry_run():
    knowledge = KnowledgeBase(None)
    knowledge.save("robots", PROFILES)
    agent = MaestroAgent(
        FakeLlmProvider([PLAN]),
        MissionPlanParser(set(), single_skill=True),
        knowledge=knowledge,
    )
    return asyncio.run(agent.dry_run("주기적으로 정리"))


def test_dry_run_balances_parallel_tasks_and_keeps_bt_fields():
    result = dry_run()

    assert result.valid, result.error
    assert result.repeat == {"interval_sec": 3600.0, "max_cycles": 0}
    by_id = {task["task_id"]: task for task in result.tasks}
    assert {by_id["status-a"]["assigned_robot_id"], by_id["status-b"]["assigned_robot_id"]} == {
        "r1",
        "r2",
    }
    # The chain go -> scan -> tidy stays on one robot instead of hopping between robots.
    assert by_id["go"]["assigned_robot_id"] == by_id["status-a"]["assigned_robot_id"]
    assert by_id["scan"]["assigned_robot_id"] == by_id["go"]["assigned_robot_id"]
    assert by_id["tidy"]["assigned_robot_id"] == "r1"
    assert by_id["go"]["params"] == {"target_name": "거실"}


def test_plan_converts_to_robo_claw_style_tree_and_validates():
    result = dry_run()
    tree = plan_to_tree(name="tidy", goal="g", tasks=result.tasks, repeat=result.repeat)

    root = tree["root"]
    assert tree["loop"]["enabled"] is True
    assert [child["type"] for child in root["children"]] == [
        "Condition",
        "Condition",
        "Fallback",
        "SleepBetweenCycles",
    ]
    stages = root["children"][2]["children"][0]["children"]
    assert stages[0]["type"] == "Parallel" and stages[0]["success_count"] == 2
    assert stages[2]["type"] == "Fallback"  # on_failure=continue
    skills = {
        "r1": {"get_status", "navigate_to", "scan_room", "tidy_home"},
        "r2": {"get_status", "navigate_to", "scan_room"},
    }
    assert validate_tree(tree, skills) == []

    stages[3]["robot_id"] = "r2"
    issues = validate_tree(tree, skills)
    assert any("tidy_home" in issue.message for issue in issues)


def test_validation_catches_structural_errors():
    tree = {
        "loop": {"enabled": True, "interval_sec": 0},
        "root": {
            "type": "Sequence",
            "children": [
                {"type": "Parallel", "success_count": 3, "children": [{"type": "AlwaysSuccess"}]},
                {"type": "Action", "robot_id": "ghost", "skill": "x"},
                {"type": "Teleport"},
                {"type": "Fallback", "children": []},
            ],
        },
    }
    messages = " ".join(issue.message for issue in validate_tree(tree, {}))

    for expected in ["interval_sec", "success_count", "ghost", "Teleport", "자식 노드가 필요"]:
        assert expected in messages


def test_store_round_trip_and_name_rules(tmp_path):
    store = BehaviorTreeStore(tmp_path)
    store.save("daily-tidy", {"root": {"type": "AlwaysSuccess"}})

    assert store.list()[0]["name"] == "daily-tidy"
    assert store.get("daily-tidy")["name"] == "daily-tidy"
    with pytest.raises(ValueError):
        store.save("../escape", {})
    store.delete("daily-tidy")
    with pytest.raises(UnknownTree):
        store.get("daily-tidy")
