"""Per-project environment ladder helpers.

A project's environment ladder is an ordered ``list[{name, branch}]``: index 0
is **head** (the branch dev/cell/leaf PRs target — the dev trunk), index -1 is
**prod** (the branch the gated release executor commits + tags on), and the
middle rungs are intermediates (qa/stag). This separates "where work lands"
from "what prod is", which a single ``default_branch`` could not.

When ``environments`` is null/empty the ladder is synthesized from
``default_branch`` as a degenerate single-branch ladder (head == prod ==
``default_branch``), so every consumer — PR target, release target, sync —
keeps behaving exactly as before the column existed. Operators declare a real
split in the panel.

These helpers are the single chokepoint every consumer routes through; never
read ``project.environments`` raw. Pure domain logic (pydantic-only), kept in
``roboco.models`` so the Project model can import it without a services-cycle.
"""

from __future__ import annotations

from pydantic import BaseModel

# A project or project-row duck-type: both the pydantic ``Project`` and the
# SQLAlchemy ``ProjectTable`` expose ``environments`` and ``default_branch``.
_ENV_LADDER = "head"
_PROD_LADDER = "prod"


class EnvRung(BaseModel):
    """One rung of the environment ladder."""

    name: str
    branch: str


def _coerce_rungs(
    raw: list[dict[str, str]] | list[EnvRung] | None,
) -> list[EnvRung] | None:
    """Normalize a raw list of dicts/models into EnvRungs; None/empty -> None."""
    if not raw:
        return None
    out: list[EnvRung] = []
    for item in raw:
        if isinstance(item, EnvRung):
            out.append(item)
        else:
            out.append(EnvRung.model_validate(item))
    return out


def effective_environments(project: object) -> list[EnvRung]:
    """Return the project's resolved environment ladder (never empty).

    Falls back to a degenerate single-branch ladder synthesized from
    ``default_branch`` when ``environments`` is null/empty, so behavior is
    unchanged until the operator declares a real ladder.
    """
    rungs = _coerce_rungs(getattr(project, "environments", None))
    if rungs:
        return rungs
    fallback = str(getattr(project, "default_branch", None) or "master")
    return [
        EnvRung(name=_ENV_LADDER, branch=fallback),
        EnvRung(name=_PROD_LADDER, branch=fallback),
    ]


def head_branch(project: object) -> str:
    """The branch dev/cell/leaf PRs target (ladder index 0)."""
    return effective_environments(project)[0].branch


def prod_branch(project: object) -> str:
    """The branch the gated release executor commits + tags on (last rung)."""
    return effective_environments(project)[-1].branch


def ladder_pairs(project: object) -> list[tuple[EnvRung, EnvRung]]:
    """Adjacent rung pairs for the prod->head cascade, top-down.

    For ``[head, qa, stag, prod]`` (index 0..3) returns
    ``[(prod, stag), (stag, qa), (qa, head)]`` — each ``(upper, lower)`` pair
    means "merge ``upper`` into ``lower``". Empty for a 1-rung ladder.
    """
    rungs = effective_environments(project)
    return [(rungs[i], rungs[i - 1]) for i in range(len(rungs) - 1, 0, -1)]


def promotion_chain(project: object) -> list[str]:
    """Branches to merge into the prod checkout head->...->just-below-prod on a
    CEO-gated release (the full-chain promotion).

    Every rung except the last (prod), excluding any rung sharing the prod
    branch — so a degenerate (head==prod) ladder yields ``[]`` (no-op). Order
    is head-first: ``[dev, qa, stag]`` for ``[dev, qa, stag, master]``.
    """
    rungs = effective_environments(project)
    prod = prod_branch(project)
    return [r.branch for r in rungs[:-1] if r.branch != prod]


def normalize_environments(
    value: list[dict[str, str]] | list[EnvRung] | None,
) -> list[dict[str, str]] | None:
    """Validate + normalize a ladder before persistence.

    Rejects empty name/branch, de-dupes by branch, preserves the declared
    order. Returns None for None/empty so the column stays null (the shim
    synthesizes the degenerate ladder from ``default_branch`` at read time).
    """
    rungs = _coerce_rungs(value)
    if not rungs:
        return None
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for rung in rungs:
        name = rung.name.strip()
        branch = rung.branch.strip()
        if not name or not branch:
            raise ValueError("each environment rung needs a non-empty name and branch")
        if branch in seen:
            raise ValueError(
                f"duplicate environment branch {branch!r}; "
                "each rung must target a distinct branch"
            )
        seen.add(branch)
        out.append({"name": name, "branch": branch})
    return out
