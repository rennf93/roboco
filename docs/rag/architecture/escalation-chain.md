# Escalation Chain Reference

Complete escalation mapping for all agents.

## Chain Overview

```
Cell Members → Cell PM → Main PM → Product Owner → CEO
```

## Full Mapping

| Agent | Escalates To |
|-------|--------------|
| `be-dev-1` | `be-pm` |
| `be-dev-2` | `be-pm` |
| `be-qa` | `be-pm` |
| `be-doc` | `be-pm` |
| `fe-dev-1` | `fe-pm` |
| `fe-dev-2` | `fe-pm` |
| `fe-qa` | `fe-pm` |
| `fe-doc` | `fe-pm` |
| `ux-dev-1` | `ux-pm` |
| `ux-dev-2` | `ux-pm` |
| `ux-qa` | `ux-pm` |
| `ux-doc` | `ux-pm` |
| `be-pm` | `main-pm` |
| `fe-pm` | `main-pm` |
| `ux-pm` | `main-pm` |
| `main-pm` | `product-owner` |
| `product-owner` | `ceo` |
| `head-marketing` | `ceo` |
| `auditor` | `ceo` |

## Cell PM for Team

| Team | Cell PM |
|------|---------|
| `backend` | `be-pm` |
| `frontend` | `fe-pm` |
| `ux_ui` | `ux-pm` |

## Escalation Tool

```python
escalate_up(
    task_id="uuid-here",
    reason="Need clarification on requirements"
)
```

Auto-routes to your escalation target. You CANNOT choose a different target. `escalate_up` is a PM verb (Cell PM / Main PM); cell members (devs, QA, documenters) signal blockers with `i_am_blocked(task_id, reason)`, which their Cell PM resolves.

## CEO Escalation (Main PM / Board Only)

```python
escalate_to_ceo(
    task_id="uuid-here",
    reason="Major feature ready for approval"
)
```

Requirements:
- Task in `awaiting_pm_review`
- PR exists
- Only Main PM, Product Owner, or Head of Marketing can call this (Cell PMs cannot — they `escalate_up` to Main PM first)

## Cannot Skip Levels

System enforces chain:
- Developer CANNOT escalate directly to Main PM
- Cell PM CANNOT escalate directly to CEO
- Each level must acknowledge and decide
