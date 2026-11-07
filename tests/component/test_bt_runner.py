import asyncio

import pytest

from robo_claw_maestro.agent.behavior_tree import split_by_robot, with_paths
from robo_claw_maestro.agent.maestro_agent import attach_behavior_tree
from robo_claw_maestro.application.bt_runner import MissionTreeRunner, TreeRunConflict
from robo_claw_maestro.application.mission_orchestrator import MissionOrchestrator
from robo_claw_maestro.application.robot_registry import RobotRegistry
from robo_claw_maestro.application.scheduler import FleetScheduler
from robo_claw_maestro.application.task_dispatcher import TaskDispatcher
from robo_claw_maestro.domain.mission import Mission, MissionStatus
from robo_claw_maestro.domain.robot import Capability, Robot, RobotConnectionState
from robo_claw_maestro.domain.task import Task, TaskStatus
from robo_claw_maestro.ports.robot_gateway import FakeRobotGateway, RobotStatusSnapshot

pytestmark = pytest.mark.component

SKILLS = ("get_status", "scan_room", "tidy_home", "navigate_to", "open_door")


async def _no_sleep(_seconds: float) -> None:
    await asyncio.sleep(0)


def make_fleet(*, battery: float | None = 80.0, offline: tuple[str, ...] = ()):
    gateway = FakeRobotGateway(
        [RobotStatusSnapshot(robot_id="cloid01"), RobotStatusSnapshot(robot_id="cloid02")]
    )
    registry = RobotRegistry(gateway)
    for robot_id in ("cloid01", "cloid02"):
        robot = Robot(
            robot_id,
            robot_id,
            connection_state=RobotConnectionState.OFFLINE
            if robot_id in offline
            else RobotConnectionState.ONLINE,
            battery_percentage=battery,
        )
        for skill in SKILLS:
            robot.add_capability(
                Capability(skill, risk_level="dangerous" if skill == "open_door" else "low")
            )
        registry.register(robot)
    orchestrator = MissionOrchestrator(FleetScheduler(registry), TaskDispatcher(registry, gateway))
    runner = MissionTreeRunner(orchestrator, registry, poll_sec=0.01, sleep=_no_sleep)
    return orchestrator, runner, gateway, registry


def catalog(registry, offline=()):
    return [
        {
            "robot_id": robot.robot_id,
            "connection_state": "OFFLINE" if robot.robot_id in offline else "ONLINE",
            "capabilities": sorted(robot.capabilities),
        }
        for robot in registry.list()
    ]


def skill_task(task_id, skill, robot=None, depends=(), **params):
    if robot:
        params["preferred_robot_id"] = robot
    return Task(
        task_id,
        "m1",
        f"{skill} {task_id}",
        task_type="skill",
        skill_name=skill,
        params=params,
        required_capabilities={skill},
        depends_on=set(depends),
    )


def tidy_mission(registry, *, repeat=None, policies=None, offline=()):
    mission = Mission("m1", "tidy the house")
    mission.add_tasks(
        [
            skill_task("status-1", "get_status", "cloid01"),
            skill_task("status-2", "get_status", "cloid02"),
            skill_task("scan-living", "scan_room", "cloid01", ["status-1"], room="거실"),
            skill_task("scan-bed", "scan_room", "cloid02", ["status-2"], room="침실"),
            skill_task("tidy-living", "tidy_home", "cloid01", ["scan-living"], area="거실"),
            skill_task("tidy-bed", "tidy_home", "cloid02", ["scan-bed"], area="침실"),
        ]
    )
    attach_behavior_tree(mission, catalog(registry, offline), repeat, policies or {})
    return mission


def run_tree(orchestrator, runner, mission_id="m1"):
    async def go():
        runner.start(mission_id)
        await runner.wait(mission_id)

    asyncio.run(go())
    return orchestrator.get(mission_id)


def test_plan_tree_is_split_per_robot_and_actions_go_out_as_skills():
    orchestrator, runner, gateway, registry = make_fleet()
    mission = tidy_mission(registry)
    orchestrator.create(mission)

    views = split_by_robot(mission.constraints["behavior_tree"])
    assert sorted(views) == ["cloid01", "cloid02"]

    finished = run_tree(orchestrator, runner)

    assert finished.status is MissionStatus.SUCCEEDED
    assert runner.snapshot("m1")["status"] == "SUCCEEDED"
    by_task = {item["task_id"]: item for item in gateway.executed_skills}
    assert by_task["tidy-living"]["robot_id"] == "cloid01"
    assert by_task["tidy-living"]["skill_name"] == "tidy_home"
    # Routing hints are not sent to the robot skill.
    assert by_task["tidy-living"]["params"] == {"area": "거실"}
    assert by_task["tidy-bed"]["robot_id"] == "cloid02"
    # Each robot's dependency chain keeps its order.
    order = [item["task_id"] for item in gateway.executed_skills]
    assert order.index("status-1") < order.index("scan-living") < order.index("tidy-living")
    assert order.index("status-2") < order.index("scan-bed") < order.index("tidy-bed")
    root = mission.constraints["behavior_tree"]["root"]
    assert root["children"][0]["name"] == "Independent chains"


