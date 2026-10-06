import pytest
from fastapi.testclient import TestClient

from robo_claw_maestro.testing import build_fake_app

pytestmark = [pytest.mark.e2e]


def test_three_robot_acceptance_flow():
    client = TestClient(build_fake_app(3))

    synchronized = client.post("/api/v1/robots/synchronize")
    assert synchronized.status_code == 200
    assert len(synchronized.json()) == 3
    assert {item["connection_state"] for item in synchronized.json()} == {"ONLINE"}

    created = client.post(
        "/api/v1/missions",
        json={
            "mission_id": "acceptance-1",
            "goal": "inspect two zones",
            "tasks": [
                {
                    "task_id": "inspect-a",
                    "instruction": "inspect A",
                    "required_capabilities": ["inspect"],
                },
                {
                    "task_id": "inspect-b",
                    "instruction": "inspect B",
                    "required_capabilities": ["inspect"],
                },
            ],
        },
    )
    assert created.status_code == 200

    run = client.post("/api/v1/missions/acceptance-1/run")
    assert run.status_code == 200
    body = run.json()
    assert body["mission"]["status"] == "SUCCEEDED"
    assert len({item["robot_id"] for item in body["assignments"]}) == 2

    stopped = client.post("/api/v1/fleet/emergency-stop")
    assert stopped.status_code == 200
    assert stopped.json()["requested"] == 3
    assert len(stopped.json()["stopped"]) == 3
