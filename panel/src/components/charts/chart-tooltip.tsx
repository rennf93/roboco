import type { CSSProperties } from "react";

// Shared recharts <Tooltip> styling — a bare `contentStyle={{fontSize:12}}`
// renders recharts' default white content box with the default (also-white)
// label text, so on the dark theme the tooltip is a white box with an
// invisible date header. Pull both colors from the popover CSS vars instead.
export const chartTooltipStyle: {
  contentStyle: CSSProperties;
  labelStyle: CSSProperties;
} = {
  contentStyle: {
    backgroundColor: "var(--popover)",
    color: "var(--popover-foreground)",
    border: "1px solid var(--border)",
    fontSize: 12,
  },
  labelStyle: { color: "var(--popover-foreground)" },
};