def test_failure_aborts_only_its_own_chain_and_other_robots_finish():
    orchestrator, runner, gateway, registry = make_fleet()
    gateway.fail_commands_for.add("cloid02")
    orchestrator.create(tidy_mission(registry))

    finished = run_tree(orchestrator, runner)

    assert finished.status is MissionStatus.PARTIALLY_SUCCEEDED
    assert finished.tasks["status-2"].status is TaskStatus.FAILED
    assert finished.tasks["scan-bed"].status is TaskStatus.CANCELLED
    assert finished.tasks["tidy-bed"].status is TaskStatus.CANCELLED
    assert finished.tasks["tidy-living"].status is TaskStatus.SUCCEEDED
    assert runner.snapshot("m1")["status"] == "FAILED"


def test_dependent_chain_failure_with_nothing_done_fails_the_mission():
    orchestrator, runner, gateway, registry = make_fleet()
    gateway.fail_commands_for.update({"cloid01", "cloid02"})
    orchestrator.create(tidy_mission(registry))

    assert run_tree(orchestrator, runner).status is MissionStatus.FAILED


def test_battery_exempt_robot_may_move_with_broken_battery_reading():
    from robo_claw_maestro.domain.policies import SchedulingSafetyPolicy

    orchestrator, _, gateway, registry = make_fleet(battery=0.0)
    scheduler = FleetScheduler(
        registry, SchedulingSafetyPolicy(battery_exempt_robot_ids={"cloid01"})
    )
    orchestrator = MissionOrchestrator(scheduler, TaskDispatcher(registry, gateway))
    runner = MissionTreeRunner(orchestrator, registry, poll_sec=0.01, sleep=_no_sleep)
    mission = Mission("m1", "move both")
    mission.add_tasks(
        [
            skill_task("go-1", "navigate_to", "cloid01", target_name="거실"),
            skill_task("go-2", "navigate_to", "cloid02", target_name="안방"),
        ]
    )
    attach_behavior_tree(mission, catalog(registry), None, {})
    orchestrator.create(mission)

    finished = run_tree(orchestrator, runner)

    assert finished.tasks["go-1"].status is TaskStatus.SUCCEEDED
    assert finished.tasks["go-2"].status is not TaskStatus.SUCCEEDED
    assert [item["robot_id"] for item in gateway.executed_skills] == ["cloid01"]


def test_on_failure_continue_keeps_going_and_reports_partial_success():
    orchestrator, runner, gateway, registry = make_fleet()
    gateway.fail_commands_for.add("cloid02")
    policies = {"status-2": "continue"}
    mission = Mission("m1", "status then tidy")
    mission.add_tasks(
        [
            skill_task("status-2", "get_status", "cloid02"),
            skill_task("tidy-living", "tidy_home", "cloid01", ["status-2"]),
        ]
    )
    attach_behavior_tree(mission, catalog(registry), None, policies)
    orchestrator.create(mission)

    finished = run_tree(orchestrator, runner)

    assert finished.status is MissionStatus.PARTIALLY_SUCCEEDED
    assert finished.tasks["tidy-living"].status is TaskStatus.SUCCEEDED


def test_offline_planned_robot_is_replaced_by_a_safe_robot():
    orchestrator, runner, gateway, registry = make_fleet(offline=("cloid02",))
    mission = Mission("m1", "scan")
    mission.add_task(skill_task("scan-bed", "scan_room"))
    attach_behavior_tree(mission, catalog(registry), None, {})
    # Force the stored plan onto the offline robot.
    mission.constraints["behavior_tree"]["root"]["children"][0]["robot_id"] = "cloid02"
    orchestrator.create(mission)

    finished = run_tree(orchestrator, runner)

    assert finished.status is MissionStatus.SUCCEEDED
    assert gateway.executed_skills[0]["robot_id"] == "cloid01"
    node = runner.snapshot("m1")["nodes"]["root.0"]
    assert "cloid01로 대체" in node["message"]


