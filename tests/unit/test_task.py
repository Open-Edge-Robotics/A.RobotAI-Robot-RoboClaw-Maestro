import pytest

from robo_claw_maestro.domain.task import InvalidTaskTransition, Task, TaskStatus

pytestmark = pytest.mark.unit


def make_task(**kwargs):
    defaults = {"task_id": "task-1", "mission_id": "mission-1", "instruction": "inspect room"}
    defaults.update(kwargs)
    return Task(**defaults)


def test_task_lifecycle_reaches_success():
    task = make_task()
    task.transition_to(TaskStatus.READY)
    task.assign("robot-a")
    task.start_attempt()
    assert task.status is TaskStatus.RUNNING
    task.update_progress(50)
    task.transition_to(TaskStatus.SUCCEEDED)
    assert task.progress == 100
    assert task.is_terminal


def test_task_transitions_create_structured_events():
    task = make_task()

    task.transition_to(TaskStatus.READY)
    task.assign("robot-a")

    events = task.pull_events()
    assert [event.event_type for event in events] == [
        "task.status_changed",
        "task.status_changed",
    ]
    assert events[-1].payload == {
        "mission_id": "mission-1",
        "from": "READY",
        "to": "ASSIGNED",
    }


def test_invalid_transition_is_rejected():
    task = make_task()
    with pytest.raises(InvalidTaskTransition):
        task.transition_to(TaskStatus.RUNNING)


def test_retry_resets_assignment_and_progress():
    task = make_task(max_attempts=2)
    task.transition_to(TaskStatus.READY)
    task.assign("robot-a")
    task.start_attempt()
    task.transition_to(TaskStatus.FAILED)
    assert task.can_retry()
    task.prepare_retry()
    assert task.status is TaskStatus.READY
    assert task.assigned_robot_id is None
    assert task.attempt_count == 1
    assert task.progress == 0


def test_task_rejects_self_dependency():
    with pytest.raises(ValueError, match="itself"):
        make_task(depends_on={"task-1"})
