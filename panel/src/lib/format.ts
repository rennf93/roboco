// Shared number/time formatters for usage & metrics charts — was
// reimplemented per-chart (inconsistent K/M rounding caused "22596k"-style
// ticks); one copy so every chart humanizes the same way.

/** Format token counts with a K/M suffix — M keeps 2 decimals, K keeps 1. */
export function formatTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

const _TWO_HOURS_MS = 2 * 60 * 60 * 1000;

/** Whether a time-bucket series is hourly or daily, from the smallest gap
 * between consecutive buckets — hourly buckets sit ~1h apart, daily ~24h.
 * Derived from the data (not the timestamp's string shape or the viewer's
 * timezone), so it can't misread a midnight-UTC daily bucket as an hour. */
export function bucketGranularity(buckets: string[]): "hour" | "day" {
  if (buckets.length < 2) return "hour";
  const times = buckets.map((b) => new Date(b).getTime()).sort((a, b) => a - b);
  let minGap = Infinity;
  for (let i = 1; i < times.length; i++) {
    minGap = Math.min(minGap, times[i] - times[i - 1]);
  }
  return minGap > _TWO_HOURS_MS ? "day" : "hour";
}

/** Format one time-bucket: "HH:00" for hourly (viewer-local), a short UTC
 * date ("Jul 15") for daily. Daily buckets are midnight UTC, so render them in
 * UTC — otherwise a non-UTC viewer sees the previous day or a bare "02:00". */
export function formatBucket(
  bucket: string,
  granularity: "hour" | "day",
): string {
  const d = new Date(bucket);
  if (granularity === "day") {
    return d.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    });
  }
  return d.getHours().toString().padStart(2, "0") + ":00";
}
