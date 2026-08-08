# Delivery lead-time metric scoping

## What changed

`TaskService.get_delivery_stats_30d` (`roboco/services/task.py`, called by `CockpitService.summary` and rendered by the panel's `company-scorecard-card.tsx` Speed/Delivery sections) used to compute `completed_30d` and `median_lead_time_hours` over every `status=completed` task in the last 30 days, with no filter on task type, source, or tree position. That population mixed real delivery work with rows that carry no real delivery lead time:

- Held CEO-approval drafts (X posts/replies/feature drafts, video-post drafts, release proposals) that complete the instant the CEO approves them, seconds after being drafted.
- Board-program exploration cycles and generic administrative tasks (`task_type=administrative`), which complete the moment a Board role files its proposal, not when anything ships.
- Parent and child rows for the same piece of work (a Main-PM coordination root plus its own cell tasks and dev subtasks), double- and triple-counting a single delivery.

The fix adds three predicates to the existing `status=completed AND completed_at IS NOT NULL AND completed_at >= now()-30d` filter:

1. `TaskTable.parent_task_id.is_(None)` — one row per delivery root. For a single-task delivery that root is the coordination root itself; for a MegaTask that root is the branchless **umbrella** (its `parent_task_id` is NULL), NOT a root-subtask — each root-subtask has `parent_task_id=<umbrella id>` set, so it's a child of the umbrella and is excluded by this same predicate.
2. `TaskTable.task_type != TaskType.ADMINISTRATIVE` — covers every board-program exploration cycle (`board_roadmap`, `board_pest_control`, ...) plus generic administrative work in one filter, since every board-program explorer already stamps `task_type=ADMINISTRATIVE`.
3. `TaskTable.source.notin_(LEAD_TIME_EXCLUDED_SOURCES)` — a new frozenset constant (defined next to `EVAL_BENCH_SOURCE` in `roboco/services/task.py`) unioning `X_SOURCES`, `X_FEATURE_EXPLORATION_SOURCE`, `VIDEO_HELD_SOURCES` (the held `video_post` draft), and `RELEASE_MANAGER_SOURCE`. `VIDEO_SOURCE` (the video-authoring task itself) is deliberately NOT in this set — it's a normal dispatched UX/UI code task, not a held draft, so excluding it would drop real delivery work. A `ceo_report` (Periscope/Sentinel) is never a `TaskTable` row at all — filed as a report, not a task — so it needs no entry in the set.

`completed_30d` and `median_lead_time_hours` are computed from the same filtered row set, so the two figures returned together always describe one population — the dict schema (`{completed_30d: int, median_lead_time_hours: float | None}`) is unchanged, so `CockpitService.summary` and every other consumer need no changes.

## Where the population is documented

The method's own docstring (`roboco/services/task.py`, `get_delivery_stats_30d`) is the source of truth for the scoping rule and spells out the MegaTask umbrella-vs-root-subtask distinction. `docs/map/metrics-observability.md` (the agent-facing codebase map's Gotchas section) carries the same explanation for agents exploring the metrics slice, plus a Key Symbols entry and a "Changes Since Baseline" note dated 2026-08-08.

## UI-facing text

`panel/src/components/business/company-scorecard-card.tsx`'s Speed section tooltip and the Delivery section's "Done (30 d)" tile hint now state the population in the UI copy itself:

- Speed: "Hours from creation to completion, over root delivery tasks completed in the last 30 days — excludes held CEO-approval drafts (X posts, video posts, release proposals), administrative tasks, and board-program exploration cycles".
- Delivery: "Root delivery tasks completed in the last 30 days — same population as the Speed section's median lead time".

## Test coverage

`tests/unit/services/test_delivery_stats_scope_db.py` seeds real rows against a live Postgres session (the exclusion filters are exactly the kind of thing a mocked `session.execute` can't prove actually executes as real SQL) and asserts:

- A completed held X-post draft (`task_type=ADMINISTRATIVE`, `source=x_post`) and a completed `board_pest_control` exploration task are excluded from both `completed_30d` and the median.
- A completed held video-post draft (`task_type=CODE`, `source=video_post`) is also excluded — this seed isolates the source predicate alone, since it's the one excluded fixture NOT already caught by the `task_type != ADMINISTRATIVE` filter.
- A real delivery root (`task_type=CODE`, `source=manual`) is included, with its own lead time reported.
- A root+child pair (the child parented under the root, both completed) counts once — only the parentless root enters the population.

## Future-maintenance note

Any future held/report-drafting engine must either tag its exploration/draft task `task_type=ADMINISTRATIVE` or add its `source` to `LEAD_TIME_EXCLUDED_SOURCES`, or it will silently enter the lead-time population. This is flagged in the constant's own code comment in `roboco/services/task.py`.
