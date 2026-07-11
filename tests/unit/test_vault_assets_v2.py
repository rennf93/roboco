"""V2 vault assets: Obsidian Bases views (`.base`) ship via the same
copy-never-overwrite ``ensure_vault_assets`` path as the Dataview templates.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from roboco.vault import ensure_vault_assets


def test_ensure_vault_assets_materializes_base_files(tmp_path: Path) -> None:
    ensure_vault_assets(tmp_path)
    task_board = tmp_path / "RoboCo" / "_meta" / "Task Board.base"
    reports = tmp_path / "RoboCo" / "_meta" / "Reports.base"
    sync_doc = tmp_path / "RoboCo" / "_meta" / "Sync to your Mac.md"
    assert task_board.exists()
    assert reports.exists()
    assert sync_doc.exists()


def test_base_files_are_valid_yaml() -> None:
    meta_dir = Path(__file__).resolve().parents[2] / "roboco" / "vault_assets" / "meta"
    for name in ("Task Board.base", "Reports.base"):
        data = yaml.safe_load((meta_dir / name).read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "views" in data
        assert isinstance(data["views"], list)
        assert data["views"]
