"""#16/#37: the alembic migration graph must stay single-head with a valid chain.

A forked head (two migrations whose ``down_revision`` is None, or two migrations
off the same parent that never got rebased linear) makes ``alembic upgrade head``
ambiguous and the deploy pick one branch silently. A dangling ``down_revision``
(a typo, or a rename that dropped the referenced id) breaks ``upgrade head`` with
a confusing "Can't locate revision identified by '...'" mid-deploy. Neither is
caught by the test suite (the suite builds its DB via ``Base.metadata.create_all``,
not ``alembic upgrade head``), so this guard walks the migration files statically.

The revision-id length limit (alembic's ``alembic_version.version_num`` is
``VARCHAR(32)``) is covered by ``test_enum_migration_parity``; this test covers
the *graph*: exactly one head, every edge resolves, every revision is reachable
from a root, and no revision id is duplicated.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict

_ROOT = Path(__file__).resolve().parents[2]
_VERSIONS = _ROOT / "alembic" / "versions"

_REVISION_RE = re.compile(r'^revision\s*=\s*"([^"]+)"', re.M)
_DOWN_REVISION_RE = re.compile(r'^down_revision\s*=\s*(?:"([^"]+)"|None)', re.M)


class _Migration(TypedDict):
    file: str
    down: str | None


def _load_migrations() -> dict[str, _Migration]:
    """Map revision_id -> {file, down_revision} for every migration file."""
    out: dict[str, _Migration] = {}
    for path in sorted(_VERSIONS.glob("*.py")):
        text = path.read_text()
        rev = _REVISION_RE.search(text)
        if rev is None:
            continue  # non-migration helper module
        down = _DOWN_REVISION_RE.search(text)
        down_id = down.group(1) if down and down.group(1) is not None else None
        out[rev.group(1)] = {"file": path.name, "down": down_id}
    return out


def test_migration_graph_has_exactly_one_head() -> None:
    """Exactly one revision must be unreferenced (no other migration points at it)."""
    migrations = _load_migrations()
    referenced = {m["down"] for m in migrations.values() if m["down"] is not None}
    heads = sorted(set(migrations) - referenced)
    assert len(heads) == 1, (
        f"expected exactly one alembic head, found {len(heads)}: {heads}. "
        "A second head means a forked migration chain — rebase the newer "
        "migration onto the current head so the chain stays linear."
    )


def test_every_down_revision_resolves_to_an_existing_migration() -> None:
    """No dangling ``down_revision`` — every edge points at a real revision id."""
    migrations = _load_migrations()
    ids = set(migrations)
    dangling = {
        rev: m["down"]
        for rev, m in migrations.items()
        if m["down"] is not None and m["down"] not in ids
    }
    assert not dangling, (
        "migrations with a down_revision that does not match any revision id "
        f"(rename/typo hazard): {dangling}"
    )


def test_every_revision_is_reachable_from_a_root() -> None:
    """Walking ``down_revision`` from every revision must reach a root (down=None)
    — a cycle or an orphaned island would otherwise strand part of the chain."""
    migrations = _load_migrations()
    for rev in migrations:
        cursor: str | None = rev
        seen: set[str] = set()
        while cursor is not None:
            if cursor in seen:
                raise AssertionError(
                    f"cycle in migration chain at {cursor} (from {rev})"
                )
            seen.add(cursor)
            node = migrations.get(cursor)
            if node is None:
                raise AssertionError(
                    f"revision {rev} walks into unknown id {cursor} (from {rev})"
                )
            cursor = node["down"]


def test_no_duplicate_revision_id() -> None:
    """Two files declaring the same revision id would shadow one another."""
    migrations = _load_migrations()
    seen: dict[str, str] = {}
    dups: list[str] = []
    for rev, m in migrations.items():
        if rev in seen:
            dups.append(f"{rev}: {seen[rev]} and {m['file']}")
        seen[rev] = m["file"]
    assert not dups, f"duplicate migration revision ids: {dups}"
