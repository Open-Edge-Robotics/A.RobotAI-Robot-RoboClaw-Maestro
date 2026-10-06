import pytest

from robo_claw_maestro.domain.mission import (
    InvalidMissionPlan,
    InvalidMissionTransition,
    Mission,
    MissionStatus,
)
from robo_claw_maestro.domain.task import Task, TaskStatus

pytestmark = pytest.mark.unit


def task(task_id, depends_on=None, **kwargs):
    return Task(
        task_id=task_id,
        mission_id="mission-1",
        instruction=f"do {task_id}",
        depends_on=set(depends_on or ()),
        **kwargs,
    )


def test_dag_returns_only_tasks_with_satisfied_dependencies():
    mission = Mission("mission-1", "inspect two rooms")
    first = task("first")
    second = task("second", {"first"})
    mission.add_tasks([first, second])

    assert [item.task_id for item in mission.ready_tasks()] == ["first"]
    first.transition_to(TaskStatus.READY)
    first.assign("robot-a")
    first.start_attempt()
    first.transition_to(TaskStatus.SUCCEEDED)
    assert [item.task_id for item in mission.ready_tasks()] == ["second"]


def test_missing_dependency_is_rejected():
    mission = Mission("mission-1", "inspect")
    with pytest.raises(InvalidMissionPlan, match="missing"):
        mission.add_task(task("second", {"missing"}))


def test_dependency_cycle_is_rejected():
    mission = Mission("mission-1", "inspect")
    with pytest.raises(InvalidMissionPlan, match="cycle"):
        mission.add_tasks([task("a", {"b"}), task("b", {"a"})])


def test_mission_refreshes_partial_success():
    mission = Mission("mission-1", "inspect")
    mission.transition_to(MissionStatus.READY)
    mission.transition_to(MissionStatus.RUNNING)
    first = task("first")
    second = task("second")
    mission.add_tasks([first, second])
    for item in (first, second):
        item.transition_to(TaskStatus.READY)
        item.assign("robot-a")
        item.start_attempt()
    first.transition_to(TaskStatus.SUCCEEDED)
    second.transition_to(TaskStatus.FAILED)
    assert mission.refresh_status() is MissionStatus.PARTIALLY_SUCCEEDED


def test_mission_transition_creates_audit_event_and_timestamps():
    mission = Mission("mission-1", "inspect")

    mission.transition_to(MissionStatus.READY)
    mission.transition_to(MissionStatus.RUNNING)
    mission.transition_to(MissionStatus.SUCCEEDED)

    events = mission.pull_events()
    assert [(event.payload["from"], event.payload["to"]) for event in events] == [
        ("DRAFT", "READY"),
        ("READY", "RUNNING"),
        ("RUNNING", "SUCCEEDED"),
    ]
    assert mission.started_at is not None
    assert mission.finished_at is not None


def test_invalid_mission_transition_is_rejected():
    mission = Mission("mission-1", "inspect")

    with pytest.raises(InvalidMissionTransition):
        mission.transition_to(MissionStatus.SUCCEEDED)


def test_task_from_another_mission_is_rejected():
    mission = Mission("mission-1", "inspect")
    with pytest.raises(InvalidMissionPlan, match="belongs"):
        mission.add_task(
            Task(task_id="task-1", mission_id="mission-2", instruction="wrong mission")
        )
