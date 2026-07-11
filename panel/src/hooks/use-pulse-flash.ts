"use client";

import { useEffect, useState } from "react";

/**
 * True for one paint frame after `pulsedAt` changes to a non-null value, then
 * flips back to false — the consumer's own CSS `transition-duration` does the
 * actual fade-out. Shared between A2APairCard (switchboard) and
 * A2AConversationList (classic list) so both flash consistently off the same
 * `pairKey -> epoch ms` pulse map.
 *
 * Render-phase derivation (react.dev/learn/you-might-not-need-an-effect
 * #adjusting-some-state-when-a-prop-changes), not an Effect keyed on
 * `pulsedAt`: flips hot in the very same render that receives a new
 * `pulsedAt`, comparing against the last value seen. Seeded to `null` (not
 * the initial `pulsedAt`) so a component that *mounts* already carrying a
 * live pulse still flashes hot instead of looking cold.
 */
export function usePulseFlash(pulsedAt: number | null): boolean {
  const [isPulsing, setIsPulsing] = useState(false);
  const [lastSeenPulse, setLastSeenPulse] = useState<number | null>(null);
  if (pulsedAt !== lastSeenPulse) {
    setLastSeenPulse(pulsedAt);
    if (pulsedAt !== null) setIsPulsing(true);
  }

  // Flip back on the next paint frame — the async rAF callback is the
  // intended "subscribe to an external clock" use of an Effect.
  useEffect(() => {
    if (!isPulsing) return;
    const raf = requestAnimationFrame(() => setIsPulsing(false));
    return () => cancelAnimationFrame(raf);
  }, [isPulsing]);

  return isPulsing;
}
