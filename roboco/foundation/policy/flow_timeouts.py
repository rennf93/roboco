"""Flow-verb timeout budgets — single source for both timeout walls.

Two independent walls exist on every ``/api/v1/flow/*`` call: the server's
``FlowVerbTimeoutMiddleware`` (``roboco/api/middleware.py``) and the agent-side
MCP client's ``httpx.Client(timeout=...)`` (``roboco/mcp/flow_server.py``). The
client wall must always OUTLAST the server wall — otherwise the agent's httpx
client gives up with a raw transport error before the middleware ever gets to
return its clean, retryable 504 ``gateway_timeout`` envelope, and the agent
sees a Python exception instead of a directed remediation hint. Both sides
import ``SLOW_VERBS`` from here so they can never classify a verb differently.
"""

from __future__ import annotations

# Verbs whose own work routinely exceeds the default flow-verb budget:
# i_am_done (git push + the pre-submit quality gate), submit_up / submit_root
# / open_pr (a multi-step PR-create chain), i_will_work_on (workspace clone,
# up to 300s). Deliberately EXCLUDES i_will_plan / delegate: the slow budget
# also governs the server middleware wall, and i_will_plan is exactly the
# verb that wedged in #326 holding the SELECT FOR UPDATE task-row lock — a
# 900s budget would let a wedged planning verb block its task row for 15
# minutes instead of 2. Healthy planning verbs are DB writes that complete
# in seconds; their 30s+ production runs were contention symptoms.
SLOW_VERBS = frozenset(
    {
        "i_am_done",
        "submit_up",
        "submit_root",
        "open_pr",
        "i_will_work_on",
    }
)

# Client-side margin added on top of the matching server budget.
CLIENT_HEADROOM_SECONDS = 10
