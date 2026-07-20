/**
 * The cockpit's icon voice — Phosphor (MIT), duotone at rest and filled
 * when active, the weight language native mobile docks use. These wrappers
 * pin the vocabulary per surface so no consumer picks weights ad hoc;
 * utility chrome (chevrons, spinners, close) stays lucide.
 */

import {
  BellSimple,
  Broom,
  ChartLineUp,
  ChatCircleDots,
  Checks,
  Gauge,
  Kanban,
  Robot,
  RocketLaunch,
  SealCheck,
} from "@phosphor-icons/react";

export type TgIconProps = {
  className?: string;
  /** Active-state rendering (the dock's selected tab): filled, not duotone. */
  filled?: boolean;
};

type PhosphorIcon = typeof BellSimple;

function wrap(Icon: PhosphorIcon) {
  function TgIcon({ className, filled = false }: TgIconProps) {
    return (
      <Icon
        className={className}
        weight={filled ? "fill" : "duotone"}
        aria-hidden="true"
      />
    );
  }
  return TgIcon;
}

/** Speedometer — Today. */
export const IconToday = wrap(Gauge);
/** Seal with a check — Approvals / approve actions. */
export const IconSeal = wrap(SealCheck);
/** Bell — Inbox (the header bell). */
export const IconInbox = wrap(BellSimple);
/** Kanban columns — Board. */
export const IconBoard = wrap(Kanban);
/** Speech bubble — Chat. */
export const IconChat = wrap(ChatCircleDots);
/** Rising trend — Metrics. */
export const IconMetrics = wrap(ChartLineUp);
/** Rocket — Ship. */
export const IconShip = wrap(RocketLaunch);
/** Double check — Ack all. */
export const IconAckAll = wrap(Checks);
/** Broom — the stale-branch sweep. */
export const IconSweep = wrap(Broom);
/** Robot head — the fleet. */
export const IconFleet = wrap(Robot);
