import asyncio

import pytest

from robo_claw_maestro.agent.knowledge import (
    DOCUMENTS,
    KnowledgeBase,
    UnknownDocument,
    parse_robot_profiles,
)
from robo_claw_maestro.agent.maestro_agent import LlmProviderError, MaestroAgent
from robo_claw_maestro.agent.planner import MissionPlanParser
from robo_claw_maestro.agent.provider import FakeLlmProvider

pytestmark = pytest.mark.unit

PROFILES = """# 로봇 프로필

intro text is not a robot

## cloid01

- robot_type: cloid
- map_id: floor-1
- capabilities: `get_status`, navigate_to, tidy_home

거실 담당.

## cloid02
- capabilities: get_status, patrol

```
## not-a-robot
```
"""

PLAN = """{"summary": "tidy", "tasks": [
  {"task_id": "check", "instruction": "check", "required_capabilities": ["get_status"]},
  {"task_id": "tidy", "instruction": "tidy", "required_capabilities": ["tidy_home"],
   "depends_on": ["check"]},
  {"task_id": "patrol", "instruction": "patrol", "required_capabilities": ["patrol"],
   "preferred_robot_id": "cloid01"}
]}"""


def test_robot_profiles_are_parsed_from_markdown_sections():
    profiles = parse_robot_profiles(PROFILES)

    assert [profile.robot_id for profile in profiles] == ["cloid01", "cloid02"]
    assert profiles[0].capabilities == ("get_status", "navigate_to", "tidy_home")
    assert profiles[0].robot_type == "cloid"
    assert profiles[0].map_id == "floor-1"
    assert profiles[0].description == "거실 담당."
    assert profiles[1].capabilities == ("get_status", "patrol")


def test_documents_fall_back_to_defaults_and_persist_to_files(tmp_path):
    knowledge = KnowledgeBase(tmp_path)

    assert [document.name for document in knowledge.list()] == list(DOCUMENTS)
    assert all(document.is_default and document.content for document in knowledge.list())

    saved = knowledge.save("role", "custom role")
    assert not saved.is_default
    assert (tmp_path / "role.md").read_text(encoding="utf-8") == "custom role"
    assert "custom role" in KnowledgeBase(tmp_path).planning_guidance()

    assert knowledge.reset("role").is_default
    assert not (tmp_path / "role.md").exists()
    with pytest.raises(UnknownDocument):
        knowledge.get("../etc/passwd")
    with pytest.raises(ValueError):
        knowledge.save("role", "x" * (64 * 1024 + 1))


def test_dry_run_plans_against_profiles_without_live_robots():
    knowledge = KnowledgeBase(None)
    knowledge.save("robots", PROFILES)
    provider = FakeLlmProvider([PLAN])
    agent = MaestroAgent(
        provider, MissionPlanParser(set()), fleet_catalog=lambda: [], knowledge=knowledge
    )

    result = asyncio.run(agent.dry_run("집 정리"))

    assert result.valid, result.error
    assert result.profile_robot_ids == ["cloid01", "cloid02"]
    by_id = {task["task_id"]: task for task in result.tasks}
    assert [item["robot_id"] for item in by_id["check"]["candidates"]] == ["cloid01", "cloid02"]
    assert by_id["tidy"]["stage"] == 2
    assert any("cloid01" in warning for warning in by_id["patrol"]["warnings"])
    assert result.assignable
    system_prompt, user_prompt, _ = provider.requests[0]
    assert "거실 담당." in system_prompt
    assert '"connection_state": "PROFILE"' in user_prompt
    assert agent.metrics.requests == 0


def test_dry_run_reports_validation_errors_instead_of_raising():
    agent = MaestroAgent(FakeLlmProvider([PLAN]), MissionPlanParser(set()), knowledge=None)

    result = asyncio.run(agent.dry_run("집 정리", include_profiles=False))

    assert not result.valid
    assert "unknown capabilities" in result.error
    assert result.raw_response == PLAN


def test_provider_failures_are_wrapped():
    class BrokenProvider:
        async def complete(self, system_prompt, user_prompt, timeout_sec):
            raise OSError("Name or service not known")

    agent = MaestroAgent(BrokenProvider(), MissionPlanParser(set()))

    with pytest.raises(LlmProviderError, match="Name or service not known"):
        asyncio.run(agent.create_plan("inspect", created_by="operator"))
    assert agent.metrics.provider_errors == 1
