// Shared number/time formatters for usage & metrics charts — was
// reimplemented per-chart (inconsistent K/M rounding caused "22596k"-style
// ticks); one copy so every chart humanizes the same way.

/** Format token counts with a K/M suffix — M keeps 2 decimals, K keeps 1. */
export function formatTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

/** Format a usage time-bucket ISO string as "HH:00" (hourly) or "MM/DD" (daily). */
export function formatBucket(bucket: string): string {
  const d = new Date(bucket);
  // If the bucket has a non-zero time component it is an hourly bucket → show HH:00.
  // Otherwise it is a daily bucket → show MM/DD.
  const isHourly =
    d.getMinutes() === 0 && (d.getHours() !== 0 || bucket.includes("T"));
  if (isHourly && d.getSeconds() === 0 && !bucket.endsWith("T00:00:00.000Z")) {
    return d.getHours().toString().padStart(2, "0") + ":00";
  }
  return d.getMonth() + 1 + "/" + d.getDate();
}
