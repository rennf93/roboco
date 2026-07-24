"use client";

import { useTheme } from "next-themes";
import { useQuery } from "@tanstack/react-query";
import { Search, Sun, Moon, Monitor, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { NotificationBell } from "@/components/notifications/notification-bell";
import { NotificationAlerts } from "@/components/notifications/notification-alerts";
import { ConnectionStatus } from "./connection-status";
import { MobileSidebar } from "./mobile-sidebar";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { usePageRefresh } from "@/hooks";
import { settingsApi } from "@/lib/api";
import { CEO_NAME_KEY, DEFAULT_CEO_NAME } from "@/lib/api/settings";
import { useUIStore } from "@/store";

const REFRESH_LABEL = "Refresh only the current page";

export function Header() {
  const { setTheme } = useTheme();
  const { refresh, loading, disabled } = usePageRefresh();
  const setCommandPaletteOpen = useUIStore((s) => s.setCommandPaletteOpen);
  // Same ["settings"] query key as the Settings page's User Info card — the
  // app-wide react-query cache means whichever loads first primes the other.
  // Falls back to the config default while loading/unset, so there's no
  // flash of a wrong name.
  const { data: settings } = useQuery({
    queryKey: ["settings"],
    queryFn: settingsApi.getAll,
  });
  const ceoName = settings?.[CEO_NAME_KEY] ?? DEFAULT_CEO_NAME;

  return (
    <header className="flex h-16 items-center justify-between border-b bg-background px-6">
      {/* Search — opens the Cmd+K command palette */}
      <div className="flex items-center gap-4 flex-1 max-w-md">
        {/* Mobile nav trigger — only shown below md, where the sidebar is hidden */}
        <MobileSidebar />
        <button
          type="button"
          onClick={() => setCommandPaletteOpen(true)}
          className="relative flex w-full items-center rounded-md border border-input bg-transparent px-3 py-2 text-sm text-muted-foreground shadow-xs transition-colors hover:bg-accent hover:text-accent-foreground"
        >
          <Search className="mr-2 h-4 w-4 shrink-0" />
          <span className="flex-1 text-left">Search tasks, agents...</span>
          <kbd className="hidden shrink-0 items-center gap-0.5 rounded border bg-muted px-1.5 font-mono text-xs sm:inline-flex">
            <span className="text-xs">⌘</span>K
          </kbd>
        </button>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-3">
        {/* Connection Status */}
        <ConnectionStatus />

        {/* Refresh current page data */}
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => void refresh()}
                disabled={disabled || loading}
                aria-label={REFRESH_LABEL}
              >
                <RefreshCw
                  className={cn("h-5 w-5", loading && "animate-spin")}
                />
              </Button>
            </TooltipTrigger>
            <TooltipContent>{REFRESH_LABEL}</TooltipContent>
          </Tooltip>
        </TooltipProvider>

        {/* Theme toggle */}
        <DropdownMenu>
          <Tooltip>
            <TooltipTrigger asChild>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon">
                  <Sun className="h-5 w-5 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
                  <Moon className="absolute h-5 w-5 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
                  <span className="sr-only">Toggle theme</span>
                </Button>
              </DropdownMenuTrigger>
            </TooltipTrigger>
            <TooltipContent>Switch light / dark theme</TooltipContent>
          </Tooltip>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => setTheme("light")}>
              <Sun className="h-4 w-4 mr-2" />
              Light
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => setTheme("dark")}>
              <Moon className="h-4 w-4 mr-2" />
              Dark
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => setTheme("system")}>
              <Monitor className="h-4 w-4 mr-2" />
              System
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        {/* Notifications with WebSocket */}
        <NotificationBell />
        <NotificationAlerts />

        {/* User */}
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="flex items-center gap-2 ml-2 pl-4 border-l">
              <div className="h-8 w-8 rounded-full bg-primary flex items-center justify-center">
                <span className="text-primary-foreground font-medium text-sm">
                  CEO
                </span>
              </div>
              <span className="text-sm font-medium hidden sm:inline">
                {ceoName}
              </span>
            </div>
          </TooltipTrigger>
          <TooltipContent>
            Signed in as the CEO — the panel&apos;s single human operator
          </TooltipContent>
        </Tooltip>
      </div>
    </header>
  );
}
