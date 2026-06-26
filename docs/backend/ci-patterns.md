# Common CI Patterns and Fixes

When working on the backend, particularly with autonomous-maintenance features or when modifying core infrastructure, the CI quality gate may surface one of several recurring patterns. This guide documents the most common failures, their root causes, and how to fix them properly without suppressions.

## SQLAlchemy UUID Type Conversions

### Problem

mypy reports a type error like:

```
error: Argument "id" to "update" of "ProjectService" has incompatible type "UUID[Any]"; expected "UUID"
```

This occurs when passing a SQLAlchemy ORM field to a service method that expects a Python `uuid.UUID` object.

### Root Cause

SQLAlchemy ORM models use `UUID[Any]` as the type annotation for UUID columns:

```python
from sqlalchemy import UUID

class Project(Base):
    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
```

When you pass `project.id` directly to a service method, you're passing a SQLAlchemy `UUID[Any]` type, but the service expects a plain Python `uuid.UUID`. While they're compatible at runtime, mypy sees them as different types.

### Solution

Use the `require_uuid()` utility function from `roboco/utils/converters.py` to convert SQLAlchemy UUID fields to Python UUIDs:

```python
from roboco.utils.converters import require_uuid

# Before (type error)
updated = await service.update(project.id, update_data)

# After (type correct)
updated = await service.update(require_uuid(project.id), update_data)
```

The `require_uuid()` function:
- Returns the input as-is if it's already a Python `uuid.UUID`
- Converts a SQLAlchemy `UUID[Any]` to a Python `uuid.UUID`
- Raises `ValueError` if the input is `None` or another type

### Where to Apply It

Watch for `require_uuid()` use in these contexts:
- **Routes** passing ORM fields to service methods (most common)
- **Services** passing fetched ORM fields to other services
- **Any function signature** expecting `uuid.UUID` but receiving an ORM field

### Related Files

- `roboco/utils/converters.py` - Contains `require_uuid()` and other type converters
- `roboco/api/routes/*.py` - Common location for route-layer conversions

## Integration Test Encryption Key Setup

### Problem

Approximately 15-20 tests fail with an error like:

```
KeyError: 'ROBOCO_ENCRYPTION_KEY'
```

or during token encryption/decryption:

```
cryptography.fernet.InvalidToken
```

This happens when tests exercise the `encrypt_token()` / `decrypt_token()` paths (used for project tokens, provider tokens, LLM routing) but the test environment doesn't have `ROBOCO_ENCRYPTION_KEY` set.

### Root Cause

The `settings.encryption_key` configuration is empty by default (it defaults to `""` in `roboco/config.py`). In the **integration test environment**, many features that touch encryption are exercised but no real encryption key is available from the host environment.

This became an issue after autonomous-maintenance features added encryption calls in:
- Project token handling
- Provider credential storage
- LLM routing configuration

### Solution

