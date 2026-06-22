"""Repo auto-scan + scaffold-draft renderer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.conventions.scan import derive_from_scan, render_yaml
from roboco.foundation.policy.conventions.models import ConventionsStandard

if TYPE_CHECKING:
    from pathlib import Path


def _sample_repo(root: Path) -> None:
    (root / "app" / "routers").mkdir(parents=True)
    (root / "app" / "models").mkdir(parents=True)
    (root / "app" / "services").mkdir(parents=True)
    (root / "app" / "routers" / "users.py").write_text("x = 1\n")
    (root / "app" / "models" / "user.py").write_text("y = 2\n")
    (root / "app" / "services" / "logic.py").write_text("z = 3\n")


def test_scan_derives_router_module_forbidding_models(tmp_path: Path) -> None:
    _sample_repo(tmp_path)
    std = derive_from_scan(tmp_path)
    routers = [m for m in std.modules if m.path == "app/routers"]
    assert routers and "model" in routers[0].forbidden


def test_scan_detects_python_language(tmp_path: Path) -> None:
    _sample_repo(tmp_path)
    assert "python" in derive_from_scan(tmp_path).languages


def test_scan_seeds_builtin_rules(tmp_path: Path) -> None:
    _sample_repo(tmp_path)
    std = derive_from_scan(tmp_path)
    assert std.rules["no_models_in_routers"].level == "block"
    assert std.rules["no_inline_comments"].level == "warn"


def test_scan_ignores_vendored_directories(tmp_path: Path) -> None:
    (tmp_path / "node_modules" / "pkg" / "routers").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "models").mkdir(parents=True)
    std = derive_from_scan(tmp_path)
    assert std.modules == []


def test_scan_lifts_claude_md_imperative_into_custom_rule(tmp_path: Path) -> None:
    _sample_repo(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("- Never use `print()`; use the logger.\n")
    custom = derive_from_scan(tmp_path).custom
    assert custom
    assert custom[0].level == "warn"
    assert "print" in custom[0].pattern


def test_render_yaml_round_trips_through_parse(tmp_path: Path) -> None:
    _sample_repo(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("Do not call `eval()` anywhere.\n")
    std = derive_from_scan(tmp_path)
    reparsed = ConventionsStandard.parse_yaml(render_yaml(std))
    assert reparsed == std


def test_render_yaml_round_trips_empty_standard() -> None:
    std = ConventionsStandard()
    assert ConventionsStandard.parse_yaml(render_yaml(std)) == std
