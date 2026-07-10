# Task board (Dataview)

Live queries over `RoboCo/Tasks/**` — requires the Dataview community plugin
(already named in `.obsidian/community-plugins.json`; install it via
Settings -> Community plugins if it isn't downloaded yet).

## Open work, by status

```dataview
TABLE status, team, priority, pr AS "PR"
FROM "RoboCo/Tasks"
WHERE status != "completed" AND status != "cancelled"
SORT priority ASC, status ASC
```

## Blocked

```dataview
TABLE team, pr AS "PR"
FROM "RoboCo/Tasks"
WHERE status = "blocked"
SORT team ASC
```

## Recently completed

```dataview
TABLE team, pr AS "PR"
FROM "RoboCo/Tasks"
WHERE status = "completed"
SORT file.mtime DESC
LIMIT 20
```
