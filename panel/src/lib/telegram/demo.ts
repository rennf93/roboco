/**
 * Dev-only demo switch for the Mini App: `/tg?demo=1` makes the cockpit's
 * queries return canned fixtures (see demo-data.ts, loaded dynamically so
 * production bundles never carry it) instead of hitting the backend —
 * lets the UI be seen and styled with zero stack running. Mutations still
 * go to the real API and will fail loudly; demo mode is a showroom, not a
 * simulator. The NODE_ENV check folds to false in production builds, so
 * the whole branch (and the fixture chunk) is dead-code-eliminated.
 */
export function isTgDemoMode(): boolean {
  return (
    process.env.NODE_ENV === "development" &&
    typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).has("demo")
  );
}
