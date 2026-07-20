"use client";

import {
  IconBoard,
  IconChat,
  IconMetrics,
  IconSeal,
  IconToday,
  type TgIconProps,
} from "@/components/tg/tg-icons";
import { cn } from "@/lib/utils";

export type TgTab = "today" | "approvals" | "board" | "chat" | "metrics";

const TABS: ReadonlyArray<{
  id: TgTab;
  label: string;
  icon: React.ComponentType<TgIconProps>;
}> = [
  { id: "today", label: "Today", icon: IconToday },
  { id: "approvals", label: "Approvals", icon: IconSeal },
  { id: "board", label: "Board", icon: IconBoard },
  { id: "chat", label: "Chat", icon: IconChat },
  { id: "metrics", label: "Metrics", icon: IconMetrics },
];

interface TgTabBarProps {
  active: TgTab;
  onChange: (tab: TgTab) => void;
}

/**
 * The cockpit's bottom nav — a floating dock inset from the screen edges
 * (the wallet pattern), controlled by page state (not routes, unlike the
 * dashboard's BottomTabBar) since the whole Mini App lives on the single
 * `/tg` route. Inbox is not a tab: it lives behind the header bell.
 */
export function TgTabBar({ active, onChange }: TgTabBarProps) {
  return (
    <nav
      aria-label="Cockpit"
      className="fixed inset-x-0 bottom-0 z-40 mx-auto w-full max-w-[430px] px-3 pb-[max(env(safe-area-inset-bottom),0.75rem)]"
    >
      <div className="flex rounded-[26px] bg-card/90 shadow-[inset_0_1px_0_rgba(255,255,255,0.05),0_16px_40px_-16px_rgba(0,0,0,0.8)] ring-1 ring-white/[0.06] backdrop-blur-xl">
        {TABS.map((tab) => {
          const isActive = active === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              aria-current={isActive ? "page" : undefined}
              onClick={() => onChange(tab.id)}
              className={cn(
                "flex flex-1 flex-col items-center gap-0.5 pb-2 pt-1.5 text-[10px] font-medium transition-colors duration-200",
                isActive ? "text-primary" : "text-muted-foreground/70",
              )}
            >
              <span
                className={cn(
                  "flex h-7 w-12 items-center justify-center rounded-full transition-all duration-300 ease-[cubic-bezier(0.32,0.72,0,1)]",
                  isActive ? "bg-primary/12" : "bg-transparent",
                )}
              >
                <tab.icon className="h-5 w-5" />
              </span>
              {tab.label}
            </button>
          );
        })}
      </div>
    </nav>
  );
}
