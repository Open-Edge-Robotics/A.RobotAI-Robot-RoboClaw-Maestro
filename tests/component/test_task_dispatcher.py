import asyncio

import pytest

from robo_claw_maestro.application.robot_registry import RobotRegistry
from robo_claw_maestro.application.task_dispatcher import TaskDispatcher, TaskDispatchError
from robo_claw_maestro.domain.robot import Capability, Robot, RobotConnectionState
from robo_claw_maestro.domain.task import Task, TaskStatus
from robo_claw_maestro.ports.robot_gateway import FakeRobotGateway, RobotStatusSnapshot

pytestmark = pytest.mark.component


def make_dispatcher(*, fail=False):
    gateway = FakeRobotGateway([RobotStatusSnapshot(robot_id="robot-a")])
    if fail:
        gateway.fail_commands_for.add("robot-a")
    registry = RobotRegistry(gateway)
    robot = Robot("robot-a", "Alpha", connection_state=RobotConnectionState.ONLINE)
    robot.add_capability(Capability("navigate_to"))
    registry.register(robot)
    return TaskDispatcher(registry, gateway), gateway, registry


def make_task():
    return Task(
        task_id="task-1",
        mission_id="mission-1",
        instruction="go to room",
        required_capabilities={"navigate_to"},
    )


def test_dispatch_success_maps_remote_result():
    dispatcher, gateway, registry = make_dispatcher()
    task = make_task()
    task.transition_to(TaskStatus.READY)

    result = asyncio.run(dispatcher.dispatch(task, "robot-a"))

    assert task.status is TaskStatus.SUCCEEDED
    assert result.remote_result is not None and result.remote_result.success
    assert result.command.idempotency_key == "mission-1:task-1:robot-a:1"
    assert registry.get("robot-a").current_task_id is None
    assert gateway.executed_tasks[0]["task_id"] == "task-1"
    assert gateway.executed_tasks[0]["idempotency_key"] == result.command.idempotency_key
    assert gateway.executed_tasks[0]["attempt_id"] == "task-1-attempt-1"


def test_dispatch_remote_failure_is_not_ambiguous():
    dispatcher, _, _ = make_dispatcher(fail=True)
    task = make_task()
    task.transition_to(TaskStatus.READY)

    result = asyncio.run(dispatcher.dispatch(task, "robot-a"))

    assert task.status is TaskStatus.FAILED
    assert result.ambiguous is False
    assert task.error_code == "FAKE_FAILURE"


def test_dispatch_rejects_ineligible_robot():
    dispatcher, _, registry = make_dispatcher()
    registry.get("robot-a").capabilities.clear()
    task = make_task()
    task.transition_to(TaskStatus.READY)

    with pytest.raises(TaskDispatchError, match="not eligible"):
        asyncio.run(dispatcher.dispatch(task, "robot-a"))


def test_dispatch_many_runs_independent_tasks():
    gateway = FakeRobotGateway(
        [
            RobotStatusSnapshot(robot_id="robot-a"),
            RobotStatusSnapshot(robot_id="robot-b"),
        ]
    )
    registry = RobotRegistry(gateway)
    alpha = Robot("robot-a", "Alpha", connection_state=RobotConnectionState.ONLINE)
    beta = Robot("robot-b", "Beta", connection_state=RobotConnectionState.ONLINE)
    alpha.add_capability(Capability("navigate_to"))
    beta.add_capability(Capability("navigate_to"))
    registry.register(alpha)
    registry.register(beta)
    dispatcher = TaskDispatcher(registry, gateway)
    first, second = make_task(), make_task()
    second.task_id = "task-2"
    first.transition_to(TaskStatus.READY)
    second.transition_to(TaskStatus.READY)

    results = asyncio.run(dispatcher.dispatch_many([(first, "robot-a"), (second, "robot-b")]))

    assert [item.task_id for item in results] == ["task-1", "task-2"]
    assert all(item.remote_result and item.remote_result.success for item in results)
