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
roboco_task_escalate(
    task_id="uuid-here",
    reason="Need clarification on requirements"
)
```

Auto-routes to your escalation target. You CANNOT choose a different target.

## CEO Escalation (PM Only)

```python
roboco_task_escalate_to_ceo(
    task_id="uuid-here",
    notes="Major feature ready for approval"
)
```

Requirements:
- Task in `awaiting_pm_review`
- PR exists
- Only PMs can call this

## Cannot Skip Levels

System enforces chain:
- Developer CANNOT escalate directly to Main PM
- Cell PM CANNOT escalate directly to CEO
- Each level must acknowledge and decide
