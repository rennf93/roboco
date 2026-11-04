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

## Why it was flaky

The flaky test was rAF starvation under parallel vitest workers: `requestAnimationFrame` and `performance.now()` stall when many workers compete for CPU, so a real-time `waitFor` can time out even though the hook is correct.
