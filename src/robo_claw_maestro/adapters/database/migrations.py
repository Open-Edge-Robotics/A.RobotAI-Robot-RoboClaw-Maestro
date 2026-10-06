"""Programmatic Alembic migration helper for local single-process startup."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

# Import records so all tables are registered in SQLModel.metadata.
from . import sqlmodel as _records  # noqa: F401


def upgrade_database(database_url: str) -> None:
    project_root = Path(__file__).resolve().parents[4]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

    engine = create_engine(database_url)
    tables = set(inspect(engine).get_table_names())
    if tables and "alembic_version" not in tables:
        # Recover databases created by an earlier development build that used
        # metadata.create_all without an Alembic revision table.
        SQLModel.metadata.create_all(engine)
        command.stamp(config, "head")
    else:
        command.upgrade(config, "head")
    engine.dispose()
