# Code Review Guidelines

Standards for conducting effective code reviews in the RoboCo system.

---

## Table of Contents

1. [Review Philosophy](#review-philosophy)
2. [Reviewer Responsibilities](#reviewer-responsibilities)
3. [Author Responsibilities](#author-responsibilities)
4. [Review Checklist](#review-checklist)
5. [Feedback Guidelines](#feedback-guidelines)
6. [Severity Classification](#severity-classification)
7. [Common Issues](#common-issues)
8. [Automated Checks](#automated-checks)

---

## Review Philosophy

### CR-001: Purpose of Code Review

**Goals:**
1. **Catch bugs** - Find defects before they reach production
2. **Maintain quality** - Ensure code meets standards
3. **Share knowledge** - Spread understanding across team
4. **Improve design** - Identify better approaches
5. **Ensure consistency** - Keep codebase uniform

**NOT goals:**
- Demonstrate superiority
- Nitpick style (that's what linters are for)
- Rewrite someone's code
- Block progress indefinitely

### CR-002: Review Mindset

**As Reviewer:**
- Assume the author did their best
- Ask questions before making assumptions
- Explain the "why" behind suggestions
- Be constructive, not destructive
- Praise good work

**As Author:**
- Code review is about the code, not you
- Every suggestion is an opportunity to learn
- Explain your reasoning when disagreeing
- Thank reviewers for their time

---

## Reviewer Responsibilities

### CR-010: Response Time

| Priority | First Response | Full Review |
|----------|---------------|-------------|
| Urgent (blocker fix) | < 2 hours | < 4 hours |
| Normal | < 4 hours | < 1 day |
| Low (refactor, docs) | < 1 day | < 2 days |

### CR-011: Review Depth

**Quick Pass (5 min):**
- Does the PR description make sense?
- Are tests included?
- Does CI pass?

**Thorough Review (30+ min):**
- Understand the full context
- Check logic and edge cases
- Verify tests cover scenarios
- Review documentation updates

### CR-012: What to Review

| Must Review | Should Review | Don't Review |
|-------------|---------------|--------------|
| Logic correctness | Code style | Auto-generated code |
| Error handling | Performance | Formatting (linter handles) |
| Security concerns | Naming | Import order (linter handles) |
| Test coverage | Documentation | |
| API contracts | Code organization | |

---

## Author Responsibilities

### CR-020: Before Requesting Review

**Pre-submission Checklist:**

```markdown
- [ ] All automated checks pass (lint, type check, tests)
- [ ] Self-reviewed the diff
- [ ] PR description explains the change
- [ ] Tests cover new functionality
- [ ] Documentation updated if needed
- [ ] No debugging code left in
- [ ] No unrelated changes included
- [ ] Commit history is clean
```

### CR-021: Writing Good PR Descriptions

**Template:**

```markdown
## Summary
Brief description of what this PR does.

## Changes
- Added X functionality
- Modified Y behavior
- Removed deprecated Z

## Testing
How to test this change:
1. Step one
2. Step two
3. Expected result

## Related
- Task: TASK-123
- Related PR: #456
```

### CR-022: Keeping PRs Small

**Size Guidelines:**

| Lines Changed | Classification | Review Time |
|---------------|----------------|-------------|
| < 100 | Small | 15 min |
| 100-300 | Medium | 30 min |
| 300-500 | Large | 1 hour |
| > 500 | Too Large | Split it! |

**How to Split Large PRs:**

1. **By layer**: API → Service → Repository
2. **By feature**: Core logic → Edge cases → Polish
3. **By concern**: Main feature → Tests → Docs

---

## Review Checklist

### CR-030: Functionality

```markdown
- [ ] Code does what PR description says
- [ ] Edge cases are handled
- [ ] Error conditions are handled gracefully
- [ ] No obvious bugs or logic errors
- [ ] Performance is acceptable for use case
```

### CR-031: Code Quality

```markdown
- [ ] Follows project coding standards
- [ ] No code duplication (DRY)
- [ ] Functions/classes have single responsibility
- [ ] Naming is clear and consistent
- [ ] Comments explain "why", not "what"
- [ ] No dead code or TODOs without context
```

### CR-032: Security

```markdown
- [ ] No hardcoded secrets or credentials
- [ ] User input is validated and sanitized
- [ ] SQL queries use parameterized statements
- [ ] No command injection vulnerabilities
- [ ] Sensitive data is not logged
- [ ] Access control is properly enforced
```

### CR-033: Testing

```markdown
- [ ] New code has tests
- [ ] Tests cover happy path
- [ ] Tests cover error cases
- [ ] Tests are readable and maintainable
- [ ] No tests skipped without reason
- [ ] Mocking is appropriate (not excessive)
```

### CR-034: API Design

```markdown
- [ ] API is intuitive and consistent
- [ ] Breaking changes are noted
- [ ] Error responses are informative
- [ ] Documentation is updated
- [ ] Backwards compatibility maintained
```

### CR-035: Data Handling

```markdown
- [ ] Database migrations are reversible
- [ ] Indexes are used appropriately
- [ ] No N+1 query issues
- [ ] Large data sets are handled efficiently
- [ ] Transactions are used correctly
```

---

## Feedback Guidelines

### CR-040: How to Give Feedback

**Structure:**

```markdown
[Severity]: [Issue]

[Context/Reason]

[Suggestion if applicable]
```

**Examples:**

```markdown
# Good feedback
BLOCKER: This SQL query is vulnerable to injection

The user input is concatenated directly. An attacker could
extract all data with: `'; DROP TABLE users; --`

Suggestion: Use parameterized queries:
```python
await db.execute("SELECT * FROM users WHERE id = :id", {"id": user_id})
```

# Bad feedback
"This is wrong."
```

### CR-041: Severity Prefixes

Use prefixes to indicate urgency:

| Prefix | Meaning | Action |
|--------|---------|--------|
| `BLOCKER:` | Must fix, security/correctness issue | Cannot merge |
| `MAJOR:` | Should fix, significant concern | Should address |
| `MINOR:` | Nice to fix, improvement | Can defer |
| `NIT:` | Nitpick, style preference | Optional |
| `QUESTION:` | Need clarification | Explain |
| `PRAISE:` | Good work | Keep doing this! |

### CR-042: Types of Comments

**Actionable:**
```markdown
MAJOR: This function modifies its input parameter, which can cause
unexpected behavior for callers. Consider returning a new object instead.
```

**Question (non-blocking):**
```markdown
QUESTION: Is this timeout intentionally set to 5 minutes? Seems long
for a health check.
```

**Suggestion (optional):**
```markdown
NIT: Could use list comprehension here for readability:
`[x.name for x in items if x.active]`
```

**Praise:**
```markdown
PRAISE: Great error handling here! The retry logic with backoff
is exactly what we need for this external API.
```

### CR-043: What NOT to Do

**Avoid:**
- Personal attacks: "Who wrote this garbage?"
- Vague criticism: "This is confusing"
- Style debates: "I prefer X" (unless it violates standards)
- Demands without explanation: "Change this"
- Blocking for non-issues: Minor style preferences

---

## Severity Classification

### CR-050: Severity Definitions

| Severity | Definition | Examples |
|----------|------------|----------|
| **BLOCKER** | Security vulnerability, data loss risk, breaks build | SQL injection, missing auth check, crashes |
| **MAJOR** | Significant bug, performance issue, design flaw | Logic error, N+1 queries, missing validation |
| **MINOR** | Improvement opportunity, minor bug | Better naming, missing edge case, documentation |
| **NIT** | Style preference, optional enhancement | Alternative approach, formatting preference |

### CR-051: Blocking vs Non-Blocking

**Block merge for:**
- Security vulnerabilities (any severity)
- Logic errors that affect functionality
- Missing tests for critical paths
- Breaking API changes without migration
- Violations of ERROR-level coding standards

**Don't block merge for:**
- Style preferences covered by linters
- "I would have done it differently"
- Missing documentation (unless API change)
- Code that works but isn't "perfect"
- Minor optimizations

---

## Common Issues

### CR-060: Logic Issues

**Missing null checks:**
```python
# Bad
user.name.lower()  # What if user is None?

# Good
if user and user.name:
    user.name.lower()
```

**Off-by-one errors:**
```python
# Bad
for i in range(len(items) + 1):  # IndexError on last iteration
    items[i]

# Good
for i in range(len(items)):
    items[i]
```

**Race conditions:**
```python
# Bad
if task.status == "pending":
    # Another process could change status here!
    task.status = "claimed"
    db.save(task)

# Good - Use atomic operations
await db.execute(
    "UPDATE tasks SET status = 'claimed' WHERE id = :id AND status = 'pending'",
    {"id": task.id}
)
```

### CR-061: Performance Issues

**N+1 queries:**
```python
# Bad
tasks = await db.query(Task).all()
for task in tasks:
    owner = await db.query(User).filter_by(id=task.owner_id).first()  # N queries!

# Good
tasks = await db.query(Task).options(selectinload(Task.owner)).all()
```

**Unbounded queries:**
```python
# Bad
users = await db.query(User).all()  # Could be millions

# Good
users = await db.query(User).limit(100).all()
```

**Memory bloat:**
```python
# Bad
data = [x for x in huge_iterator]  # Loads all into memory

# Good
for x in huge_iterator:  # Process one at a time
    process(x)
```

### CR-062: Security Issues

**SQL injection:**
```python
# Bad
f"SELECT * FROM users WHERE id = '{user_input}'"

# Good
"SELECT * FROM users WHERE id = :id", {"id": user_input}
```

**Missing authorization:**
```python
# Bad
@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str) -> None:
    await db.delete_task(task_id)  # Anyone can delete any task!

# Good
@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, current_user: User = Depends()) -> None:
    task = await db.get_task(task_id)
    if task.owner_id != current_user.id:
        raise HTTPException(403, "Not authorized")
    await db.delete_task(task_id)
```

**Sensitive data exposure:**
```python
# Bad
logger.info(f"User login: {user.email}, password: {password}")

# Good
logger.info("User login", user_id=user.id)
```

---

## Automated Checks

### CR-070: Required Checks

All PRs must pass these automated checks before merge:

| Check | Tool | Purpose |
|-------|------|---------|
| Formatting | ruff format | Code style consistency |
| Linting | ruff check | Code quality issues |
| Type checking | mypy | Type safety |
| Tests | pytest | Functionality verification |
| Dead code | vulture | Remove unused code |
| Security | bandit | Security vulnerabilities |
| Complexity | xenon | Maintainability |

### CR-071: CI Pipeline

```yaml
# .github/workflows/ci.yml
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4

      - name: Format check
        run: uv run ruff format --check .

      - name: Lint
        run: uv run ruff check .

      - name: Type check
        run: uv run mypy roboco/

      - name: Tests
        run: uv run pytest --cov=roboco --cov-fail-under=80

      - name: Security scan
        run: uv run bandit -r roboco/ -ll
```

### CR-072: Pre-commit Hooks

Use pre-commit hooks to catch issues early:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: ruff-format
        name: ruff format
        entry: uv run ruff format
        language: system
        types: [python]

      - id: ruff-check
        name: ruff check
        entry: uv run ruff check --fix
        language: system
        types: [python]

      - id: mypy
        name: mypy
        entry: uv run mypy
        language: system
        types: [python]
```

---

## Quick Reference

### Review Flow

```
1. Author creates PR
   └─► Auto-checks run

2. Reviewer assigned
   └─► Quick pass (5 min)
       └─► Issues? Request changes early

3. Full review
   └─► Check functionality
   └─► Check code quality
   └─► Check security
   └─► Check tests

4. Feedback given
   └─► BLOCKER/MAJOR: Must address
   └─► MINOR/NIT: Optional

5. Author addresses feedback
   └─► Push changes
   └─► Reply to comments

6. Re-review if needed
   └─► Approve or request more changes

7. Merge
   └─► Delete branch
```

### Comment Templates

**Blocker:**
```markdown
BLOCKER: [Brief issue]

[Why this is a problem]

[How to fix it]
```

**Question:**
```markdown
QUESTION: [What you don't understand]

[Context for why you're asking]
```

**Praise:**
```markdown
PRAISE: [What's good about this]

[Why it's particularly good]
```

### Time Estimates

| PR Size | Lines | Review Time |
|---------|-------|-------------|
| XS | < 50 | 10 min |
| S | 50-100 | 15 min |
| M | 100-300 | 30 min |
| L | 300-500 | 1 hour |
| XL | > 500 | Split it! |
