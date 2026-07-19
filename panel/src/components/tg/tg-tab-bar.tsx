"use client";

import {
  IconBoard,
  IconChat,
  IconInbox,
  IconSeal,
  IconToday,
  type TgIconProps,
} from "@/components/tg/tg-icons";
import { cn } from "@/lib/utils";

export type TgTab = "today" | "approvals" | "inbox" | "board" | "chat";

const TABS: ReadonlyArray<{
  id: TgTab;
  label: string;
  icon: React.ComponentType<TgIconProps>;
}> = [
  { id: "today", label: "Today", icon: IconToday },
  { id: "approvals", label: "Approvals", icon: IconSeal },
  { id: "inbox", label: "Inbox", icon: IconInbox },
  { id: "board", label: "Board", icon: IconBoard },
  { id: "chat", label: "Chat", icon: IconChat },
];

interface TgTabBarProps {
  active: TgTab;
  onChange: (tab: TgTab) => void;
}

/**
 * The cockpit's own bottom nav — 4 thumb-sized tabs, controlled by page
 * state (not routes, unlike the dashboard's BottomTabBar) since the whole
 * Mini App lives on the single `/tg` route.
 */
export function TgTabBar({ active, onChange }: TgTabBarProps) {
  return (
    <nav
      aria-label="Cockpit"
      className="fixed inset-x-0 bottom-0 z-40 mx-auto flex w-full max-w-[430px] border-t bg-background/90 pb-[env(safe-area-inset-bottom)] backdrop-blur"
    >
      {TABS.map((tab) => {
        const isActive = active === tab.id;
        return (
          <button
            key={tab.id}
            type="button"
            aria-current={isActive ? "page" : undefined}
            onClick={() => onChange(tab.id)}
            className={cn(
              "tg-display flex flex-1 flex-col items-center gap-0.5 pb-2 pt-1.5 text-[9px] uppercase tracking-[0.08em] transition-colors",
              isActive ? "text-primary" : "text-muted-foreground/70",
            )}
          >
            <span
              className={cn(
                "flex h-7 w-12 items-center justify-center rounded-full transition-all duration-200 ease-out",
                isActive ? "bg-primary/15" : "bg-transparent",
              )}
            >
              <tab.icon className="h-5 w-5" />
            </span>
            {tab.label}
          </button>
        );
      })}
    </nav>
  );
}
