# Rule Enforcement

Validate actions against organizational standards before executing.

## Pre-Action Validation

Before taking significant actions, check for applicable rules:

```python
roboco_validate_action(
    action_type="create_endpoint",
    context="Adding POST /users endpoint with email/password"
)
```

**Returns:**

```json
{
  "status": "validated",
  "allowed": true,
  "violations": [],
  "warnings": ["Consider rate limiting for auth endpoints"],
  "relevant_standards": [
    {"rule": "Use Pydantic for request validation", "severity": "required"},
    {"rule": "Return 201 for successful creates", "severity": "recommended"}
  ]
}
```

## Getting Standards

Retrieve standards before writing code:

```python
# Get coding standards for Python
roboco_get_standards(domain="coding", language="python")

# Get security standards
roboco_get_standards(domain="security")

# Get workflow standards
roboco_get_standards(domain="workflow")
```

## Standard Domains

| Domain | Covers |
|--------|--------|
| `coding` | Style, patterns, naming, structure |
| `security` | OWASP, auth, input validation |
| `workflow` | Task lifecycle, handoffs, reviews |
| `testing` | Coverage, patterns, mocking |
| `api` | REST conventions, versioning |
| `git` | Branching, commits, PRs |

## Code Review

Get automated feedback before committing:

```python
roboco_review_code(
    code="""
def create_user(email: str, password: str):
    user = User(email=email, password=password)
    db.add(user)
    return user
""",
    file_path="api/users.py",
    change_type="add"  # add, modify, delete
)
```

**Returns:**

```json
{
  "status": "reviewed",
  "approved": false,
  "score": 65,
  "comments": [
    {
      "line": 2,
      "severity": "error",
      "message": "Password must be hashed before storage"
    },
    {
      "line": 1,
      "severity": "warning",
      "message": "Add Pydantic model for input validation"
    }
  ],
  "standards_checked": ["security-passwords", "api-validation"]
}
```

## Action Types

Common action types for validation:

| Action Type | Description |
|-------------|-------------|
| `create_endpoint` | Adding API endpoint |
| `add_dependency` | Adding package/library |
| `database_migration` | Schema changes |
| `auth_change` | Authentication/authorization |
| `env_config` | Environment configuration |
| `file_upload` | File upload handling |
| `external_api` | External API integration |

## Workflow

### Before Writing Code

```python
# 1. Get applicable standards
standards = roboco_get_standards(domain="coding", language="python")

# 2. Review the rules
for s in standards["standards"]:
    print(f"[{s['severity']}] {s['rule']}")
```

### Before Committing

```python
# 1. Validate the action
result = roboco_validate_action(
    action_type="create_endpoint",
    context=my_code
)

# 2. Check for violations
if not result["allowed"]:
    for v in result["violations"]:
        print(f"VIOLATION: {v}")
    # Fix before proceeding

# 3. Get code review
review = roboco_review_code(code=my_code, file_path="api/endpoint.py")

# 4. Address comments
if not review["approved"]:
    for c in review["comments"]:
        print(f"[{c['severity']}] Line {c['line']}: {c['message']}")
```

## Severity Levels

| Level | Meaning |
|-------|---------|
| `required` | Must follow, blocks merge |
| `recommended` | Should follow, may warn |
| `optional` | Nice to have |
| `deprecated` | Being phased out |

## Best Practices

1. **Check standards first** - Before writing, know the rules
2. **Validate before commit** - Catch issues early
3. **Review your code** - Automated feedback helps
4. **Fix violations** - Don't ignore required rules
5. **Address warnings** - They often prevent future issues
