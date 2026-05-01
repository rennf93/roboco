"""Tests for role-config catalog."""

from __future__ import annotations

import pytest
from roboco.services.gateway.role_config import (
    ROLE_CONFIGS,
    get_role_config,
)


class TestRoleConfigCatalog:
    def test_developer_config(self) -> None:
        cfg = get_role_config("developer")
        assert "give_me_work" in cfg.flow_tools
        assert "i_will_work_on" in cfg.flow_tools
        assert "i_am_done" in cfg.flow_tools
        assert "commit" in cfg.do_tools
        assert "note" in cfg.do_tools
        assert "evidence" in cfg.do_tools
        assert cfg.allows_write is True
        assert cfg.allows_subagent is False  # devs don't dispatch sub-research

    def test_qa_config(self) -> None:
        cfg = get_role_config("qa")
        assert "claim_review" in cfg.flow_tools
        assert "pass" in cfg.flow_tools
        assert "fail" in cfg.flow_tools
        # QA does NOT have i_am_done / commit
        assert "i_am_done" not in cfg.flow_tools
        assert "commit" not in cfg.do_tools

    def test_documenter_config(self) -> None:
        cfg = get_role_config("documenter")
        assert "claim_doc_task" in cfg.flow_tools
        assert "i_documented" in cfg.flow_tools
        assert cfg.allows_write is True

    def test_cell_pm_config(self) -> None:
        cfg = get_role_config("cell_pm")
        assert "complete" in cfg.flow_tools
        assert "unblock" in cfg.flow_tools
        assert "triage" in cfg.flow_tools
        assert cfg.allows_subagent is True  # PMs may need parallel research

    def test_main_pm_config(self) -> None:
        cfg = get_role_config("main_pm")
        assert "complete" in cfg.flow_tools
        assert "triage_all" in cfg.flow_tools

    def test_unknown_role_raises(self) -> None:
        with pytest.raises(KeyError, match="unknown role"):
            get_role_config("not_a_role")

    def test_all_roles_have_idle(self) -> None:
        for role, cfg in ROLE_CONFIGS.items():
            assert "i_am_idle" in cfg.flow_tools, f"{role} missing i_am_idle"

    def test_no_role_has_toolsearch(self) -> None:
        # ToolSearch is removed entirely — no manifest should include it
        for cfg in ROLE_CONFIGS.values():
            assert "ToolSearch" not in cfg.flow_tools
            assert "ToolSearch" not in cfg.do_tools
