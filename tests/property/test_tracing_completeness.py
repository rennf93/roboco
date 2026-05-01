"""Property test: every completed task has full tracing.

Filled in fully in Phase 4 once all roles use the gateway. Phase 0 ships
the scaffold so the test path is established.
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="filled in Phase 4 once all roles use the gateway")
def test_completed_tasks_have_full_tracing() -> None:
    """For every task with status=completed in the smoke-test fixture batch:
       - audit_log has >=1 entry per state transition with non-null agent_id
       - dev role has >=1 journal:reflect for the task
       - QA role has >=1 journal:learning for the task
       - PM role has >=1 journal:decision for the task
       - acceptance_criteria_status: every criterion has referencing_artifact_id
       - qa_evidence_inspected = true
    """
    pass
