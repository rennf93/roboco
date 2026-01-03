# Agent UUIDs Reference

**ALWAYS use SLUGS when assigning tasks.** The system resolves slugs to UUIDs automatically.

```python
# CORRECT - Use slug
roboco_task_create(assigned_to="be-dev-1", ...)

# WRONG - Don't construct UUIDs manually
roboco_task_create(assigned_to="00000000-0000-0000-0001-000000000001", ...)
```

## UUID Scheme (Reference Only)

```
00000000-0000-0000-{CELL}-00000000000{N}
```

| Cell Code | Team |
|-----------|------|
| `0000` | CEO |
| `0001` | Backend |
| `0002` | Frontend |
| `0003` | UX/UI |
| `0004` | Board/Management |

**NO OTHER CELL CODES EXIST.** Do not construct UUIDs with codes like `0005`, `0006`, etc.

## Backend Cell (0001)

| Slug | UUID |
|------|------|
| `be-dev-1` | `00000000-0000-0000-0001-000000000001` |
| `be-dev-2` | `00000000-0000-0000-0001-000000000002` |
| `be-qa` | `00000000-0000-0000-0001-000000000003` |
| `be-pm` | `00000000-0000-0000-0001-000000000004` |
| `be-doc` | `00000000-0000-0000-0001-000000000005` |

## Frontend Cell (0002)

| Slug | UUID |
|------|------|
| `fe-dev-1` | `00000000-0000-0000-0002-000000000001` |
| `fe-dev-2` | `00000000-0000-0000-0002-000000000002` |
| `fe-qa` | `00000000-0000-0000-0002-000000000003` |
| `fe-pm` | `00000000-0000-0000-0002-000000000004` |
| `fe-doc` | `00000000-0000-0000-0002-000000000005` |

## UX/UI Cell (0003)

| Slug | UUID |
|------|------|
| `ux-dev-1` | `00000000-0000-0000-0003-000000000001` |
| `ux-dev-2` | `00000000-0000-0000-0003-000000000002` |
| `ux-qa` | `00000000-0000-0000-0003-000000000003` |
| `ux-pm` | `00000000-0000-0000-0003-000000000004` |
| `ux-doc` | `00000000-0000-0000-0003-000000000005` |

## Board/Management (0004)

| Slug | UUID |
|------|------|
| `main-pm` | `00000000-0000-0000-0004-000000000001` |
| `product-owner` | `00000000-0000-0000-0004-000000000002` |
| `head-marketing` | `00000000-0000-0000-0004-000000000003` |
| `auditor` | `00000000-0000-0000-0004-000000000004` |

## CEO (0000)

| Slug | UUID |
|------|------|
| `ceo` | `00000000-0000-0000-0000-000000000001` |

## Usage

Most tools accept either slug or UUID:
```python
roboco_task_claim(task_id)  # task_id is UUID
roboco_journal_read_team("be-dev-1")  # slug works
roboco_journal_read_team("00000000-0000-0000-0001-000000000001")  # UUID works
```
