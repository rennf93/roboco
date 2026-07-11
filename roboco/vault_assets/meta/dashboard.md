# Task board (Dataview)

Live queries over `RoboCo/Tasks/**` — requires the Dataview community plugin (already named in `.obsidian/community-plugins.json`; install it via Settings -> Community plugins if it isn't downloaded yet). Archived notes (`RoboCo/Archive/**`) are excluded.

## Open work, by status

```dataview
TABLE status, team, priority, pr AS "PR"
FROM "RoboCo/Tasks"
WHERE status != "completed" AND status != "cancelled" AND !contains(file.path, "Archive/")
SORT priority ASC, status ASC
```

## Blocked

```dataview
TABLE team, pr AS "PR"
FROM "RoboCo/Tasks"
WHERE status = "blocked" AND !contains(file.path, "Archive/")
SORT team ASC
```

## Recently completed

```dataview
TABLE team, pr AS "PR"
FROM "RoboCo/Tasks"
WHERE status = "completed" AND !contains(file.path, "Archive/")
SORT file.mtime DESC
LIMIT 20
```
