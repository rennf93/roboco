# CI Fix: Mypy Type Narrowing in Telegram InitData Test

**Run 29629255153 — Resolved**

## Root Cause
CI run 29629255153 failed on the `mypy` quality gate, not the historical pydantic-settings issue. The test file's `__main__` self-check block at `tests/unit/utils/test_telegram_initdata.py:122` was indexing the return value of `validate_init_data()` (which returns `dict[str, object] | None`) without first narrowing away the `None` type.

## Solution Applied
Narrowed the `None` possibility before indexing:
- Assign the result to a variable
- Assert it is not `None`
- Then index the dict

This mirrors the existing safe-indexing pattern at line 41 of the same test file.

## Impact
- **Scope:** Test infrastructure only (no production code modified)
- **Risk:** Minimal — single-line change in a self-check block
- **Behavior:** No change to the module under test
- **Verification:** Local `make quality` run confirmed full pass (mypy clean, 13,383 tests passed, 94.46% coverage)

## Pattern
For future test self-check blocks that call functions returning `T | None`, always narrow before indexing:
```python
result = some_function_returning_optional()
assert result is not None
# now safe to access result["key"]
```
