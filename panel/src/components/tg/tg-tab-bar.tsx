"use client";

import { Gauge, CheckSquare, Bell, Kanban, MessageSquare } from "lucide-react";
import { cn } from "@/lib/utils";

export type TgTab = "today" | "approvals" | "inbox" | "board" | "chat";

const TABS: ReadonlyArray<{
  id: TgTab;
  label: string;
  icon: typeof CheckSquare;
}> = [
  { id: "today", label: "Today", icon: Gauge },
  { id: "approvals", label: "Approvals", icon: CheckSquare },
  { id: "inbox", label: "Inbox", icon: Bell },
  { id: "board", label: "Board", icon: Kanban },
  { id: "chat", label: "Chat", icon: MessageSquare },
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
      className="fixed inset-x-0 bottom-0 z-40 flex border-t bg-background pb-[env(safe-area-inset-bottom)]"
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
              "flex flex-1 flex-col items-center gap-1 py-2.5 text-xs font-medium transition-colors",
              isActive ? "text-primary" : "text-muted-foreground",
            )}
          >
            <tab.icon className="h-6 w-6" />
            {tab.label}
          </button>
        );
      })}
    </nav>
  );
}
