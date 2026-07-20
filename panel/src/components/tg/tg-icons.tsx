/**
 * RoboCo's own cockpit icon set — hand-drawn duotone glyphs (a 45%-opacity
 * body plus solid accents, everything currentColor) so the nav and action
 * surfaces stop reading as stock-library linework. Utility chrome
 * (chevrons, spinners, list-row glyphs) deliberately stays lucide; these
 * cover the hero surfaces only.
 */

export type TgIconProps = { className?: string };

function Svg({
  className,
  children,
}: TgIconProps & { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      className={className}
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

/** Speedometer — Today. */
export function IconToday({ className }: TgIconProps) {
  return (
    <Svg className={className}>
      <path
        fillRule="evenodd"
        opacity=".45"
        d="M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm0 3.5a5.5 5.5 0 1 1 0 11 5.5 5.5 0 0 1 0-11Z"
      />
      <rect
        x="11.1"
        y="5.2"
        width="1.8"
        height="7.2"
        rx=".9"
        transform="rotate(45 12 12)"
      />
      <circle cx="12" cy="12" r="2" />
    </Svg>
  );
}

/** Octagon seal with a check — Approvals / approve actions. */
export function IconSeal({ className }: TgIconProps) {
  return (
    <Svg className={className}>
      <path
        opacity=".45"
        d="M8.2 2.6h7.6a1 1 0 0 1 .7.3l4.6 4.6a1 1 0 0 1 .3.7v7.6a1 1 0 0 1-.3.7l-4.6 4.6a1 1 0 0 1-.7.3H8.2a1 1 0 0 1-.7-.3l-4.6-4.6a1 1 0 0 1-.3-.7V8.2a1 1 0 0 1 .3-.7l4.6-4.6a1 1 0 0 1 .7-.3Z"
      />
      <path
        d="m8.4 12.2 2.4 2.4 4.8-5"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
    </Svg>
  );
}

/** Bell with an unread dot — Inbox. */
export function IconInbox({ className }: TgIconProps) {
  return (
    <Svg className={className}>
      <path
        opacity=".45"
        d="M12 3.2a6 6 0 0 0-6 6v3l-1.5 2.6c-.4.7.1 1.6.9 1.6h13.2c.8 0 1.3-.9.9-1.6L18 12.2v-3a6 6 0 0 0-6-6Z"
      />
      <path d="M9.7 18.6a2.4 2.4 0 0 0 4.6 0H9.7Z" />
      <circle cx="17.8" cy="5.4" r="2.6" />
    </Svg>
  );
}

/** Kanban columns — Board. */
export function IconBoard({ className }: TgIconProps) {
  return (
    <Svg className={className}>
      <rect x="3.4" y="4" width="4.8" height="12.5" rx="1.7" opacity=".45" />
      <rect x="9.6" y="4" width="4.8" height="16" rx="1.7" />
      <rect x="15.8" y="4" width="4.8" height="9" rx="1.7" opacity=".45" />
    </Svg>
  );
}

/** Speech bubble carrying the brand cursor — Chat. */
export function IconChat({ className }: TgIconProps) {
  return (
    <Svg className={className}>
      <path
        opacity=".45"
        d="M4 6.5A3.5 3.5 0 0 1 7.5 3h9A3.5 3.5 0 0 1 20 6.5v6a3.5 3.5 0 0 1-3.5 3.5H9.8l-4.1 3.4c-.7.5-1.7 0-1.7-.9V6.5Z"
      />
      <rect x="8.2" y="10.6" width="6.2" height="2.2" rx="1.1" />
    </Svg>
  );
}

/** Rising trend in a frame — Metrics. */
export function IconMetrics({ className }: TgIconProps) {
  return (
    <Svg className={className}>
      <rect x="3" y="3.6" width="18" height="16.8" rx="4.2" opacity=".45" />
      <path
        d="m6.8 14.6 3.4-3.6 2.6 2.2 4.2-5"
        stroke="currentColor"
        strokeWidth="2.1"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
      <circle cx="17.2" cy="8.1" r="1.9" />
    </Svg>
  );
}

/** Rocket — Ship. */
export function IconShip({ className }: TgIconProps) {
  return (
    <Svg className={className}>
      <path
        opacity=".45"
        d="M12 2.3c3 1.8 4.8 5 4.8 8.6 0 1.9-.4 3.7-1.2 5.2H8.4a11.7 11.7 0 0 1-1.2-5.2c0-3.6 1.8-6.8 4.8-8.6Z"
      />
      <circle cx="12" cy="9.6" r="2" />
      <path d="M10.1 17.4h3.8c.3 1.7-.4 3.4-1.9 4.8-1.5-1.4-2.2-3.1-1.9-4.8Z" />
    </Svg>
  );
}

/** Double check — Ack all. */
export function IconAckAll({ className }: TgIconProps) {
  return (
    <Svg className={className}>
      <path
        opacity=".45"
        d="m3 12.9 4 4 7.6-8.8"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
      <path
        d="m10.4 13.8 3.1 3.1 7.5-8.7"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
    </Svg>
  );
}

/** Broom — the stale-branch sweep. */
export function IconSweep({ className }: TgIconProps) {
  return (
    <Svg className={className}>
      <path
        d="M19.4 3.6 13 10.8"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        fill="none"
      />
      <path
        opacity=".45"
        d="M12.1 10.4 15 13c-.9 3.1-3.3 5.7-7 7-1.5.5-3.2-.1-3.9-1.6l-1-2c3.7-.5 6.8-2.5 9-6Z"
      />
    </Svg>
  );
}

/** Robot head — the fleet. */
export function IconFleet({ className }: TgIconProps) {
  return (
    <Svg className={className}>
      <rect x="11.1" y="2" width="1.8" height="3.4" rx=".9" />
      <rect x="4" y="6.2" width="16" height="13" rx="4.2" opacity=".45" />
      <circle cx="9" cy="12.7" r="1.8" />
      <circle cx="15" cy="12.7" r="1.8" />
    </Svg>
  );
}
