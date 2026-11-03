# Frontend open-blocker audit (2026-11-03)

Covered directly: abaf1335, 61b2b7fb, 4f6e76ec. Not readable by the auditing developer (evidence is assignee-only): d6a26717, d42e53ea, c3a63a00, 86b799b8, 7e551634, 86ed0eab, dfeb2d8a, 69713246.

## Live blockers, oldest first

1. abaf1335, finding 258dd981, raised 2026-11-03T04:17Z, owner fe-dev-1. Cause is the task's own diff: regenerate-artifacts commit 608b5090 drifted generated files. Next step: restore those files to base.
2. 4f6e76ec, finding efe69869, raised 2026-11-03T04:37Z, owner fe-dev-1. Same cause (commit 1a8a8d3c). Next step: revert the generated files to base.

61b2b7fb has only major findings (60ce65cf, 647480dd) with the same cause.

## Red base flag

d6a26717 blocker 3aeaa180 (head ab1e81bc unchanged, CI red on Python quality gate and Entire Gates with no Python in the diff) is probably a red integration base. Unconfirmed. Route to main-pm.

## Stale blockers

None found among readable tasks. No developer verb closes findings, so any stale one is unclosable by the developer.

## Process root cause

The regenerate-artifacts step drifts generated files from trunk in every affected PR.
