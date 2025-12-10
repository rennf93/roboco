# QA Review: TASK-{ID}

> **Reviewer**: {qa agent-id}
> **Date**: YYYY-MM-DD
> **Verdict**: {PASSED / NEEDS_REVISION / BLOCKED}

---

## Review Summary

| Aspect | Status | Notes |
|--------|--------|-------|
| Functionality | ✅ / ❌ | {notes} |
| Code Quality | ✅ / ❌ | {notes} |
| Tests | ✅ / ❌ | {notes} |
| Security | ✅ / ❌ | {notes} |
| Documentation | ✅ / ❌ | {notes} |

**Overall Verdict**: {PASSED / NEEDS_REVISION}

---

## Acceptance Criteria Verification

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | {criterion from task} | ✅ / ❌ | {how verified} |
| 2 | {criterion from task} | ✅ / ❌ | {how verified} |
| 3 | {criterion from task} | ✅ / ❌ | {how verified} |

---

## Functionality Testing

### Happy Path
| Test Case | Expected | Actual | Status |
|-----------|----------|--------|--------|
| {test case 1} | {expected} | {actual} | ✅ / ❌ |
| {test case 2} | {expected} | {actual} | ✅ / ❌ |

### Edge Cases
| Test Case | Expected | Actual | Status |
|-----------|----------|--------|--------|
| {edge case 1} | {expected} | {actual} | ✅ / ❌ |
| {edge case 2} | {expected} | {actual} | ✅ / ❌ |

### Error Handling
| Scenario | Expected | Actual | Status |
|----------|----------|--------|--------|
| {error scenario 1} | {expected} | {actual} | ✅ / ❌ |
| {error scenario 2} | {expected} | {actual} | ✅ / ❌ |

---

## Code Quality Review

### Code Review Checklist
- [ ] Follows project conventions
- [ ] No code duplication
- [ ] Functions are focused (single responsibility)
- [ ] Naming is clear and consistent
- [ ] No dead code or commented-out code
- [ ] Error handling is appropriate
- [ ] Logging is adequate

### Type Safety
- [ ] All types properly defined
- [ ] No `any` types (TS) or missing hints (Python)
- [ ] Null/undefined handled properly

### Observations
{Any code quality observations}

---

## Test Coverage

### Automated Tests
| Category | Before | After | Delta |
|----------|--------|-------|-------|
| Unit Tests | X | X | +X |
| Integration Tests | X | X | +X |
| Coverage % | X% | X% | +X% |

### Test Quality
- [ ] Tests cover happy path
- [ ] Tests cover error cases
- [ ] Tests are readable
- [ ] Tests don't have false positives

### Test Output
```
{Paste test run output}
```

---

## Security Review

### Checklist
- [ ] Input validation present
- [ ] No sensitive data exposed
- [ ] Authentication/authorization correct
- [ ] No injection vulnerabilities (SQL, command, etc.)
- [ ] No XSS vulnerabilities
- [ ] Secrets not hardcoded

### Findings
{Any security observations}

---

## Performance Review

- [ ] No obvious performance issues
- [ ] Database queries optimized
- [ ] No N+1 query problems
- [ ] Appropriate caching considered

### Observations
{Any performance observations}

---

## Issues Found

### Blocking Issues

#### Issue 1: {Title}
**Severity**: {Critical / High}
**Location**: `{file:line}`

**Description**: {What's wrong}

**Steps to Reproduce**:
1. {Step 1}
2. {Step 2}

**Expected**: {What should happen}
**Actual**: {What happens}

**Suggested Fix**: {How to fix, if known}

---

#### Issue 2: {Title}
{Same format}

---

### Non-Blocking Issues

#### Issue 3: {Title}
**Severity**: {Medium / Low}
**Location**: `{file:line}`

**Description**: {What's wrong}

**Suggestion**: {How to improve}

---

## Positive Observations

{What was done well - positive feedback matters!}

- {Good thing 1}
- {Good thing 2}

---

## Suggestions for Future

{Non-blocking suggestions for improvement}

- {Suggestion 1}
- {Suggestion 2}

---

## Verdict Details

### If PASSED
- All acceptance criteria met
- No blocking issues
- Code quality acceptable
- Tests adequate
- Ready for documentation

### If NEEDS_REVISION
**Required changes before re-review**:
1. {Required change 1}
2. {Required change 2}

**Re-review scope**: {What will be re-checked}

---

## Review Log

| Date | Action | Reviewer |
|------|--------|----------|
| YYYY-MM-DD | Initial review | {agent} |
| YYYY-MM-DD | Re-review after fixes | {agent} |
