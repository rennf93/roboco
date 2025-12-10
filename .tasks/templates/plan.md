# Implementation Plan: TASK-{ID}

> **Created**: YYYY-MM-DD
> **Author**: {agent-id}
> **Status**: {draft / approved / in_progress / completed}

---

## Overview

{High-level description of the approach}

## Goals

1. {Primary goal}
2. {Secondary goal}

## Non-Goals

- {What we're explicitly NOT doing}
- {Scope boundaries}

---

## Approach

### Strategy
{Explain the overall strategy and why}

### Key Design Decisions
- **{Decision 1}**: {Rationale}
- **{Decision 2}**: {Rationale}

### Alternatives Considered
| Alternative | Pros | Cons | Why Rejected |
|-------------|------|------|--------------|
| {alt 1} | {pros} | {cons} | {why not} |
| {alt 2} | {pros} | {cons} | {why not} |

---

## Sub-Tasks

### Phase 1: {Name}
**Estimated effort**: {time}

- [ ] 1.1 {Sub-task}
  - Notes: {any notes}
- [ ] 1.2 {Sub-task}
  - Notes: {any notes}
- [ ] 1.3 {Sub-task}

### Phase 2: {Name}
**Estimated effort**: {time}

- [ ] 2.1 {Sub-task}
- [ ] 2.2 {Sub-task}
- [ ] 2.3 {Sub-task}

### Phase 3: Testing & Cleanup
**Estimated effort**: {time}

- [ ] 3.1 Write/update tests
- [ ] 3.2 Run full test suite
- [ ] 3.3 Code cleanup
- [ ] 3.4 Self-review

---

## Technical Details

### Files to Create
| File | Purpose |
|------|---------|
| `path/to/new/file.py` | {purpose} |

### Files to Modify
| File | Changes |
|------|---------|
| `path/to/existing/file.py` | {what changes} |

### Dependencies
- {Library/package needed}
- {Other task dependency}

### API Changes
{If applicable}

| Endpoint | Method | Change |
|----------|--------|--------|
| `/api/v1/example` | POST | New endpoint |

### Database Changes
{If applicable}

| Table | Change |
|-------|--------|
| `users` | Add column `preferences` |

---

## Testing Strategy

### Unit Tests
- [ ] Test {functionality 1}
- [ ] Test {functionality 2}
- [ ] Test edge case: {description}

### Integration Tests
- [ ] Test {integration point 1}
- [ ] Test {integration point 2}

### Manual Testing
- [ ] Verify {scenario 1}
- [ ] Verify {scenario 2}

---

## Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| {Risk 1} | {low/med/high} | {low/med/high} | {how to mitigate} |
| {Risk 2} | {low/med/high} | {low/med/high} | {how to mitigate} |

---

## Open Questions

- [ ] {Question 1}
  - Answer: {answer when known}
- [ ] {Question 2}
  - Answer: {answer when known}

---

## Checkpoints

| Checkpoint | Criteria | Completed |
|------------|----------|-----------|
| Plan approved | PM reviewed | ☐ |
| Phase 1 complete | Sub-tasks 1.x done | ☐ |
| Phase 2 complete | Sub-tasks 2.x done | ☐ |
| Tests passing | All green | ☐ |
| Self-review done | Checklist complete | ☐ |
| Ready for QA | All criteria met | ☐ |

---

## Notes

{Additional planning notes, references, etc.}
