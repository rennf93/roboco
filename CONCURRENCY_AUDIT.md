# Engine Dedup Audit and Concurrency Fixes (PR #628)

## Summary

This PR audits the list-open-then-originate dedup pattern across six engines and fixes two defects:

### Fixes (with regression tests)

1. **VideoEngine.open_video_task** — Race condition fixed
   - **Why it races:** Three concurrent call sites (release hook, spotlight hook, /video/request route)
   - **Fix:** Per-occasion HeartbeatMutex wraps the dedup check + insert atomically
   - **Tests:**
     - `test_open_video_task_lock_held` — locked calls return None
     - `test_open_video_task_concurrent_calls` — concurrent calls for same occasion create only one task

2. **sequencing.py dev_task_collision_edges** — Short-circuit bug fixed
   - **Why it fails:** `if edges: return edges` drops the assignee-lane fallback whenever any surfaced pair collides
   - **Bug:** Unsurfaced siblings in same lane end up with no ordering at all
   - **Fix:** Fallback always runs, filtering pairs already covered by the analyzer
   - **Tests:**
     - `test_dev_collision_fallback_still_applies_when_another_pair_collides` — unrelated pairs still ordered
     - `test_dev_collision_fallback_covers_unsurfaced_sibling_in_surfaced_lane` — mixed-surface lane chain preserved

### Verified Correct (with regression tests documenting why)

1. **RoadmapEngine.run_cycle** — Single sequential orchestrator-loop call site → no self-race
2. **XEngine.run_cycle** — Single sequential orchestrator-loop call site → no self-race
3. **DepUpdateEngine.run_cycle** — Single sequential orchestrator-loop call site → no self-race
4. **CIWatchEngine.run_cycle** — Single sequential orchestrator-loop call site → no self-race
5. **SelfHealEngine.run_cycle** — Single sequential orchestrator-loop call site → no self-race
6. **ReleaseExecutor half-landed retry** — Version bump + changelog always atomically committed before any push
   - Test: `test_release_commit_sha_detects_a_real_half_landed_commit` — real repo test verifies committed state

Also verified:
- **sequencing.py rule 3** — All-shared batch generates no edges (correct by inspection)
  - Test: `test_all_shared_batch_with_disjoint_surfaces_generates_no_edges`

## Key Patterns for Future Work

### ✅ Safe Patterns

- **Sequential call site** — If a method has exactly one entry point and builds dedup sets before any commit, the check-then-insert is atomic within that cycle. Document the orchestrator wiring to prevent accidental second call sites.

- **Atomic database writes** — If version bump and changelog write together in one git commit before any push, a partial state is unreachable on origin. A fresh retry clone always sees the full committed state.

### ⚠️ Anti-Patterns

- **Non-atomic check-then-act** — Multiple concurrent calls can race on a shared resource without a lock
  - Fix: HeartbeatMutex (renews TTL, fail-closed on crash)

- **Conditional early returns skipping independent logic** — If multiple orderings apply independently, use filtering instead
  - Fix: Always run both mechanisms, skip pairs already covered

## Regression Test Coverage

- 9 new regression tests total
- 3 tests for VideoEngine occasion lock
- 3 tests for sequencing.py fallback
- 3 tests for verified patterns (ReleaseExecutor, rule 3, orchestrator sequentiality)

All tests confirm the concurrent/sequential assumptions and prevent regressions if these patterns are refactored.
