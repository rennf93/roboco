"use client";

/**
 * Hand-rolled inline-SVG charts for the cockpit — no charting library in the
 * Mini App bundle. Both are theme-driven (stroke/fill ride `currentColor`,
 * so the caller sets the hue via a text color) and degrade to a flat
 * baseline for an all-zero series rather than dividing by zero.
 */

const SPARK_W = 300;
const SPARK_H = 72;

/** Smooth-ish area sparkline with a gradient fill and an emphasized last
 * point — the hero's spend trend. `values` oldest → newest. */
export function Sparkline({ values }: { values: number[] }) {
  const n = values.length;
  const max = Math.max(...values, 0);
  const min = Math.min(...values, 0);
  const span = max - min || 1;
  const gradId = "tg-spark-grad";

  const x = (i: number) => (n <= 1 ? 0 : (i / (n - 1)) * SPARK_W);
  // Leave 6px headroom top/bottom so the stroke + endpoint dot never clip.
  const y = (v: number) => SPARK_H - 6 - ((v - min) / span) * (SPARK_H - 12);

  const points = values.map((v, i) => [x(i), y(v)] as const);
  const line = points.map(([px, py]) => `${px},${py}`).join(" ");
  const area = `M0,${SPARK_H} L${line.replace(/ /g, " L")} L${SPARK_W},${SPARK_H} Z`;
  const [lastX, lastY] = points[points.length - 1] ?? [SPARK_W, SPARK_H / 2];

  return (
    <svg
      viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
      preserveAspectRatio="none"
      className="h-16 w-full overflow-visible text-primary"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.28" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gradId})`} className="tg-backdrop" />
      <polyline
        points={line}
        pathLength={1}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
        className="tg-draw-line"
      />
      <circle cx={lastX} cy={lastY} r="3.5" fill="currentColor" />
    </svg>
  );
}

const AREA_W = 340;
const AREA_H = 128;

/**
 * Full-size area chart for drilldowns — the wallet asset-chart archetype:
 * a clean line + gradient fill with the series' min/max annotated on the
 * right edge and start/end captions underneath. No axes, no grid; the two
 * extremes plus the endpoints are the honest summary a phone needs.
 * `values` oldest → newest; `format` renders the min/max annotations.
 */
export function TgAreaChart({
  values,
  format = (v: number) => v.toFixed(2),
  startLabel,
  endLabel,
}: {
  values: number[];
  format?: (v: number) => string;
  startLabel?: string;
  endLabel?: string;
}) {
  const n = values.length;
  const max = Math.max(...values, 0);
  const min = Math.min(...values, 0);
  const span = max - min || 1;
  const gradId = "tg-area-grad";

  const x = (i: number) => (n <= 1 ? 0 : (i / (n - 1)) * AREA_W);
  const y = (v: number) => AREA_H - 8 - ((v - min) / span) * (AREA_H - 16);

  const points = values.map((v, i) => [x(i), y(v)] as const);
  const line = points.map(([px, py]) => `${px},${py}`).join(" ");
  const area = `M0,${AREA_H} L${line.replace(/ /g, " L")} L${AREA_W},${AREA_H} Z`;
  const [lastX, lastY] = points[points.length - 1] ?? [AREA_W, AREA_H / 2];

  return (
    <div>
      <div className="relative">
        <svg
          viewBox={`0 0 ${AREA_W} ${AREA_H}`}
          preserveAspectRatio="none"
          className="h-32 w-full overflow-visible text-primary"
          aria-hidden="true"
        >
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="currentColor" stopOpacity="0.26" />
              <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={area} fill={`url(#${gradId})`} className="tg-backdrop" />
          <polyline
            points={line}
            pathLength={1}
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
            className="tg-draw-line"
          />
          <circle cx={lastX} cy={lastY} r="4" fill="currentColor" />
        </svg>
        {max > min && (
          <>
            <span className="absolute right-0 top-0 text-[10px] tabular-nums text-muted-foreground/70">
              {format(max)}
            </span>
            <span className="absolute bottom-0 right-0 text-[10px] tabular-nums text-muted-foreground/70">
              {format(min)}
            </span>
          </>
        )}
      </div>
      {(startLabel || endLabel) && (
        <div className="mt-1.5 flex justify-between text-[10px] text-muted-foreground/60">
          <span>{startLabel}</span>
          <span>{endLabel}</span>
        </div>
      )}
    </div>
  );
}

/** Compact day bars — the last bar (today) emphasized in the accent, the
 * rest muted. `values` oldest → newest. */
export function DayBars({
  values,
  labels,
}: {
  values: number[];
  labels?: string[];
}) {
  const max = Math.max(...values, 1);
  return (
    <div className="flex items-end gap-1.5" aria-hidden="true">
      {values.map((v, i) => {
        const isToday = i === values.length - 1;
        const pct = Math.round((v / max) * 100);
        return (
          <div key={i} className="flex flex-1 flex-col items-center gap-1">
            <div className="flex h-14 w-full items-end">
              <div
                className={`w-full rounded-sm ${
                  isToday ? "bg-primary" : "bg-muted-foreground/25"
                }`}
                style={{ height: `${Math.max(pct, v > 0 ? 8 : 3)}%` }}
              />
            </div>
            {labels && (
              <span className="text-[9px] tabular-nums text-muted-foreground/60">
                {labels[i]}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
