"use client";

import { useTheme } from "next-themes";
import { Search, Sun, Moon, Monitor, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { NotificationBell } from "@/components/notifications/notification-bell";
import { ConnectionStatus } from "./connection-status";
import { MobileSidebar } from "./mobile-sidebar";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { usePageRefresh } from "@/hooks";

export function Header() {
  const { setTheme } = useTheme();
  const { refresh, loading, disabled } = usePageRefresh();

  return (
    <header className="flex h-16 items-center justify-between border-b bg-background px-6">
      {/* Search */}
      <div className="flex items-center gap-4 flex-1 max-w-md">
        {/* Mobile nav trigger — only shown below md, where the sidebar is hidden */}
        <MobileSidebar />
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="relative w-full">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                type="search"
                placeholder="Search tasks, agents..."
                className="pl-10"
                disabled={true}
              />
            </div>
          </TooltipTrigger>
          <TooltipContent>Coming Soon</TooltipContent>
        </Tooltip>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-3">
        {/* Connection Status */}
        <ConnectionStatus />

        {/* Refresh current page data */}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => void refresh()}
              disabled={disabled || loading}
              aria-label="Refresh only the current page"
            >
              <RefreshCw
                className={cn("h-5 w-5", loading && "animate-spin")}
              />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Refresh this page&apos;s data</TooltipContent>
        </Tooltip>

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

        {/* User */}
        <div className="flex items-center gap-2 ml-2 pl-4 border-l">
          <div className="h-8 w-8 rounded-full bg-primary flex items-center justify-center">
            <span className="text-primary-foreground font-medium text-sm">
              CEO
            </span>
          </div>
          <span className="text-sm font-medium hidden sm:inline">Renzo</span>
        </div>
      </div>
    </header>
  );
}
