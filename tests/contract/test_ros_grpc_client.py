import asyncio
from types import SimpleNamespace

import pytest

from robo_claw_maestro.adapters.grpc.ros_grpc_client import RosGrpcRobotGateway
from robo_claw_maestro.domain.robot import RobotOperationalState

pytestmark = pytest.mark.contract


class FakeStub:
    async def Ping(self, request, timeout):
        assert request.message == "maestro-ping"
        assert timeout > 0
        return SimpleNamespace(message="pong")

    @staticmethod
    def _status():
        return SimpleNamespace(
            robot_id="robot-a",
            battery_percentage=77.5,
            current_task="NAVIGATING",
            current_pose=SimpleNamespace(position=SimpleNamespace(x=1.0, y=2.0, z=0.0)),
        )

    async def GetRobotInfo(self, request, timeout):
        return SimpleNamespace(id="robot-a", name="Alpha", type="FORMER")

    async def GetRobotStatus(self, request, timeout):
        return self._status()

    def GetRobotStatusStream(self, request, timeout):
        async def responses():
            yield self._status()
            yield self._status()

        return responses()

    async def GetRobotCameraImage(self, request, timeout):
        return SimpleNamespace(
            robot_id="robot-a", image="ZmFrZQ==", format="image/jpeg", timestamp=123
        )

    async def ExecuteTask(self, request, timeout):
        assert request.instruction == "go to room"
        assert timeout == 10
        return SimpleNamespace(success=True, result_message="done")

    async def ExecuteSkill(self, request, timeout):
        return SimpleNamespace(
            success=True,
            message="skill done",
            result_json='{"objects": 2}',
        )

    async def EmergencyStop(self, request, timeout):
        return SimpleNamespace(success=True, message="stopped")


class Messages:
    COMPRESSED = 1

    @staticmethod
    def PingRequest(**kwargs):
        return SimpleNamespace(**kwargs)

    @staticmethod
    def EmptyRequest(**kwargs):
        return SimpleNamespace(**kwargs)

    @staticmethod
    def ExecuteTaskRequest(**kwargs):
        return SimpleNamespace(**kwargs)

    @staticmethod
    def ExecuteSkillRequest(**kwargs):
        return SimpleNamespace(**kwargs)

    @staticmethod
    def CameraRequest(**kwargs):
        return SimpleNamespace(**kwargs)


def test_existing_ros_grpc_contract_is_adapted():
    gateway = RosGrpcRobotGateway(FakeStub(), Messages)

    assert asyncio.run(gateway.ping("robot-a"))
    info = asyncio.run(gateway.get_info("robot-a"))
    status = asyncio.run(gateway.get_status("robot-a"))

    async def collect_stream():
        return [item async for item in gateway.stream_status("robot-a")]

    streamed = asyncio.run(collect_stream())
    result = asyncio.run(
        gateway.execute_task("robot-a", instruction="go to room", task_id="task-1", timeout_sec=10)
    )
    skill = asyncio.run(
        gateway.execute_skill("robot-a", skill_name="analyze_scene", params={}, timeout_sec=10)
    )
    snapshot = asyncio.run(gateway.request_snapshot("robot-a"))
    stop = asyncio.run(gateway.emergency_stop("robot-a"))

    assert info.display_name == "Alpha"
    assert len(streamed) == 2
    assert status.operational_state is RobotOperationalState.EXECUTING
    assert status.pose["x"] == 1.0
    assert result.success
    assert skill.result == {"objects": 2}
    assert snapshot.image_base64 == "ZmFrZQ=="
    assert stop.success
