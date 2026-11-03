"""Every flow verb a role's manifest advertises must have an HTTP route.

Original bug (2026-11-02 live): ``unclaim`` was in the pr_reviewer manifest
(lifecycle allows it) but ``flow_pr_reviewer`` never registered the route, so
the reviewer got a 404 "no route" envelope with no way to release a claim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.routing import APIRoute
from roboco.api.routes.v1 import (
    flow_auditor,
    flow_board,
    flow_cell_pm,
    flow_dev,
    flow_devops,
    flow_doc,
    flow_main_pm,
    flow_pr_reviewer,
    flow_qa,
)
from roboco.services.gateway.role_config import ROLE_CONFIGS

if TYPE_CHECKING:
    from fastapi import APIRouter

_ROUTERS_BY_ROLE = {
    "developer": flow_dev.router,
    "qa": flow_qa.router,
    "documenter": flow_doc.router,
    "cell_pm": flow_cell_pm.router,
    "main_pm": flow_main_pm.router,
    "auditor": flow_auditor.router,
    "pr_reviewer": flow_pr_reviewer.router,
    "devops": flow_devops.router,
    "product_owner": flow_board.router,
    "head_marketing": flow_board.router,
}

# flow_server._INTENT_TO_PUBLIC (that module needs spawn env vars to import).
_INTENT_TO_PUBLIC = {"pass_review": "pass", "fail_review": "fail"}


def _routed_verbs(router: APIRouter) -> set[str]:
    return {
        route.path.rsplit("/", 1)[-1]
        for route in router.routes
        if isinstance(route, APIRoute)
    }


@pytest.mark.parametrize("role", sorted(_ROUTERS_BY_ROLE))
def test_every_advertised_flow_verb_has_a_route(role: str) -> None:
    advertised = {_INTENT_TO_PUBLIC.get(v, v) for v in ROLE_CONFIGS[role].flow_tools}
    missing = advertised - _routed_verbs(_ROUTERS_BY_ROLE[role])
    assert not missing, f"{role} advertises verbs with no route: {sorted(missing)}"
