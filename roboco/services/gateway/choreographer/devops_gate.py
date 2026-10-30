"""DevOps infra-review-gate helpers (the Stage-3 second-reviewer gate).

Everything the infra review gate needs that is NOT verb plumbing: the pure
glob predicate, the structured ``devops_review`` verdict reader, the co-claim
marker checks, and the flag-gated ``infra_gate_applies`` resolver. Split out
of ``pr_gate.py`` for the same reason ``second_review_gate.py`` /
``findings.py`` are: a focused, independently testable unit instead of one
more concern piled onto an already-large mixin.

Storage decisions (locked):
- **Co-claim** rides an orchestration marker (``markers.DEVOPS_GATE_CLAIMANT``),
  NOT ``active_claimant_id`` - the primary reviewer owns that single-claimant
  slot and must keep ownership.
- **Verdict** rides the structured ``notes_structured["devops_review"]`` note
  (verdict + head SHA, overwrite-in-place per review round). Chosen over a
  ``task_review_findings`` pass-marker because findings model defects (a pass
  has nothing to insert), while the note is the same storage the gate already
  trusts (``pr_review`` carries verdict/head_sha) and reads in-memory - the
  precondition stays cheap. Devops ``pr_fail`` DEFECTS still land in the
  ledger under the ``devops_review`` origin (see pr_gate).
"""

from __future__ import annotations

from fnmatch import fnmatch
from typing import TYPE_CHECKING, Any

from roboco.foundation.policy.content import markers

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.db.tables import ProjectTable

# The findings-ledger origin for a devops pr_fail AND the structured-note key
# for the devops verdict - one string, two stores (origins are free-form;
# ``second_review`` set the precedent for a dedicated gate-review origin).
DEVOPS_REVIEW_ORIGIN = "devops_review"

# The floating DevOps agent's slug (its static UUID resolves via seeds
# AGENT_UUIDS, the same seam the dispatchers use).
DEVOPS_AGENT_SLUG = "devops-1"


def infra_glob_hit(changed_file: str, glob: str) -> bool:
    """Whether one changed path falls under one infra glob.

    Mirrors the house glob style (``_codeql_glob_overlap``): fnmatch plus a
    directory-prefix tolerance so a bare ``docker`` glob still catches
    ``docker/compose.yml``. ``fnmatch`` treats ``*`` as crossing ``/``, so the
    shipped ``dir/**`` globs match everything beneath them. No negation
    handling - the existing glob helpers have none either.
    """
    if fnmatch(changed_file, glob):
        return True
    return changed_file.startswith(glob.rstrip("/") + "/")


def changed_files_hit_infra(changed_files: list[str], globs: list[str]) -> bool:
    """Whether ANY changed path intersects ANY infra glob. Naive loop - the
    glob sets are tiny and this runs per gate preflight / dispatch tick."""
    return any(
        infra_glob_hit(changed, glob) for changed in changed_files for glob in globs
    )


def devops_verdict_record(t: Any) -> dict[str, Any] | None:
    """The task's stored devops verdict note, defensively read.

    Returns ``None`` for anything that is not a well-shaped verdict payload
    (absent section, a non-dict payload, a missing verdict) so the
    precondition treats unreadable storage as "no verdict" rather than
    crashing the gate.
    """
    section = (getattr(t, "notes_structured", None) or {}).get(DEVOPS_REVIEW_ORIGIN)
    if not isinstance(section, dict) or not section.get("verdict"):
        return None
    return section


def verdict_satisfies_head(record: dict[str, Any], head_sha: str | None) -> bool:
    """Whether a ``passed`` verdict still covers the PR's CURRENT head.

    ``head_sha=None`` from the capture means the lookup failed, not that the
    head moved - fail OPEN (matching ``_capture_pr_head_sha``'s own fail-open
    posture) rather than wedging the gate on a git blip.
    """
    if record.get("verdict") != "passed":
        return False
    return head_sha is None or record.get("head_sha") == head_sha


def is_devops_co_claimant(t: Any, agent_id: UUID) -> bool:
    """Whether ``agent_id`` holds the devops co-claim marker on this task."""
    return markers.get_devops_gate_claimant(t) == str(agent_id)


