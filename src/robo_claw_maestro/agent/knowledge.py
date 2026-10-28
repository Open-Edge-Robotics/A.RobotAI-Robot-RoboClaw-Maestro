"""Operator-editable Markdown documents that shape Maestro planning."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

MAX_DOCUMENT_BYTES = 64 * 1024

# Order is the order documents appear in the UI and in the planning prompt.
DOCUMENTS: dict[str, str] = {
    "role": "Maestro 역할",
    "skills": "Maestro 스킬",
    "mcp": "외부 MCP 설정",
    "troubleshooting": "Troubleshooting",
    "robots": "로봇 프로필",
}

_HEADING = re.compile(r"^##\s+(.+?)\s*$")
_FIELD = re.compile(r"^\s*[-*]\s*(robot_type|site_id|map_id|map_version|capabilities)\s*:\s*(.*)$")


class UnknownDocument(KeyError):
    pass


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    name: str
    title: str
    content: str
    is_default: bool
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class RobotProfile:
    """A robot described in robots.md, used as a virtual fleet member in dry-runs."""

    robot_id: str
    robot_type: str = "unknown"
    site_id: str = ""
    map_id: str = ""
    map_version: str = ""
    capabilities: tuple[str, ...] = ()
    description: str = ""


def _default_content(name: str) -> str:
    package = resources.files("robo_claw_maestro.agent") / "knowledge_defaults" / f"{name}.md"
    return package.read_text(encoding="utf-8")


def parse_robot_profiles(markdown: str) -> list[RobotProfile]:
    """Parse ``## robot_id`` sections with ``- key: value`` fields.

    Lines that are not recognised fields become the free-text description.
    """
    profiles: list[RobotProfile] = []
    current: dict[str, object] | None = None

    def flush() -> None:
        if current is None:
            return
        profiles.append(
            RobotProfile(
                robot_id=str(current["robot_id"]),
                robot_type=str(current.get("robot_type") or "unknown"),
                site_id=str(current.get("site_id", "")),
                map_id=str(current.get("map_id", "")),
                map_version=str(current.get("map_version", "")),
                capabilities=tuple(current.get("capabilities", ())),  # type: ignore[arg-type]
                description="\n".join(current["description"]).strip(),  # type: ignore[arg-type]
            )
        )

    in_code_block = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_code_block = not in_code_block
        heading = None if in_code_block else _HEADING.match(line)
        if heading:
            flush()
            robot_id = heading.group(1).strip().strip("`")
            current = {"robot_id": robot_id, "description": []} if robot_id else None
            continue
        if current is None:
            continue
        field = None if in_code_block else _FIELD.match(line)
        if field is None:
            current["description"].append(line)  # type: ignore[union-attr]
            continue
        key, value = field.group(1), field.group(2).strip()
        if key == "capabilities":
            names = [item.strip().strip("`") for item in value.split(",")]
            current[key] = tuple(name for name in names if name)
        else:
            current[key] = value.strip("`")
    flush()
    return profiles


class KnowledgeBase:
    """Stores documents as ``<directory>/<name>.md``; missing files fall back to defaults.

    With ``directory=None`` documents are kept in memory (tests and ephemeral runs).
    """

    def __init__(self, directory: str | Path | None) -> None:
        self._directory = Path(directory) if directory else None
        self._memory: dict[str, str] = {}
        if self._directory is not None:
            self._directory.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[KnowledgeDocument]:
        return [self.get(name) for name in DOCUMENTS]

    def get(self, name: str) -> KnowledgeDocument:
        self._check(name)
        stored, updated_at = self._read(name)
        return KnowledgeDocument(
            name=name,
            title=DOCUMENTS[name],
            content=stored if stored is not None else _default_content(name),
            is_default=stored is None,
            updated_at=updated_at,
        )

    def save(self, name: str, content: str) -> KnowledgeDocument:
        self._check(name)
        if len(content.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise ValueError(f"document exceeds {MAX_DOCUMENT_BYTES} bytes")
        if self._directory is None:
            self._memory[name] = content
        else:
            target = self._path(name)
            handle, temporary = tempfile.mkstemp(dir=self._directory, suffix=".tmp")
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(content)
            os.replace(temporary, target)
        return self.get(name)

    def reset(self, name: str) -> KnowledgeDocument:
        self._check(name)
        if self._directory is None:
            self._memory.pop(name, None)
        else:
            self._path(name).unlink(missing_ok=True)
        return self.get(name)

    def planning_guidance(self) -> str:
        sections = [
            f"## {document.title}\n\n{document.content.strip()}" for document in self.list()
        ]
        return "\n\n".join(sections)

    def robot_profiles(self) -> list[RobotProfile]:
        return parse_robot_profiles(self.get("robots").content)

    def _read(self, name: str) -> tuple[str | None, datetime | None]:
        if self._directory is None:
            return self._memory.get(name), None
        path = self._path(name)
        if not path.is_file():
            return None, None
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        return path.read_text(encoding="utf-8"), modified

    def _path(self, name: str) -> Path:
        assert self._directory is not None
        return self._directory / f"{name}.md"

    @staticmethod
    def _check(name: str) -> None:
        if name not in DOCUMENTS:
            raise UnknownDocument(name)
