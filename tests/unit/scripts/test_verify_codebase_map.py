"""scripts/verify_codebase_map.py: the docs/map reference checker.

docs/map is auto-indexed into the knowledge base, so a row naming a deleted
file or a renamed symbol misleads every agent that retrieves it, and a module
no row names is invisible to them. These tests pin what the checker treats
as an error (a path or symbol that does not exist, a module or migration no
table maps: exit 1), what it only warns about (line and size drift: exit 0,
so an ordinary code edit never fails CI), what ``--fix`` may rewrite, and
that ``make quality`` runs it.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

ROOT = Path(__file__).parents[3]
SCRIPT = ROOT / "scripts" / "verify_codebase_map.py"
MAKEFILE = ROOT / "Makefile"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_codebase_map", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve string annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


vcm = _load_module()

SERVICE = '''\
"""A service."""

import functools


class Widget:
    """A widget."""

    def __init__(self) -> None:
        self.count = 0

    @functools.cache
    def spin(self) -> int:
        return self.count


def build_widget() -> Widget:
    return Widget()


LIMIT = 3
'''

SETTINGS = """\
class Settings:
    widget_enabled: bool = False
"""

PANEL_HOOK = """\
import { useState } from "react";

export function useWidget() {
  return useState(0);
}
"""


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _slice(root: Path, body: str) -> Path:
    return _write(root, "docs/map/widgets.md", textwrap.dedent(body))


def _tree(root: Path) -> None:
    """A small tree whose two modules a base slice maps (panel/ is out of scope)."""
    _write(root, "roboco/services/widget.py", SERVICE)
    _write(root, "roboco/config.py", SETTINGS)
    _write(root, "panel/src/hooks/use-widget.ts", PANEL_HOOK)
    _write(
        root,
        "docs/map/base.md",
        "| Path | Role |\n|---|---|\n"
        "| roboco/services/widget.py | Widgets |\n"
        "| roboco/config.py | Settings |\n",
    )


def _kinds(root: Path) -> list[str]:
    return sorted(f.kind for f in vcm.check_map(root))


# ---------------------------------------------------------------------------
# Errors: what fails the gate
# ---------------------------------------------------------------------------


def test_missing_path_is_an_error_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _tree(tmp_path)
    _slice(
        tmp_path,
        """\
        ## Files

        | Path | Role | LOC |
        |---|---|---|
        | roboco/services/gone.py | Deleted long ago | 10 |
        """,
    )
    assert vcm.main(["--root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "missing-path: `roboco/services/gone.py` does not exist" in out


def test_symbol_not_defined_in_the_cited_file_is_an_error(tmp_path: Path) -> None:
    _tree(tmp_path)
    _slice(
        tmp_path,
        """\
        | Name | Kind | File:Line | Responsibility |
        |---|---|---|---|
        | `Widget.explode` | method | roboco/services/widget.py:13 | Never existed |
        """,
    )
    assert _kinds(tmp_path) == ["missing-symbol"]


def test_a_name_that_is_only_imported_is_not_defined_there(tmp_path: Path) -> None:
    _tree(tmp_path)
    _write(
        tmp_path,
        "roboco/services/facade.py",
        "from roboco.services.widget import build_widget\n\n"
        '__all__ = ["build_widget"]\n',
    )
    _slice(
        tmp_path,
        """\
        | Name | Kind | File:Line | Responsibility |
        |---|---|---|---|
        | build_widget | function | roboco/services/facade.py:1 | Re-export only |
        """,
    )
    assert _kinds(tmp_path) == ["missing-symbol"]


# ---------------------------------------------------------------------------
# Warnings: drift reported, never failing
# ---------------------------------------------------------------------------


def test_line_drift_warns_exits_zero_and_fix_rewrites_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _tree(tmp_path)
    doc = _slice(
        tmp_path,
        """\
        | Name | Kind | File:Line | Responsibility |
        |---|---|---|---|
        | `build_widget` | function | roboco/services/widget.py:2 | Factory |
        | `Widget.spin` | method | roboco/services/widget.py:~40 | Spins |
        """,
    )
    assert vcm.main(["--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    # Fixable drift is counted, not listed, unless --verbose.
    assert "line-drift 2" in out
    assert "widgets.md:3: line-drift" not in out
    assert vcm.main(["--root", str(tmp_path), "--verbose"]) == 0
    assert "widgets.md:3: line-drift" in capsys.readouterr().out

    assert vcm.main(["--root", str(tmp_path), "--fix"]) == 0
    text = doc.read_text()
    assert "roboco/services/widget.py:17 |" in text
    # The decorated method is anchored at its `def` line, `~` dropped.
    assert "roboco/services/widget.py:13 |" in text
    assert _kinds(tmp_path) == []


def test_a_decorator_line_counts_as_the_definition(tmp_path: Path) -> None:
    _tree(tmp_path)
    _slice(
        tmp_path,
        """\
        | Name | Kind | File:Line | Responsibility |
        |---|---|---|---|
        | `Widget.spin` | method | roboco/services/widget.py:12 | Spins |
        """,
    )
    assert _kinds(tmp_path) == []


def test_a_range_is_shifted_with_its_symbol(tmp_path: Path) -> None:
    _tree(tmp_path)
    doc = _slice(
        tmp_path,
        """\
        | Name | Kind | File:Line | Responsibility |
        |---|---|---|---|
        | `build_widget` | function | roboco/services/widget.py:10-11 | Factory |
        """,
    )
    vcm.main(["--root", str(tmp_path), "--fix"])
    assert "roboco/services/widget.py:17-18 |" in doc.read_text()


def test_loc_drift_is_fixed_and_an_approximate_size_tolerates_noise(
    tmp_path: Path,
) -> None:
    _tree(tmp_path)
    big = _write(tmp_path, "roboco/big.py", "VALUE = 1\n" * 100)
    big_lines = len(big.read_text().splitlines())
    doc = _slice(
        tmp_path,
        f"""\
        | Path | Role | LOC |
        |---|---|---|
        | roboco/services/widget.py | Widgets | 999 |
        | roboco/config.py | Settings | ~200 |
        | roboco/big.py | Big | ~{big_lines + 5} |
        | roboco/services/ | Package | — |
        """,
    )
    assert _kinds(tmp_path) == ["loc-drift", "loc-drift"]
    vcm.main(["--root", str(tmp_path), "--fix"])
    text = doc.read_text()
    assert f"| Widgets | {len(SERVICE.splitlines())} |" in text
    # An approximate size keeps its marker when it is rewritten, and one
    # within the tolerance is left alone.
    assert f"| Settings | ~{len(SETTINGS.splitlines())} |" in text
    assert f"| Big | ~{big_lines + 5} |" in text
    assert _kinds(tmp_path) == []


def test_prose_row_drift_is_a_suggestion_never_an_automatic_fix(
    tmp_path: Path,
) -> None:
    _tree(tmp_path)
    doc = _slice(
        tmp_path,
        """\
        | Title | File:Line | Claim | Severity |
        |---|---|---|---|
        | `build_widget` leaks | roboco/services/widget.py:3 | Some claim. | low |
        | `build_widget` is fresh | roboco/services/widget.py:18 | In the body. | low |
        | Nothing named here | roboco/services/widget.py:500 | Past the end. | low |
        """,
    )
    before = doc.read_text()
    findings = vcm.check_map(tmp_path)
    assert sorted(f.kind for f in findings) == ["line-out-of-range", "prose-line-drift"]
    assert all(not f.fixes for f in findings)
    vcm.main(["--root", str(tmp_path), "--fix"])
    assert doc.read_text() == before


# ---------------------------------------------------------------------------
# Resolution rules
# ---------------------------------------------------------------------------


def test_abbreviated_paths_resolve_through_the_slice_files_table(
    tmp_path: Path,
) -> None:
    _tree(tmp_path)
    # A second widget.py elsewhere makes the bare basename ambiguous.
    _write(tmp_path, "roboco/api/routes/widget.py", "router = None\n")
    _write(
        tmp_path,
        "docs/map/routes.md",
        "| Path | Role |\n|---|---|\n| roboco/api/routes/widget.py | Routes |\n",
    )
    _slice(
        tmp_path,
        """\
        | Path | Role |
        |---|---|
        | `roboco/services/widget.py` | Widgets |

        | Name | Kind | File:Line | Responsibility |
        |---|---|---|---|
        | `LIMIT` | const | widget.py:21 | The cap |
        | `useWidget` | hook | `hooks/use-widget.ts:3` | Panel hook |
        """,
    )
    assert _kinds(tmp_path) == []


def test_env_names_call_sites_and_module_names_resolve(tmp_path: Path) -> None:
    _tree(tmp_path)
    _write(
        tmp_path,
        "roboco/services/caller.py",
        "from roboco.services.widget import build_widget\n\n\n"
        "def run() -> None:\n    build_widget()\n",
    )
    _slice(
        tmp_path,
        """\
        | Flag | Default | File:Line | Effect |
        |---|---|---|---|
        | `ROBOCO_WIDGET_ENABLED` | false | roboco/config.py:2 | Arms widgets |

        | Symbol | Invoked from | Trigger |
        |---|---|---|
        | `build_widget` | `roboco/services/caller.py:5` | Each run |

        | Name | File | Trigger |
        |---|---|---|
        | widget | roboco/services/widget.py | `python -m roboco.services.widget` |
        """,
    )
    assert _kinds(tmp_path) == []


def test_brace_and_glob_paths_must_each_resolve(tmp_path: Path) -> None:
    _tree(tmp_path)
    _slice(
        tmp_path,
        """\
        | Path | Role |
        |---|---|
        | `roboco/{services,config}/` | half missing |
        | `roboco/services/*.py` | glob |
        | `panel/src/hooks/use-{widget,gadget}.ts` | one missing |
        """,
    )
    findings = vcm.check_map(tmp_path)
    assert sorted(f.kind for f in findings) == ["missing-path", "missing-path"]


def test_migration_range_is_fixed_and_missing_rows_are_listed(
    tmp_path: Path,
) -> None:
    for name in ("001_initial.py", "002_more.py", "003_latest.py"):
        _write(tmp_path, f"alembic/versions/{name}", '"""m."""\n')
    doc = _write(
        tmp_path,
        "docs/map/db-migrations.md",
        textwrap.dedent(
            """\
            The Alembic chain (001→002) runs on boot.

            | Path | Role |
            |---|---|
            | `alembic/versions/` | 2 migration files 001..002 |

            | Num | File | What it adds/changes |
            |-----|------|---------------------|
            | 001 | 001_initial.py | Tables. |
            | 002 | 002_more.py | More. |
            """
        ),
    )
    assert _kinds(tmp_path) == [
        "migration-range",
        "migration-range",
        "unmapped-migration",
    ]
    vcm.main(["--root", str(tmp_path), "--fix"])
    text = doc.read_text()
    assert "Alembic chain (001→003)" in text
    assert "3 migration files 001..003" in text
    findings = vcm.check_map(tmp_path)
    assert [f.kind for f in findings] == ["unmapped-migration"]
    assert findings[0].is_error
    assert "Migration Chain table" in findings[0].message


def test_a_module_no_table_names_is_an_error_naming_its_slice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _tree(tmp_path)
    _write(tmp_path, "roboco/services/gadget.py", "GADGET = 1\n")
    assert vcm.main(["--root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert (
        "docs/map/base.md:1: unmapped-module: `roboco/services/gadget.py` is "
        "named by no docs/map table: its neighbours are mapped in "
        "`docs/map/base.md`"
    ) in out
    assert "then run `make map`" in out


def test_a_module_with_no_mapped_neighbour_points_at_the_slice_list(
    tmp_path: Path,
) -> None:
    _tree(tmp_path)
    _write(tmp_path, "scripts/new_tool.py", "print(1)\n")
    (finding,) = vcm.check_map(tmp_path)
    assert finding.kind == "unmapped-module"
    assert finding.doc == "docs/map/_front.md"
    assert "start a new slice" in finding.message


def test_a_directory_row_maps_no_module_but_a_glob_row_does(tmp_path: Path) -> None:
    _tree(tmp_path)
    _write(tmp_path, "roboco/gadgets/one.py", "ONE = 1\n")
    _write(tmp_path, "roboco/gadgets/two.py", "TWO = 2\n")
    _write(tmp_path, "docker/agent.Dockerfile", "FROM scratch\n")
    doc = _slice(
        tmp_path,
        """\
        | Path | Role |
        |---|---|
        | roboco/gadgets/ | The gadgets package |
        | docker/ | Images |
        """,
    )
    assert _kinds(tmp_path) == ["unmapped-module"] * 3
    doc.write_text(
        "| Path | Role |\n|---|---|\n"
        "| `roboco/gadgets/*.py` | The gadgets |\n"
        "| `docker/agent.Dockerfile` | The agent image |\n"
    )
    assert _kinds(tmp_path) == []


@pytest.mark.parametrize(
    ("path", "required"),
    [
        ("roboco/services/x.py", True),
        ("roboco/services/__init__.py", False),
        ("roboco/vault_assets/meta/README.md", False),
        ("roboco/vault_assets/obsidian/graph.json", False),
        ("docker/scripts/hooks/stop-hook.sh", True),
        ("docker/git-support/hooks/README.md", False),
        ("scripts/bootstrap.sh", True),
        ("panel/src/app/page.tsx", False),
        ("tests/unit/test_x.py", False),
    ],
)
def test_which_files_need_a_map_row(path: str, *, required: bool) -> None:
    assert vcm.requires_map_row(path) is required


def test_generated_concat_and_fenced_tables_are_not_read(tmp_path: Path) -> None:
    _tree(tmp_path)
    _write(
        tmp_path,
        "docs/map/_complete_map.md",
        "| Path | Role |\n|---|---|\n| roboco/gone.py | stale copy |\n",
    )
    _slice(
        tmp_path,
        """\
        ```
        | Path | Role |
        |---|---|
        | roboco/gone.py | inside a fence |
        ```
        """,
    )
    assert _kinds(tmp_path) == []


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cell", "names"),
    [
        ("`Widget.spin`", ["Widget.spin"]),
        ("`a`/`b` / c", ["a", "b", "c"]),
        ("write_agents_md (kimi)", ["write_agents_md"]),
        ("`ConventionsStandard.parse_yaml(text)`", ["ConventionsStandard.parse_yaml"]),
        ("GET /api/release/proposal", []),
        ("scripts/regenerate_verb_tables.py", []),
        ("Choreographer verb dispatch (can_invoke_intent)", []),
    ],
)
def test_symbol_names(cell: str, names: list[str]) -> None:
    assert vcm.symbol_names(cell) == names


def test_split_cells_keeps_escaped_pipes_and_offsets() -> None:
    line = r"| `a \| b` | x.py:3 |"
    cells = vcm.split_cells(line)
    assert [c.text for c in cells] == [r"`a \| b`", "x.py:3"]
    assert line[cells[1].start :].startswith("x.py:3")


# ---------------------------------------------------------------------------
# Wiring and the committed map
# ---------------------------------------------------------------------------


def test_quality_target_runs_the_map_check() -> None:
    text = MAKEFILE.read_text()
    quality_block = text.split("\n.PHONY: quality\n", 1)[1].split("\n.PHONY: ", 1)[0]
    assert "scripts/verify_codebase_map.py" in quality_block, (
        "make quality must run scripts/verify_codebase_map.py (the docs/map "
        "reference gate) — wiring was removed"
    )
    assert "scripts/verify_codebase_map.py --fix" not in quality_block
    assert "scripts/rebuild_map_concat.py --check" in quality_block, (
        "make quality must fail when _complete_map.md is behind its slices"
    )


def test_codegen_rebuilds_the_concatenation_but_never_rewrites_lines() -> None:
    text = MAKEFILE.read_text()
    codegen_block = text.split("\n.PHONY: codegen\n", 1)[1].split("\n\n", 1)[0]
    assert "scripts/rebuild_map_concat.py" in codegen_block
    assert "verify_codebase_map.py" not in codegen_block


def test_committed_map_has_no_errors() -> None:
    errors = [f.render() for f in vcm.check_map(ROOT) if f.is_error]
    assert errors == [], "\n".join(errors)
