# Testing `useCountUp` (rAF timing) deterministically

Task 8470a856, PR #1421 (follow-up to PR #1417, AI routing card). Test-only change; no runtime behavior changed.

## What changed

`panel/src/components/tg/__tests__/motion.test.tsx`:

- The test "settles on the exact target when animating" used `waitFor` with the default 4s timeout. `useCountUp` runs on `requestAnimationFrame` and `performance.now()`, which starve when vitest runs many workers in parallel, so the test timed out under load and passed when run alone.
- It now calls `vi.useFakeTimers()`, advances time with `act(() => vi.advanceTimersByTime(500))`, asserts the hook returns exactly `12.34`, and restores real timers in a `finally` block.
- The test "counts up on the frame clock even when it lags performance.now()" is kept unchanged. It drives frames by hand, a scenario fake timers cannot cover.
- Imports were consolidated so `act` comes from the same `@testing-library/react` import as `render`, `screen`, `fireEvent` and `waitFor`.

## Guidance

- For hooks driven by rAF, prefer fake timers plus `act(advanceTimersByTime)` over `waitFor`. Always restore real timers in `finally`.
- Keep the hand-driven frame test when the behavior under test is frame-clock vs wall-clock divergence.

## Status and caveats

- The cause of the red `ci` check on PR #1417 is **unconfirmed**. The developer had no `gh` access and never read the CI log. The flaky test is a local-reproduction hypothesis. If `ci` stays red, the failing job log is needed.
- `ai-routing-card.tsx` is untouched; its tests (including Kimi) pass.
- `prettier --check` also flags `playbook-review-queue.tsx` and `tg-approvals-tab.test.tsx`. These are base files outside this PR, and the Makefile `panel-gate` has no prettier step.
