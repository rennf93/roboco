"use client";

import { useCallback, useSyncExternalStore } from "react";

const DEFAULT_BREAKPOINT_PX = 768; // Tailwind `md`

/**
 * True below `breakpointPx` (default the Tailwind `md` breakpoint).
 * `useSyncExternalStore`'s server snapshot (`false`, desktop) is also what
 * React uses for the client's first render before hydration commits — so SSR
 * and the initial hydration pass render identical markup, and the real
 * matchMedia value only takes over a tick later. No manual
 * useState/useEffect pairing, so there's nothing to cascade-render.
 */
export function useIsMobile(breakpointPx: number = DEFAULT_BREAKPOINT_PX) {
  const query = `(max-width: ${breakpointPx - 1}px)`;
  // Memoized per query: a new subscribe identity each render would make
  // useSyncExternalStore tear down + re-attach the matchMedia listener on
  // every render of every consumer.
  const subscribe = useCallback(
    (onChange: () => void) => {
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false,
  );
}
