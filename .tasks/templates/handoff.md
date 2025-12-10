# Documentation Handoff: TASK-{ID}

> **From**: {developer agent-id}
> **To**: Documenter
> **Date**: YYYY-MM-DD

---

## Summary

{Plain language description of what was built - 2-3 sentences}

## What Changed

### New Functionality
- {New feature/capability 1}
- {New feature/capability 2}

### Modified Behavior
- {Changed behavior 1}
- {Changed behavior 2}

### Breaking Changes
- {Breaking change 1} (if any)
- None

---

## Documentation Needed

### Required
- [ ] {Doc type 1}: {brief description}
- [ ] {Doc type 2}: {brief description}
- [ ] Changelog entry

### Optional
- [ ] {Additional doc if useful}

---

## Key Commits

| Commit | Description | Key Files |
|--------|-------------|-----------|
| {hash} | {description} | {files} |
| {hash} | {description} | {files} |
| {hash} | {description} | {files} |

---

## Code Locations

### New Files
| File | Purpose |
|------|---------|
| `path/to/file.py` | {what it does} |

### Modified Files
| File | What Changed |
|------|--------------|
| `path/to/file.py` | {what changed} |

---

## API Documentation

{If applicable - provide details for API docs}

### New Endpoints

#### `{METHOD} /api/v1/{path}`

**Description**: {what it does}

**Authentication**: {auth requirements}

**Request**:
```json
{
  "field": "type - description"
}
```

**Response**:
```json
{
  "field": "type - description"
}
```

**Errors**:
| Code | Description |
|------|-------------|
| 400 | {when} |
| 401 | {when} |

---

## Usage Examples

{Code examples the documenter should include}

### Example 1: {Use Case}

```python
# Example code
from module import feature

result = feature.do_thing(param)
```

### Example 2: {Use Case}

```python
# Another example
```

---

## Important Conversations

{Links to important discussions that provide context}

| Message/Thread | Topic | Key Insight |
|----------------|-------|-------------|
| {link/reference} | {topic} | {what's important} |

---

## Gotchas & Warnings

{Things the documenter should highlight in docs}

1. **{Gotcha 1}**: {explanation}
2. **{Gotcha 2}**: {explanation}

---

## Related Documentation

{Existing docs that may need updates}

- `docs/path/to/related.md` - may need {update type}
- `README.md` - {if needs update}

---

## Changelog Entry

Suggested changelog entry:

```markdown
## [{version}] - YYYY-MM-DD

### Added
- {New feature description} (#TASK-{ID})

### Changed
- {Changed behavior} (#TASK-{ID})

### Fixed
- {Bug fix if applicable} (#TASK-{ID})
```

---

## Questions for Documenter

{Any clarifying questions the dev wants to raise}

1. {Question 1}
2. {Question 2}

---

## Dev's Journey Notes

For full context, see: [journal.md](journal.md)

### Key Learnings Worth Documenting
- {Learning that users/devs should know}
- {Pattern that's reusable}

### Decisions Worth Explaining
- **{Decision}**: {Why - this helps users understand the design}
