# Permissions Reference

What each role can do in the system.

## Permission Levels

| Level | Roles |
|-------|-------|
| CEO | ceo, system |
| BOARD | product_owner, head_marketing |
| AUDITOR | auditor |
| MAIN_PM | main_pm |
| CELL_PM | cell_pm |
| CELL_MEMBER | developer, qa, documenter |

## Task Permissions

| Action | CEO | Board | Auditor | Main PM | Cell PM | Dev | QA | Doc |
|--------|-----|-------|---------|---------|---------|-----|----|----|
| View All | Yes | Yes | Yes | Yes | - | - | - | - |
| View Own | - | - | - | - | Yes | Yes | Yes | Yes |
| Create | Yes | Yes | Yes | Yes | Yes | - | - | - |
| Assign | Yes | Yes | Yes | Yes | Yes | - | - | - |
| Cancel | - | Yes | - | Yes | Yes | - | - | - |
| Close | Yes | Yes | Yes | Yes | Yes | Yes | - | Yes |
| Claim | - | - | - | Yes | Yes | Yes | Yes | Yes |
| Pass QA | - | - | - | - | - | - | Yes | - |
| Fail QA | - | - | - | - | - | - | Yes | - |
| Docs Complete | - | - | - | - | - | - | - | Yes |

Note: CEO and Auditor CANNOT cancel (by design - observe/approve only).

## Index Permissions

| Action | CEO | Board | Auditor | Main PM | Cell PM | Dev | QA | Doc |
|--------|-----|-------|---------|---------|---------|-----|----|----|
| Index Code | Yes | - | - | Yes | Yes | Yes | - | - |
| Index Docs | Yes | Yes | - | Yes | Yes | Yes | - | Yes |
| Search/Query | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| View Stats | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Clear Index | Yes | - | - | Yes | - | - | - | - |
| Refresh Index | Yes | - | - | Yes | - | - | - | - |

Note: Board (Product Owner, Head Marketing) can only index docs, not code.

## Notification Permissions

| Role | Can Send | Scope |
|------|----------|-------|
| ceo | Yes | All |
| product_owner | Yes | Management chain |
| head_marketing | Yes | Management chain |
| auditor | Yes | All |
| main_pm | Yes | All |
| cell_pm | Yes | Own cell |
| developer | No | - |
| qa | No | - |
| documenter | No | - |

## PM-Capable Roles

These roles can create/assign tasks:
- `ceo`
- `product_owner`
- `head_marketing`
- `main_pm`
- `cell_pm`

## Cancellation Roles

These roles can cancel tasks:
- `product_owner`
- `head_marketing`
- `main_pm`
- `cell_pm`

Note: CEO and Auditor CANNOT cancel (observe/approve only).

## View Scope

| Role | Can View |
|------|----------|
| CEO | All tasks |
| Board | All tasks |
| Auditor | All tasks (silent) |
| Main PM | All tasks |
| Cell PM | Own cell + cross-cell |
| Cell Member | Own cell |
