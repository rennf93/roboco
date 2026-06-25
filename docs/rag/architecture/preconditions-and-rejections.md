# Preconditions and Rejection Kinds

## What are preconditions?

A **Precondition** is a declarative gate-check that the gateway verifies before allowing an action. Each precondition has four parts:

| Field | Meaning |
|-------|---------|
| `key` | Internal name (e.g., `owns_task`) |
| `check` | A function that returns True if the precondition passes |
| `remediate` | Human-readable hint surfaced when the precondition fails |
| `missing_token` | What appears in the `tracing_gap.missing[]` array when it fails (for input artifact errors) |
| `rejection_kind` | **NEW:** Controls which `error` flavor is returned on failure (see below) |

When a verb is invoked, the gateway checks all preconditions for that verb. If any fail, the agent receives a structured error envelope.

## The two rejection kinds: `tracing_gap` vs `not_authorized`

When a precondition fails, the error flavor depends on the **reason for the failure**:

### `tracing_gap` (default)

**Meaning:** A required artifact is missing — the agent needs to do something to provide it.

**Examples:**
- `PRECONDITION_COMMITS` fails if the developer hasn't made any commits yet
- `PRECONDITION_PR_EXISTS` fails if the developer hasn't opened a PR

**Agent experience:**
```json
{
  "error": "tracing_gap",
  "message": "Missing required commit(s)",
  "missing": ["commits"],
  "remediate": "commit() at least once with a non-empty message before submitting"
}
```

The `missing[]` array tells the agent exactly what artifact is missing, so they can take the right action.

### `not_authorized` (ownership / identity gates)

**Meaning:** The agent is not allowed to perform this action — a role/permission boundary, not a missing artifact.

**Examples:**
- **`PRECONDITION_OWNERSHIP`** fails if the agent is not assigned to the task
- **Self-review block** fails if the QA agent is the original developer
- **Role gate** fails if a non-PM tries to merge

**Agent experience:**
```json
{
  "error": "not_authorized",
  "message": "task is not assigned to you; call give_me_work() to find your work",
  "remediate": "task is not assigned to you; call give_me_work() to find your work"
}
```

There is no `missing[]` array — the agent is simply not allowed, and the remediate message tells them what to do instead (usually "find your own work" or "have a different role perform this").

## How `rejection_kind` works

When a Precondition is defined, it includes a `rejection_kind` field that determines which error flavor it returns:

```python
@dataclass(frozen=True)
class Precondition:
    key: str
    check: Callable[[Any, Any, Any], bool]
    remediate: str
    missing_token: str
    rejection_kind: RejectionKind = "tracing_gap"  # default
```

**Built-in preconditions and their rejection kinds:**

| Precondition | `rejection_kind` | Why |
|--------------|------------------|-----|
| `PRECONDITION_OWNERSHIP` | `not_authorized` | Unowned tasks are authorization failures, not missing artifacts |
| `PRECONDITION_COMMITS` | `tracing_gap` | Commits are missing artifacts the agent can create |
| `PRECONDITION_PR_EXISTS` | `tracing_gap` | A PR is a missing artifact the agent can create |
| Most others | `tracing_gap` | Missing data artifacts the agent can provide |

## Dispatch logic in `_check_intent_preconditions`

When the gateway evaluates verb preconditions, it checks them in order and returns the first failure:

```python
def _check_intent_preconditions(
    spec_intent: IntentSpec, task: Any, ctx: Context
) -> Decision | None:
    """Verb-level extra_preconditions gate.
    
    If the first failing precondition has rejection_kind='not_authorized',
    return Decision.reject(kind='not_authorized').
    All other failures return Decision.tracing_gap.
    """
    missing = [
        p.missing_token
        for p in spec_intent.extra_preconditions
        if not p.check(task, None, ctx)
    ]
    if not missing:
        return None
    
    first_missing = next(
        p for p in spec_intent.extra_preconditions 
        if p.missing_token == missing[0]
    )
    
    # Check the rejection_kind of the first failing precondition
    if first_missing.rejection_kind == "not_authorized":
        return Decision.reject(
            kind="not_authorized",
            message=first_missing.remediate,
            remediate=first_missing.remediate,
        )
    
    # Default: tracing_gap with missing tokens
    return Decision.tracing_gap(
        missing=missing, 
        remediate=first_missing.remediate
    )
```

The key insight: **Only the first failing precondition's `rejection_kind` is checked.** This ensures ownership gates are checked early (they usually are in the preconditions list) so unowned tasks fail fast with `not_authorized` instead of collecting other tracing gaps.

## Agent-visible impact

When an agent tries to perform an action on a task they don't own, they now see:

```json
{
  "error": "not_authorized",
  "message": "task is not assigned to you; call give_me_work() to find your work",
  "remediate": "task is not assigned to you; call give_me_work() to find your work"
}
```

This is semantically clearer than the previous `tracing_gap` / `owns_task` message: it's an authorization failure, not a data-collection problem. The agent cannot add a "missing" artifact to fix it — they need a different task.

## When to add a new precondition with `rejection_kind='not_authorized'`

When designing a new gate-check precondition:

- Use `rejection_kind='not_authorized'` if the failure is a **role or identity boundary** (the agent is the wrong person / role for this action)
- Use the default `rejection_kind='tracing_gap'` if the failure is a **missing artifact** (the agent can provide / create it)

**Example:** A new "task must be in this project" check would use `not_authorized` because the agent is the wrong role/team, not because they're missing data.

## See also

- [How agents are sandboxed](../company/agent-gateway.md) — the gateway, verbs, and envelope
- [REST API](../../api/rest-api.md) — error envelope schema and error flavors
- [Task model](./task-model.md) — task fields and state