Add a **session-scoped autouse fixture** to `tests/integration/conftest.py` (create the file if it doesn't exist):

```python
"""Integration-test fixtures.

Supplements the top-level conftest with integration-level concerns:

* A session-scoped autouse fixture that seeds ``settings.encryption_key``
  with a fresh Fernet key so any test that exercises encrypt_token /
  decrypt_token (project tokens, provider tokens, LLM routing) works
  without needing ROBOCO_ENCRYPTION_KEY in the environment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from cryptography.fernet import Fernet
from roboco.config import settings

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(scope="session", autouse=True)
def configure_test_encryption_key() -> Iterator[None]:
    """Seed a valid Fernet key so encryption works in integration tests.

    Restores the original (empty) value on teardown so it cannot leak into
    any non-integration test that shares the process.
    """
    original = settings.encryption_key
    settings.encryption_key = Fernet.generate_key().decode()
    yield
    settings.encryption_key = original
```

**Key details:**

- **Session scope**: Sets up once per test session, before any tests run
- **Autouse**: Applies automatically to all integration tests without explicit decoration
- **Clean teardown**: Restores the original (empty) value so the key doesn't leak into other test suites
- **Fresh key per run**: Generates a valid key each time, so tests never share a stale key

### When to Add This

You need this fixture when a new feature or change introduces encryption calls into integration tests. Common symptoms:

1. Integration tests pass locally (you have `ROBOCO_ENCRYPTION_KEY`) but fail in CI
2. Tests fail specifically in `test_project_service`, `test_llm_routing`, `test_provider_*` modules
3. The error is `cryptography.fernet.InvalidToken` or a missing-key error

### Related Modules

These tests often need the fixture:
- `tests/integration/services/test_project_service.py`
- `tests/integration/api/test_provider_routes.py`
- `tests/integration/services/test_provider_service.py`
- `tests/integration/services/test_llm_routing.py`

## Git Tag Handling in Workspace Clones

### Problem

A test like `test_gather_snapshot_reads_the_real_repo` fails with:

```
AssertionError: expected version "0.1.0", got "unknown"
```

or in the logs:

```
fatal: No names found, cannot describe anything.
```

This occurs when code calls `git describe --tags` to get the latest version tag, but the repository clone has no tags.

### Root Cause

Agent workspace clones are created with `git clone --depth <N>` or without tags to reduce bandwidth and startup time. When code relies on `git describe --tags` to find the latest version tag (common in release readiness assessment), it returns empty if no tags are present locally.

This is a real issue for:
- **Release readiness assessment** (`_last_tag()` in `release_readiness.py`)
- **Version detection** in deployment scripts
- **Changelog generation** that references the last release

### Solution

Add a **fallback fetch** in functions that call `git describe --tags`. In `roboco/services/release_readiness.py`, the `_last_tag()` function should:

1. First try `git describe --tags --abbrev=0` (fast, works if tags are present)
2. If it returns empty, silently fetch tags from the remote with `--quiet`
3. Try the describe again
4. Return `None` if still empty

```python
def _last_tag(root: Path) -> str | None:
    tag = _run_git(root, ["describe", "--tags", "--abbrev=0"]).strip()
    if not tag:
        # Tags may not be present in shallow clones or agent workspace clones.
        # Attempt a silent fetch; if the remote is unreachable the second describe
        # still returns empty and we legitimately return None.
        _run_git(root, ["fetch", "--tags", "--quiet", "origin"])
        tag = _run_git(root, ["describe", "--tags", "--abbrev=0"]).strip()
    return tag or None
```

**Key details:**

- **Silent fetch**: `--quiet` suppresses output so this doesn't pollute logs
- **Graceful degradation**: If `origin` is unreachable, the fetch fails silently (because `_run_git` uses `check=False`), and the function returns `None` — correct behavior
- **Workspace-safe**: Makes the function robust to shallow/limited clones while preserving correctness for full clones

### When to Apply This

You need this pattern when:

1. A feature adds code that calls `git describe --tags` or similar
2. Tests fail in the agent workspace but pass locally (where you likely have a full clone with tags)
3. The failure is version detection or release detection

### Related Patterns

Other git commands that may need similar fallbacks:

- `git describe --tags` → needs `fetch --tags` fallback
- `git rev-list --tags --max-count=1` → needs tags to be present
- `git log --oneline` on a shallow clone → may need `git fetch --deepen` fallback

## Debugging CI Failures Locally

### Quick Checks

Before submitting a fix, verify locally:

```bash
# Run the full quality gate
make quality

# Or individual gates
uv run ruff check .
uv run ruff format --check .
uv run mypy roboco/
uv run pytest
```

### Common Patterns in Logs

| Log snippet | Likely cause |
|---|---|
| `error: Argument "id"... has incompatible type` | Missing `require_uuid()` wrapper |
| `KeyError: 'ROBOCO_ENCRYPTION_KEY'` or `InvalidToken` | Missing integration test encryption fixture |
| `fatal: No names found, cannot describe anything` | Missing git tag fallback in release code |

### Testing the Patterns

To verify your fixes don't regress:

```bash
# Test type checking catches UUID mismatches
uv run mypy roboco/api/routes/project.py

# Test integration tests have encryption
uv run pytest tests/integration/services/test_project_service.py -v

# Test release code handles missing tags
uv run pytest tests/integration/services/test_release_readiness.py::test_gather_snapshot_reads_the_real_repo -v
```

## See Also

- [roboco/utils/converters.py](../../roboco/utils/converters.py) - Type converter utilities
- [roboco/services/release_readiness.py](../../roboco/services/release_readiness.py) - Release readiness engine
- [tests/integration/conftest.py](../../tests/integration/conftest.py) - Integration test fixtures
- [Common issues](../troubleshooting/common-issues.md) - Broader troubleshooting guide