def devops_agent_id() -> UUID | None:
    """devops-1's static seed UUID, or None if the seed row vanished."""
    from uuid import UUID

    from roboco.seeds.initial_data import AGENT_UUIDS

    raw = AGENT_UUIDS.get(DEVOPS_AGENT_SLUG)
    try:
        return UUID(raw) if raw else None
    except ValueError:
        return None


def devops_authored_task(t: Any, descendants: list[Any], devops_id: UUID) -> bool:
    """Whether the assembled PR's work was authored (or is owned) by devops-1,
    given the task plus its descendant rows (pure so both the choreographer
    and the dispatcher can share the walk once they've fetched descendants).

    The self-review exclusion: a PR the devops agent itself built via the
    delegation lane gets the normal cell review, so the gate must not demand
    a devops verdict on it. An assembled task has no single author, so check
    the task's own assignee plus every CODE-type descendant's assignee (the
    same walk ``_authoring_providers`` uses to resolve authorship).
    """
    from roboco.models.base import TaskType

    if getattr(t, "assigned_to", None) is not None and str(t.assigned_to) == str(
        devops_id
    ):
        return True
    return any(
        getattr(d, "task_type", None) == TaskType.CODE
        and getattr(d, "assigned_to", None) is not None
        and str(d.assigned_to) == str(devops_id)
        for d in descendants
    )


async def resolve_task_project(session: Any, t: Any) -> ProjectTable | None:
    """Resolve the PROJECT ROW for a task (the conventions read needs the
    table, not just the slug). Mirrors ``resolve_task_project_slug``'s
    project_id -> product_id -> cell-map fallthrough exactly - a Main-PM
    coordination root carries only a ``product_id``; its root→master PR lives
    in the product's repo, whose conventions therefore govern the gate.
    """
    from uuid import UUID

    from roboco.services.project import get_project_service

    project_service = get_project_service(session)
    if t.project_id is not None:
        return await project_service.get(t.project_id)
    product_id = getattr(t, "product_id", None)
    if product_id is not None:
        from roboco.services.product import get_product_service

        product_service = get_product_service(session)
        project_ids = await product_service.distinct_project_ids(UUID(str(product_id)))
        if not project_ids:
            return None
        return await project_service.get(project_ids[0])
    cell_map = getattr(t, "cell_projects", None) or []
    seen: set[UUID] = set()
    for mapping in sorted(cell_map, key=lambda m: m.team.value):
        pid = UUID(str(mapping.project_id))
        if pid in seen:
            continue
        seen.add(pid)
        project = await project_service.get(pid)
        if project is not None:
            return project
    return None


async def effective_infra_globs_for_task(session: Any, t: Any) -> list[str]:
    """The project's effective infra globs for a gate task. Best-effort: any
    resolution/read failure yields ``[]`` - an unresolvable declaration must
    never wedge the gate (the precondition then simply does not apply)."""
    from roboco.services.conventions import get_conventions_service

    try:
        project = await resolve_task_project(session, t)
        if project is None:
            return []
        return await get_conventions_service(session).effective_infra_globs(project)
    except Exception:
        return []


async def infra_gate_applies(
    task_service: Any, t: Any, *, changed_files: list[str]
) -> bool:
    """THE predicate: does this gate task require a devops verdict?

    True only when ALL of these hold (cheap-first):
    - ``settings.devops_enabled`` is ON - enforcement is flag-gated because
      without the flag nothing can ever spawn devops-1, so requiring its
      verdict would wedge every infra PR;
    - the changed files intersect the project's effective infra globs (the
      declaration read is deliberately NOT conventions-flag-gated -
      ``effective_infra_globs`` is flag-independent by contract);
    - the task is NOT devops-authored (self-review exclusion).

    The zero-commit ``pr_waived`` waiver needs no check here: a waived task
    reroutes to ``submit_pm_review`` at gate entry and never reaches
    ``awaiting_pr_review`` at all. ``changed_files=[]`` (a fetch failure or a
    branchless task) never intersects, so the gate fails open.
    """
    from roboco.config import settings

    if not settings.devops_enabled:
        return False
    if not changed_files:
        return False
    globs = await effective_infra_globs_for_task(task_service.session, t)
    if not globs or not changed_files_hit_infra(changed_files, globs):
        return False
    devops_id = devops_agent_id()
    if devops_id is not None:
        descendants = await task_service.get_all_descendants(t.id)
        if devops_authored_task(t, list(descendants), devops_id):
            return False
    return True
