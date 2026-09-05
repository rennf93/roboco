# Project Agent-Access Enforcement (allowed_agents)

`ProjectService.check_agent_access` (`roboco/services/project.py:698`) encodes a
project's access rule — the project must exist, the requesting agent's team
must match the project's assigned cell, and an optional `allowed_agents` list
further restricts the whole-cell default down to named agents. Until this
change the rule had zero callers: setting `allowed_agents` via the write
routes had no observable effect anywhere in the system. It is now enforced at
the **claim path** — the chokepoint an agent actually traverses when it is
granted work on a project.

## The write path (unchanged)

`POST` / `DELETE /projects/{project_id}/access/{agent_id}`
(`add_allowed_agent` / `remove_allowed_agent`, `roboco/api/routes/project.py:406,449`)
remain the **only** way to mutate `Project.allowed_agents`
(`roboco/models/project.py:205`). No new routes were added; this change only
wires an existing rule into an existing chokepoint.

## The enforcement chokepoint

`Choreographer._agent_access_claim_guard`
(`roboco/services/gateway/choreographer/_impl.py`) resolves the guard's
inputs — the task's eager-loaded `project` relationship and the claiming
agent's team (via `TaskService.agent_for` → `GatewayAgentView.team`) — and
calls `ProjectService.check_agent_access(project_id, agent_id, team)` as the
**sole** deny/allow decision. The guard does not re-implement any part of the
rule; it only shapes the refusal via the pure predicate
`agent_access_denied_guard` (`roboco/services/gateway/claim_guards.py`), which
mirrors the existing `unmet_dependency_guard` shape (no DB I/O, no rule
logic).

The guard is inert (returns `None`, no refusal) whenever:

- the Choreographer's `project` dependency is unset (existing
  `ChoreographerDeps` constructions / tests that don't plumb it),
- the task has no project (a branchless coordination root),
- the claiming agent's view carries no resolvable string team, or an
  unrecognized team value (mock-safety, mirroring `_sequence_claim_guard`).

It is wired as a new **opt-in** flag, `check_agent_access: bool = False`, on
`Choreographer._run_claim_guards`, composed together with the pre-existing
`check_project_budget` flag through a shared `_opt_in_claim_guards` helper.
Both flags are set `True` at exactly the two **work-starting** claim call
sites: `i_will_work_on` and `i_will_plan` (both in `_impl.py`). Every other
claim path — QA's `claim_review`, the documenter's `claim_doc_task`, the
PR-gate's `claim_gate_review`, `claim_pr_review` — leaves the flag at its
`False` default, so review/doc/gate claims of already-in-flight work are
never blocked by an access rule that changed after the work started.

## Behavior

- **`allowed_agents=None`** (today's default): `check_agent_access` passes
  trivially — whole-cell access, byte-for-byte unchanged from before this
  change.
- **`allowed_agents=[...]`, agent not on the list** (same cell): the claim is
  refused with `Envelope.not_authorized`, naming the project id, agent id,
  and task id, and a `remediate` pointing at
  `POST /projects/{project_id}/access/{agent_id}` (add the agent) or
  reassigning the task to a permitted agent. The refusal is machine-readable
  — `error: "not_authorized"` — not a formatted string an integration would
  have to parse.
- **Cell mismatch**: still refused by the same rule (assigned-cell check is
  part of `check_agent_access`, unchanged by this task).

A denied claim is **not** auto-released back to the pool — the PM reassigns
it. Auto-releasing would loop the same denied agent right back onto the same
task via `give_me_work`.

## Testing

`tests/unit/gateway/test_agent_access_claim_guard.py` covers the pure
predicate (pass / deny shape) and the Choreographer wiring (every inert
condition above, the rule-denied/rule-passed paths, and both the opt-in-false
and opt-in-true `_run_claim_guards` compositions).

## Related files

- **Rule:** `roboco/services/project.py:698` (`ProjectService.check_agent_access`, untouched by this task)
- **Write routes:** `roboco/api/routes/project.py:406,449`
- **Model field:** `roboco/models/project.py:205` (`Project.allowed_agents`)
- **Guard predicate:** `roboco/services/gateway/claim_guards.py` (`agent_access_denied_guard`)
- **Guard wiring:** `roboco/services/gateway/choreographer/_impl.py` (`_agent_access_claim_guard`, `_opt_in_claim_guards`, `ChoreographerDeps.project`)
- **DI:** `roboco/api/deps.py` (`get_choreographer` wires `project=get_project_service(db_session)`)
- **Tests:** `tests/unit/gateway/test_agent_access_claim_guard.py`
