"""env_branches — shim, ladder pairs, head/prod resolution, normalization.

Pure domain helpers (pydantic-only); no DB. The read-time shim synthesizes a
degenerate single-branch ladder from ``default_branch`` when ``environments``
is null, so every consumer behaves identically until a real ladder is declared.
"""

from __future__ import annotations

import pytest
from roboco.models.env_branches import (
    EnvRung,
    effective_environments,
    head_branch,
    ladder_pairs,
    normalize_environments,
    prod_branch,
    promotion_chain,
)


class _Proj:
    """Duck-typed project row (matches Project + ProjectTable surface)."""

    def __init__(
        self,
        *,
        default_branch: str = "master",
        environments: list[dict[str, str]] | None = None,
    ) -> None:
        self.default_branch = default_branch
        self.environments = environments


# --- effective_environments shim ------------------------------------------


def test_null_environments_synthesizes_degenerate_ladder() -> None:
    proj = _Proj(default_branch="slave")
    rungs = effective_environments(proj)
    assert [(r.name, r.branch) for r in rungs] == [("head", "slave"), ("prod", "slave")]


def test_empty_environments_synthesizes_degenerate_ladder() -> None:
    proj = _Proj(default_branch="master", environments=[])
    rungs = effective_environments(proj)
    assert [(r.name, r.branch) for r in rungs] == [
        ("head", "master"),
        ("prod", "master"),
    ]


def test_missing_default_branch_falls_back_to_master() -> None:
    proj = _Proj(default_branch="")  # falsy default_branch
    assert head_branch(proj) == "master"
    assert prod_branch(proj) == "master"


def test_set_environments_returned_as_is_preserving_order() -> None:
    proj = _Proj(
        environments=[
            {"name": "head", "branch": "dev"},
            {"name": "qa", "branch": "qa"},
            {"name": "prod", "branch": "master"},
        ]
    )
    rungs = effective_environments(proj)
    assert [r.branch for r in rungs] == ["dev", "qa", "master"]
    assert all(isinstance(r, EnvRung) for r in rungs)


# --- head_branch / prod_branch --------------------------------------------


def test_head_and_prod_single_rung() -> None:
    proj = _Proj(environments=[{"name": "prod", "branch": "master"}])
    assert head_branch(proj) == "master"
    assert prod_branch(proj) == "master"


def test_head_and_prod_two_rungs() -> None:
    proj = _Proj(
        environments=[
            {"name": "head", "branch": "slave"},
            {"name": "prod", "branch": "master"},
        ]
    )
    assert head_branch(proj) == "slave"
    assert prod_branch(proj) == "master"


def test_head_and_prod_four_rungs() -> None:
    proj = _Proj(
        environments=[
            {"name": "head", "branch": "dev"},
            {"name": "qa", "branch": "qa"},
            {"name": "stag", "branch": "stag"},
            {"name": "prod", "branch": "master"},
        ]
    )
    assert head_branch(proj) == "dev"
    assert prod_branch(proj) == "master"


# --- ladder_pairs (prod -> head cascade) -----------------------------------


def test_ladder_pairs_empty_for_single_rung() -> None:
    assert (
        ladder_pairs(_Proj(environments=[{"name": "prod", "branch": "master"}])) == []
    )


def test_ladder_pairs_two_rungs() -> None:
    proj = _Proj(
        environments=[
            {"name": "head", "branch": "dev"},
            {"name": "prod", "branch": "master"},
        ]
    )
    pairs = ladder_pairs(proj)
    assert [(u.branch, lower.branch) for u, lower in pairs] == [("master", "dev")]


def test_ladder_pairs_four_rungs_top_down() -> None:
    proj = _Proj(
        environments=[
            {"name": "head", "branch": "dev"},
            {"name": "qa", "branch": "qa"},
            {"name": "stag", "branch": "stag"},
            {"name": "prod", "branch": "master"},
        ]
    )
    # [(prod, stag), (stag, qa), (qa, head)] — merge upper into lower.
    pairs = ladder_pairs(proj)
    assert [(u.branch, lower.branch) for u, lower in pairs] == [
        ("master", "stag"),
        ("stag", "qa"),
        ("qa", "dev"),
    ]


def test_ladder_pairs_lower_rung_is_never_prod() -> None:
    """The cascade's target is never prod by construction — only CEO merges prod."""
    proj = _Proj(
        environments=[
            {"name": "head", "branch": "dev"},
            {"name": "qa", "branch": "qa"},
            {"name": "prod", "branch": "master"},
        ]
    )
    prod_name = prod_branch(proj)
    for _upper, lower in ladder_pairs(proj):
        assert lower.branch != prod_name


# --- promotion_chain (full-chain release promotion) ----------------------


def test_promotion_chain_empty_for_degenerate_ladder() -> None:
    """head==prod => nothing to promote (no-op release promotion)."""
    assert promotion_chain(_Proj(default_branch="master")) == []
    assert (
        promotion_chain(_Proj(environments=[{"name": "prod", "branch": "master"}]))
        == []
    )


def test_promotion_chain_two_rungs() -> None:
    proj = _Proj(
        environments=[
            {"name": "head", "branch": "dev"},
            {"name": "prod", "branch": "master"},
        ]
    )
    assert promotion_chain(proj) == ["dev"]


def test_promotion_chain_four_rungs_head_first_excluding_prod() -> None:
    proj = _Proj(
        environments=[
            {"name": "head", "branch": "dev"},
            {"name": "qa", "branch": "qa"},
            {"name": "stag", "branch": "stag"},
            {"name": "prod", "branch": "master"},
        ]
    )
    assert promotion_chain(proj) == ["dev", "qa", "stag"]


# --- normalize_environments ------------------------------------------------


def test_normalize_none_returns_none() -> None:
    assert normalize_environments(None) is None
    assert normalize_environments([]) is None


def test_normalize_strips_and_preserves_order() -> None:
    out = normalize_environments(
        [{"name": " head ", "branch": " dev "}, {"name": "prod", "branch": "master"}]
    )
    assert out == [
        {"name": "head", "branch": "dev"},
        {"name": "prod", "branch": "master"},
    ]


def test_normalize_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="non-empty name"):
        normalize_environments([{"name": "", "branch": "dev"}])


def test_normalize_rejects_empty_branch() -> None:
    with pytest.raises(ValueError, match="non-empty name"):
        normalize_environments(
            [{"name": "head", "branch": "  "}]
        )  # branch trimmed to empty


def test_normalize_rejects_duplicate_branch() -> None:
    with pytest.raises(ValueError, match="duplicate environment branch"):
        normalize_environments(
            [{"name": "head", "branch": "dev"}, {"name": "prod", "branch": "dev"}]
        )


def test_normalize_accepts_envrung_models() -> None:
    out = normalize_environments([EnvRung(name="head", branch="dev")])
    assert out == [{"name": "head", "branch": "dev"}]