def test_low_battery_blocks_motion_skill_without_fallback_for_preferred_robot():
    orchestrator, runner, gateway, registry = make_fleet(battery=5.0)
    mission = Mission("m1", "move")
    mission.add_task(skill_task("go", "navigate_to", "cloid01", target_name="주방"))
    attach_behavior_tree(mission, catalog(registry), None, {})
    orchestrator.create(mission)

    finished = run_tree(orchestrator, runner)

    assert finished.status is MissionStatus.FAILED
    assert gateway.executed_skills == []
    assert "battery_below_motion_threshold" in runner.snapshot("m1")["nodes"]["root.0"]["message"]


def test_dangerous_skill_is_never_sent_without_an_approval_service():
    orchestrator, runner, gateway, registry = make_fleet()
    mission = Mission("m1", "open")
    mission.add_task(skill_task("open", "open_door", "cloid01"))
    attach_behavior_tree(mission, catalog(registry), None, {})
    orchestrator.create(mission)

    finished = run_tree(orchestrator, runner)

    assert finished.status is MissionStatus.FAILED
    assert gateway.executed_skills == []
    assert "승인" in runner.snapshot("m1")["nodes"]["root.0"]["message"]


def test_dispatcher_refuses_dangerous_skill_unless_approved():
    _, _, gateway, registry = make_fleet()
    dispatcher = TaskDispatcher(registry, gateway)
    task = skill_task("open", "open_door", "cloid01")
    task.transition_to(TaskStatus.READY)

    asyncio.run(dispatcher.dispatch(task, "cloid01"))

    assert task.error_code == "SKILL_NOT_DISPATCHABLE"
    assert gateway.executed_skills == []


def _approval_fleet():
    from robo_claw_maestro.application.approval_service import ApprovalService

    orchestrator, _, gateway, registry = make_fleet()
    approvals = ApprovalService(registry)
    runner = MissionTreeRunner(
        orchestrator, registry, poll_sec=0.01, sleep=_no_sleep, approval_service=approvals
    )
    mission = Mission("m1", "boxing then move")
    mission.add_tasks(
        [
            skill_task("open", "open_door", "cloid01"),
            skill_task("go", "navigate_to", "cloid01", ["open"], target_name="거실"),
        ]
    )
    attach_behavior_tree(mission, catalog(registry), None, {})
    orchestrator.create(mission)
    return orchestrator, runner, gateway, approvals


def _decide(runner, approvals, decision):
    async def go():
        runner.start("m1")
        for _ in range(200):
            pending = approvals.list(pending_only=True)
            if pending:
                break
            await asyncio.sleep(0)
        assert pending, "the tree should wait for an approval"
        assert pending[0].payload["task_id"] == "open"
        node = runner.snapshot("m1")["nodes"]["root.0"]
        assert node["status"] == "WAITING_APPROVAL"
        if decision == "approve":
            await approvals.approve(pending[0].approval_id, "operator")
        else:
            approvals.reject(pending[0].approval_id, "operator")
        await runner.wait("m1")
        return pending[0].approval_id

    return asyncio.run(go())


def test_dangerous_action_waits_for_approval_then_runs():
    orchestrator, runner, gateway, approvals = _approval_fleet()

    approval_id = _decide(runner, approvals, "approve")

    assert orchestrator.get("m1").status is MissionStatus.SUCCEEDED
    assert [item["task_id"] for item in gateway.executed_skills] == ["open", "go"]
    assert approvals.get(approval_id).status.value == "EXECUTED"


def test_rejected_approval_fails_the_action_without_sending():
    orchestrator, runner, gateway, approvals = _approval_fleet()

    _decide(runner, approvals, "reject")

    assert orchestrator.get("m1").status is MissionStatus.FAILED
    assert gateway.executed_skills == []
    assert "REJECTED" in runner.snapshot("m1")["nodes"]["root.0"]["message"]


def test_repeat_runs_max_cycles_with_fresh_idempotency_keys():
    orchestrator, runner, gateway, registry = make_fleet()
    mission = Mission("m1", "patrol")
    mission.add_task(skill_task("status-1", "get_status", "cloid01"))
    attach_behavior_tree(mission, catalog(registry), {"interval_sec": 5, "max_cycles": 3}, {})
    orchestrator.create(mission)

    finished = run_tree(orchestrator, runner)

    assert finished.status is MissionStatus.SUCCEEDED
    keys = [item["idempotency_key"] for item in gateway.executed_skills]
    assert len(keys) == 3 and len(set(keys)) == 3
    assert runner.snapshot("m1")["cycle"] == 3


