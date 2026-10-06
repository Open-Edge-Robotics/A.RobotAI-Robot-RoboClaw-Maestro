"""Real gRPC fake Robo-Claw servers for integration and manual testing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import grpc

from robo_claw_maestro.adapters.grpc.generated import robo_pb2, robo_pb2_grpc


class FakeRosGrpcServicer(robo_pb2_grpc.RosGrpcServicer):
    def __init__(
        self,
        robot_id: str,
        *,
        battery_percentage: float = 80.0,
        task_delay_sec: float = 0.01,
    ) -> None:
        self.robot_id = robot_id
        self.battery_percentage = battery_percentage
        self.task_delay_sec = task_delay_sec
        self.current_task = "IDLE"
        self.estopped = False
        self.executed_tasks: list[str] = []
        self.executed_skills: list[str] = []

    async def Ping(self, request, context):
        return robo_pb2.Pong(message=f"pong:{self.robot_id}")

    async def GetRobotInfo(self, request, context):
        return robo_pb2.Robot(
            id=self.robot_id,
            name=self.robot_id,
            description="Maestro integration fake robot",
            type=robo_pb2.TEST,
        )

    def _status(self):
        return robo_pb2.RobotStatus(
            robot_id=self.robot_id,
            battery_percentage=self.battery_percentage,
            current_task=self.current_task,
            current_pose=robo_pb2.RobotPose(
                robot_id=self.robot_id,
                position=robo_pb2.Point(x=0.0, y=0.0, z=0.0),
                orientation=robo_pb2.Quaternion(w=1.0),
            ),
        )

    async def GetRobotStatus(self, request, context):
        return self._status()

    async def GetRobotStatusStream(self, request, context):
        while not context.done():
            yield self._status()
            await asyncio.sleep(0.1)

    async def GetRobotCameraImage(self, request, context):
        return robo_pb2.RobotCameraImage(
            robot_id=self.robot_id,
            image="R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
            timestamp=1,
            format="image/gif",
        )

    async def ExecuteTask(self, request, context):
        if self.estopped:
            return robo_pb2.ExecuteTaskResponse(
                success=False, result_message="robot is emergency-stopped"
            )
        instruction = request.instruction.strip()
        if not instruction:
            return robo_pb2.ExecuteTaskResponse(
                success=False, result_message="instruction is empty"
            )
        self.current_task = instruction
        self.executed_tasks.append(instruction)
        await asyncio.sleep(self.task_delay_sec)
        self.current_task = "IDLE"
        return robo_pb2.ExecuteTaskResponse(success=True, result_message="fake task completed")

    async def ExecuteSkill(self, request, context):
        if self.estopped:
            return robo_pb2.ExecuteSkillResponse(
                success=False,
                message="robot is emergency-stopped",
                skill_name=request.skill_name,
            )
        self.executed_skills.append(request.skill_name)
        return robo_pb2.ExecuteSkillResponse(
            success=True,
            message="fake skill completed",
            skill_name=request.skill_name,
            result_json='{"fake":true}',
        )

    async def EmergencyStop(self, request, context):
        self.estopped = True
        self.current_task = "ERROR"
        return robo_pb2.ActionResult(success=True, message="fake emergency stop accepted")


@dataclass(slots=True)
class RunningFakeRobot:
    robot_id: str
    endpoint: str
    server: grpc.aio.Server
    servicer: FakeRosGrpcServicer

    async def stop(self) -> None:
        await self.server.stop(grace=0)


async def start_fake_robot(
    robot_id: str,
    port: int,
    *,
    host: str = "127.0.0.1",
    battery_percentage: float = 80.0,
) -> RunningFakeRobot:
    server = grpc.aio.server()
    servicer = FakeRosGrpcServicer(robot_id, battery_percentage=battery_percentage)
    robo_pb2_grpc.add_RosGrpcServicer_to_server(servicer, server)
    bind_address = f"{host}:{port}"
    bound_port = server.add_insecure_port(bind_address)
    if bound_port == 0:
        raise RuntimeError(f"failed to bind fake robot endpoint: {bind_address}")
    await server.start()
    return RunningFakeRobot(robot_id, f"{host}:{bound_port}", server, servicer)
