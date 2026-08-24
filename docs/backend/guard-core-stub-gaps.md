# guard-core 3.12.0 / fastapi-guard 7.6.0 type-stub gaps

PR #945 (`95d2525d`) fixed 9 mypy errors in `roboco/security.py` and `tests/unit/test_security.py` that blocked CI after the guard-core 3.12.0 / fastapi-guard 7.6.0 upgrade. All errors stem from the pydantic-mypy plugin and guard's type stubs not matching runtime behavior. No runtime behavior changed — only type annotations and suppressions.

## Root cause

`SecurityConfig` is a Pydantic model whose fields accept mutable collection types (`list[str]`, `dict[str, ...]`) at the type-stub level. guard-core 3.12.0 added runtime coercion of those collection fields to immutable types (`tuple`, `frozenset`, `MappingProxyType`) at construction. Two mismatch families result:

1. **Tuple/MappingProxyType vs list/dict** — RoboCo passes `tuple` and `MappingProxyType` values to `SecurityConfig` kwargs whose stubs declare `list[str]` / `dict[str, ...]`. mypy reports `arg-type`. Affects `trusted_proxies`, `whitelist`, `threat_ban_config`, `global_behavior_rules`.
2. **Missing stub attributes/kwargs** — guard's `__init__.py` does not re-export the `status` submodule (mypy reports `attr-defined` on `from guard import status`), and the `behavior_scan_response_body` kwarg exists on `SecurityConfig` at runtime but the pydantic-mypy plugin does not surface it in the generated `__init__` signature (mypy reports `call-arg`). The same plugin gap causes `attr-defined` on `behavior_scan_response_body` attribute accesses in the test file.

## Fix approach

**Errors 3-6 (type mismatches): `cast()` from `typing`.** The four kwargs whose source values are `tuple` / `MappingProxyType` are wrapped in `cast()` targeting the `list` / `dict` type mypy expects. This is preferred over `type: ignore` because it documents the expected type explicitly rather than silencing the check. The cast is a no-op at runtime — Python's `cast()` returns its second argument unchanged.

```python
# security.py — the four cast() sites (kwargs inside the SecurityConfig(...) call)
# fmt: off
trusted_proxies=cast("list[str]", ("127.0.0.1", "::1", "172.16.0.0/12")),
whitelist=cast("list[str] | None", _guard_whitelist()),
threat_ban_config=cast("dict[str, ThreatBanConfig]", _THREAT_BAN_CONFIG),
global_behavior_rules=cast("list[BehaviorRuleConfig]", _BEHAVIOR_RULES),
# fmt: on
```

**Errors 1-2, 7-9 (missing stub attributes/kwargs): `type: ignore` with waivers.** Five sites where the stub gap cannot be bridged with a cast (the attribute or kwarg simply does not exist in the stubs) carry `type: ignore` comments, each waived in `.roboco/conventions.yml` under `no_lint_suppressions`:

| File | Line | Code | Reason |
|------|------|------|--------|
| `roboco/security.py` | 25 | `attr-defined` | `guard/status.py` exists in guard 7.6.0 but `guard.__init__` does not re-export it; Python's submodule import machinery resolves it at runtime. |
| `roboco/security.py` | 848 | `call-arg` | `behavior_scan_response_body` kwarg exists on `SecurityConfig` at runtime (Pydantic `model_fields` confirms `bool`) but the pydantic-mypy plugin does not surface it in the generated `__init__`. |
| `tests/unit/test_security.py` | 381 | `unreachable` | guard-core 3.12.0 coerces collection fields to immutable types at construction, so in-place `.append()` raises `AttributeError` at runtime, but mypy infers the `pytest.raises` body is unreachable because the stub types report `list`. |
| `tests/unit/test_security.py` | 409 | `attr-defined` | Same `behavior_scan_response_body` plugin gap as security.py:848. |
| `tests/unit/test_security.py` | 416 | `attr-defined` | Same as above. |

## Waivers

The two waiver entries in `.roboco/conventions.yml` (paths `roboco/security.py` and `tests/unit/test_security.py`, rule `no_lint_suppressions`) document each suppression with its root cause. They follow the same pattern as the existing waivers for `prompter_live.py` and `lifecycle.py`.

## Stale-venv caveat

The workspace venv carries guard 7.3.1 / guard-core 3.7.0 (stale), which lacks `guard/status.py` and does not coerce collection fields. CI installs the pinned `>=7.6.0` / `>=3.12.0`. The `type: ignore[attr-defined]` on the `guard.status` import is correct for CI; the import fails locally against 7.3.1 but works on CI with 7.6.0+. The `/app/.venv` (used by the orchestrator runtime) carries guard 7.6.0 and confirms both `guard/status.py` and `add_status_route` exist there.

## When these suppressions can be removed

All five `type: ignore` suppressions and the four `cast()` calls resolve when guard-core / fastapi-guard ship matching type stubs — i.e., when the pydantic-mypy plugin surfaces `behavior_scan_response_body` in the `SecurityConfig.__init__` signature and `guard.__init__` re-exports the `status` submodule. Until then, these are upstream stub gaps, not silenced debt.