def test_cancel_while_running_stops_after_current_action():
    orchestrator, runner, gateway, registry = make_fleet()
    orchestrator.create(tidy_mission(registry))

    async def go():
        runner.start("m1")
        await asyncio.sleep(0)
        runner.cancel("m1")
        await runner.wait("m1")

    asyncio.run(go())
    finished = orchestrator.get("m1")

    assert finished.status is MissionStatus.CANCELLED
    assert runner.snapshot("m1")["status"] == "CANCELLED"
    assert all(task.is_terminal for task in finished.tasks.values())


def test_pause_holds_the_tree_until_resume():
    orchestrator, runner, gateway, registry = make_fleet()
    orchestrator.create(tidy_mission(registry))

    async def go():
        runner.start("m1")
        runner.pause("m1")
        for _ in range(20):
            await asyncio.sleep(0)
        executed_while_paused = len(gateway.executed_skills)
        runner.resume("m1")
        await runner.wait("m1")
        return executed_while_paused

    assert asyncio.run(go()) == 0
    assert orchestrator.get("m1").status is MissionStatus.SUCCEEDED


def test_second_start_while_running_conflicts():
    orchestrator, runner, gateway, registry = make_fleet()
    orchestrator.create(tidy_mission(registry))

    async def go():
        runner.start("m1")
        with pytest.raises(TreeRunConflict):
            runner.start("m1")
        await runner.wait("m1")

    asyncio.run(go())


def test_split_by_robot_keeps_paths_and_optional_fallbacks():
    tree = {
        "root": {
            "type": "Sequence",
            "name": "Root",
            "children": [
                {
                    "type": "Parallel",
                    "name": "Stage 1",
                    "success_count": 2,
                    "children": [
                        {"type": "Action", "name": "a", "robot_id": "r1", "skill": "s"},
                        {
                            "type": "Fallback",
                            "name": "b-optional",
                            "children": [
                                {"type": "Action", "name": "b", "robot_id": "r2", "skill": "s"},
                                {"type": "AlwaysSuccess", "name": "Skipped"},
                            ],
                        },
                    ],
                },
                {"type": "Action", "name": "c", "robot_id": "", "skill": "s"},
            ],
        }
    }
    annotated = {"root": with_paths(tree["root"])}
    views = split_by_robot(annotated)

    assert sorted(views) == ["(미배정)", "r1", "r2"]
    r2_stage = views["r2"]["children"][0]
    assert r2_stage["success_count"] == 2  # fleet-level requirement is kept
    assert r2_stage["children"][0]["children"][0]["path"] == "root.0.1.0"
    assert views["(미배정)"]["children"][0]["path"] == "root.1"


def test_rerun_failed_mission_resumes_at_the_failed_step_with_new_keys():
    orchestrator, runner, gateway, registry = make_fleet()
    gateway.fail_commands_for.add("cloid01")
    mission = Mission("m1", "status then tidy")
    mission.add_tasks(
        [
            skill_task("status-2", "get_status", "cloid02"),
            skill_task("tidy-living", "tidy_home", "cloid01", ["status-2"]),
            skill_task("tidy-after", "tidy_home", "cloid02", ["tidy-living"]),
        ]
    )
    attach_behavior_tree(mission, catalog(registry), None, {})
    orchestrator.create(mission)
    # status-2 succeeded, so the failed mission is partial.
    assert run_tree(orchestrator, runner).status is MissionStatus.PARTIALLY_SUCCEEDED
    failed_key = gateway.executed_skills[-1]["idempotency_key"]

    gateway.fail_commands_for.clear()  # operator fixed cloid01
    gateway.executed_skills.clear()

    async def go():
        runner.rerun("m1")
        await runner.wait("m1")

    asyncio.run(go())
    finished = orchestrator.get("m1")

    assert finished.status is MissionStatus.SUCCEEDED
    # The succeeded status check is skipped; the tree restarts at the failed step.
    assert [item["task_id"] for item in gateway.executed_skills] == ["tidy-living", "tidy-after"]
    assert gateway.executed_skills[0]["idempotency_key"] != failed_key
    assert gateway.executed_skills[0]["idempotency_key"].endswith(":r1")


def test_rerun_all_resets_every_task_and_rejects_unfinished_missions():
    orchestrator, runner, gateway, registry = make_fleet()
    orchestrator.create(tidy_mission(registry))
    with pytest.raises(TreeRunConflict):
        runner.rerun("m1")  # still DRAFT
    assert run_tree(orchestrator, runner).status is MissionStatus.SUCCEEDED
    with pytest.raises(TreeRunConflict):
        runner.rerun("m1")  # nothing failed
    gateway.executed_skills.clear()

    async def go():
        runner.rerun("m1", failed_only=False)
        await runner.wait("m1")

    asyncio.run(go())

    assert orchestrator.get("m1").status is MissionStatus.SUCCEEDED
    assert len(gateway.executed_skills) == 6


