"""Guard: every ORM enum value must be produced by the migration chain.

StrEnum values get added to the ORM (roboco/models/base.py) freely, but a value
with no corresponding `ALTER TYPE ... ADD VALUE` migration breaks at runtime on
any DB whose enum type predates it — `invalid input value for enum
notificationtype: "a2a_request"`. Alembic autogenerate does NOT detect added
enum labels, so nothing else catches this. This test renders the full chain
offline (no DB) and fails if any ORM enum value is missing from it.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

from roboco.db.tables import Base  # registers every ORM enum

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _orm_enum_values() -> dict[str, set[str]]:
    orm: dict[str, set[str]] = {}
    for table in Base.metadata.tables.values():
        for col in table.columns:
            name = getattr(col.type, "name", None)
            labels = getattr(col.type, "enums", None)
            if name and labels:
                orm.setdefault(name, set()).update(labels)
    return orm


def _migration_chain_enum_labels() -> dict[str, set[str]]:
    rendered = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert rendered.returncode == 0, (
        "alembic offline render failed:\n" + rendered.stderr[-2000:]
    )
    sql = rendered.stdout
    chain: dict[str, set[str]] = {}
    for m in re.finditer(r"CREATE TYPE (\w+) AS ENUM \(([^)]*)\)", sql, re.S):
        chain.setdefault(m.group(1), set()).update(re.findall(r"'([^']+)'", m.group(2)))
    for m in re.finditer(
        r"ALTER TYPE (\w+) ADD VALUE (?:IF NOT EXISTS )?'([^']+)'", sql
    ):
        chain.setdefault(m.group(1), set()).add(m.group(2))
    return chain


def test_every_orm_enum_value_is_created_by_the_migration_chain() -> None:
    orm = _orm_enum_values()
    chain = _migration_chain_enum_labels()
    drift = {
        name: sorted(orm[name] - chain.get(name, set()))
        for name in orm
        if orm[name] - chain.get(name, set())
    }
    assert not drift, (
        "ORM enum values the migration chain never creates (add an "
        "`ALTER TYPE <enum> ADD VALUE IF NOT EXISTS '<value>'` migration — "
        "autogenerate does NOT detect added enum labels):\n"
        + "\n".join(f"  {name}: {vals}" for name, vals in sorted(drift.items()))
    )
