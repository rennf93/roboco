"""The orchestrator's internal API calls must carry an authorized identity.

Regression guard for the wedge where dispatcher ``httpx`` clients were built
without an agent identity, so the orchestrator's self-PATCHes (auto-block /
auto-resume / auto-recover / SLA annotation) were rejected ``401 Missing
X-Agent-ID`` and silently no-op'd — leaving paused/blocked parents stuck and
their dependents stranded. The fix gives every API-facing dispatcher client the
system identity; these tests lock that the identity is both *present* and
*authorized* for task writes (otherwise the self-call would 403 instead of act).
"""

from roboco.foundation import identity as _foundation
from roboco.models import AgentRole
from roboco.models.permissions import TASK_PERMISSIONS, TaskAction
from roboco.runtime.orchestrator import _SYSTEM_API_HEADERS


def test_system_api_headers_match_the_system_identity() -> None:
    system = _foundation.AGENTS["system"]
    assert _SYSTEM_API_HEADERS["X-Agent-ID"] == str(system.uuid)
    assert _SYSTEM_API_HEADERS["X-Agent-Role"] == "system"


def test_system_identity_is_authorized_for_task_writes() -> None:
    # admin_set_status — the audited override path the orchestrator's
    # auto-recover / auto-resume drive — is gated behind TaskAction.ASSIGN.
    # The identity the orchestrator sends must hold it.
    assert TaskAction.ASSIGN in TASK_PERMISSIONS[AgentRole.SYSTEM]