def _peer_report_fleet(monkeypatch):
    from robo_claw_maestro.application.approval_service import ApprovalService
    from robo_claw_maestro.ports.robot_gateway import RemoteTaskResult

    orchestrator, _, gateway, registry = make_fleet()
    approvals = ApprovalService(registry)
    runner = MissionTreeRunner(
        orchestrator, registry, poll_sec=0.01, sleep=_no_sleep, approval_service=approvals
    )
    original = gateway.execute_skill

    async def execute_skill(robot_id, **kwargs):
        result = await original(robot_id, **kwargs)
        if kwargs["skill_name"] == "tidy_home":
            # Robo-Claw under Maestro reports the peer work instead of calling the peer.
            return RemoteTaskResult(
                True,
                "done",
                {
                    "success": True,
                    "result": {
                        "status": "completed",
                        "peer_requests": [
                            {
                                "kind": "cleanup_demo",
                                "suggested_peer": "cloid01",
                                "place": "안방",
                                "target": "포장지",
                                "instruction": "[cleanup_demo v1] 안방 포장지",
                            }
                        ],
                    },
                },
            )
        return result

    monkeypatch.setattr(gateway, "execute_skill", execute_skill)
    mission = Mission("m1", "tidy master bedroom")
    mission.add_task(skill_task("tidy-bed", "tidy_home", "cloid02", area="안방"))
    attach_behavior_tree(mission, catalog(registry), None, {})
    orchestrator.create(mission)
    return orchestrator, runner, gateway, approvals


def test_reported_peer_work_runs_on_the_peer_through_maestro_after_approval(monkeypatch):
    orchestrator, runner, gateway, approvals = _peer_report_fleet(monkeypatch)

    async def go():
        runner.start("m1")
        for _ in range(500):
            pending = approvals.list(pending_only=True)
            if pending:
                break
            await asyncio.sleep(0)
        assert pending and pending[0].robot_id == "cloid01"
        assert "Former" not in pending[0].reason and "cleanup_demo" in pending[0].reason
        await approvals.approve(pending[0].approval_id, "operator")
        await runner.wait("m1")

    asyncio.run(go())
    finished = orchestrator.get("m1")

    followup = finished.tasks["tidy-bed-peer-1"]
    assert followup.status is TaskStatus.SUCCEEDED
    assert followup.assigned_robot_id == "cloid01"
    assert finished.status is MissionStatus.SUCCEEDED
    # The peer got a natural-language instruction from Maestro, not from cloid02.
    sent = [item for item in gateway.executed_tasks if item["task_id"] == "tidy-bed-peer-1"]
    assert sent[0]["robot_id"] == "cloid01"
    assert sent[0]["instruction"] == "[cleanup_demo v1] 안방 포장지"
    node = runner.snapshot("m1")["nodes"]["root.0"]
    assert node["followups"][0]["status"] == "SUCCESS"


def test_rejected_peer_work_leaves_the_source_success_and_the_mission_partial(monkeypatch):
    orchestrator, runner, gateway, approvals = _peer_report_fleet(monkeypatch)

    async def go():
        runner.start("m1")
        for _ in range(500):
            pending = approvals.list(pending_only=True)
            if pending:
                break
            await asyncio.sleep(0)
        approvals.reject(pending[0].approval_id, "operator")
        await runner.wait("m1")

    asyncio.run(go())
    finished = orchestrator.get("m1")

    assert finished.tasks["tidy-bed"].status is TaskStatus.SUCCEEDED
    assert finished.tasks["tidy-bed-peer-1"].status is TaskStatus.CANCELLED
    assert finished.status is MissionStatus.PARTIALLY_SUCCEEDED
    assert not any(item["task_id"] == "tidy-bed-peer-1" for item in gateway.executed_tasks)


def test_run_round_never_sends_a_task_that_requires_approval():
    _, _, gateway, registry = make_fleet()
    dispatcher = TaskDispatcher(registry, gateway)
    task = Task("peer", "m1", "do it", params={"requires_approval": True})
    task.transition_to(TaskStatus.READY)

    asyncio.run(dispatcher.dispatch(task, "cloid01"))

    assert task.error_code == "SKILL_NOT_DISPATCHABLE"
    assert gateway.executed_tasks == []
