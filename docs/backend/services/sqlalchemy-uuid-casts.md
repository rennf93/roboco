# SQLAlchemy `Mapped[UUID]` cast convention

When casting SQLAlchemy `Mapped[UUID]` primary-key columns to the runtime `uuid.UUID` type for typing purposes, use the string-literal form:

```python
cast('UUID', child.id)
```

not the runtime symbol form:

```python
cast(UUID, child.id)  # noqa: TC006
```

## Why

- `Mapped[UUID]` resolves to `uuid.UUID` at runtime, but static checkers need the cast target.
- The string-literal form avoids importing `UUID` solely to pass it to `typing.cast`, which ruff's `TC006` rule flags as a typing-only import used at runtime.
- It also avoids `# noqa` or `# type: ignore` suppressions.

## Where we use it

- `roboco/services/task.py:_supersede_replacement_landed` — descendant traversal.
- `roboco/services/task.py:get_all_descendants` — descendant traversal.
