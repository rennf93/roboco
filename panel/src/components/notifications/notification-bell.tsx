"use client";

import { useState } from "react";
import Link from "next/link";
import { useNotificationStream } from "@/hooks/use-websocket";
import {
  useNotifications,
  useMarkNotificationRead,
  useAcknowledgeNotification,
  useMarkAllNotificationsRead,
} from "@/hooks/use-notifications";
import type { Notification } from "@/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { HelpTip } from "@/components/ui/help-tip";
import { Bell, Wifi, WifiOff, CheckCheck, MailOpen, Check } from "lucide-react";

const BELL_LABEL = "View notifications";
const PREVIEW_LIMIT = 10;

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const { isConnected, clearMessages } = useNotificationStream();
  const { data } = useNotifications();
  const markRead = useMarkNotificationRead();
  const acknowledge = useAcknowledgeNotification();
  const markAllRead = useMarkAllNotificationsRead();

  const unreadCount = data?.unread_count ?? 0;
  const pendingAckCount = data?.pending_ack_count ?? 0;
  const items = (data?.items ?? []).slice(0, PREVIEW_LIMIT);
  // The badge caps its own display at "9+", hiding the exact count — surface
  // the real number here so it's never lost to sighted or AT users.
  const bellLabel =
    unreadCount > 0
      ? `${BELL_LABEL} (${unreadCount} unread)`
      : BELL_LABEL;

  const handleMarkRead = (id: string) => {
    void markRead.mutateAsync(id);
  };
  const handleAcknowledge = (id: string) => {
    void acknowledge.mutateAsync(id);
  };
  const handleMarkAllRead = () => {
    void markAllRead.mutateAsync();
  };

  return (
    <Popover
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        // The stream buffer is no longer displayed here (the alerts sibling
        // owns the toast); clear it on close so it can't grow unbounded.
        if (!o) clearMessages();
      }}
    >
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <PopoverTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="relative"
                aria-label={bellLabel}
                title={bellLabel}
              >
                <Bell className="h-5 w-5" />
                {unreadCount > 0 && (
                  <Badge className="absolute -top-1 -right-1 h-5 min-w-5 flex items-center justify-center p-0 text-xs bg-red-500">
                    {unreadCount > 9 ? "9+" : unreadCount}
                  </Badge>
                )}
              </Button>
            </PopoverTrigger>
          </TooltipTrigger>
          <TooltipContent>{bellLabel}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <PopoverContent className="w-80" align="end">
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <h4 className="font-semibold">Notifications</h4>
              {isConnected ? (
                <HelpTip label="Live update stream connected">
                  <Wifi className="h-4 w-4 text-green-500" aria-label="connected" />
                </HelpTip>
              ) : (
                <HelpTip label="Live update stream disconnected — list may be stale">
                  <WifiOff className="h-4 w-4 text-gray-400" aria-label="disconnected" />
                </HelpTip>
              )}
            </div>
            {unreadCount > 0 && (
              <Button
                variant="ghost"
                size="sm"
                onClick={handleMarkAllRead}
                className="h-7 px-2 text-xs"
              >
                <CheckCheck className="h-3.5 w-3.5 mr-1" />
                Mark all read
              </Button>
            )}
          </div>

          {pendingAckCount > 0 && (
            <p className="text-xs text-amber-600 dark:text-amber-400">
              {pendingAckCount} pending acknowledgement
              {pendingAckCount > 1 ? "s" : ""}
            </p>
          )}

          {items.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-4">
              No new notifications
            </p>
          ) : (
            <div className="space-y-1.5 max-h-64 overflow-y-auto">
              {items.map((notification) => (
                <BellRow
                  key={notification.id}
                  notification={notification}
                  onMarkRead={handleMarkRead}
                  onAcknowledge={handleAcknowledge}
                />
              ))}
            </div>
          )}

          <div className="pt-2 border-t">
            <Link
              href="/notifications"
              onClick={() => setOpen(false)}
              prefetch={false}
            >
              <Button variant="outline" size="sm" className="w-full">
                View All Notifications
              </Button>
            </Link>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

interface BellRowProps {
  notification: Notification;
  onMarkRead: (id: string) => void;
  onAcknowledge: (id: string) => void;
}

function BellRow({ notification, onMarkRead, onAcknowledge }: BellRowProps) {
  const needsAck = notification.requires_ack && !notification.is_acknowledged;
  return (
    <div
      className={
        "p-2 rounded bg-muted/60 " +
        (notification.is_read ? "opacity-60" : "border-l-2 border-l-primary")
      }
    >
      <div className="flex items-start justify-between gap-2">
        <span className="font-medium text-sm line-clamp-1 flex-1 min-w-0">
          {notification.subject}
        </span>
        <HelpTip label="How urgently the sender flagged this — doesn't change what you need to do, just how prominently it's shown.">
          <Badge variant="outline" className="text-[10px] shrink-0">
            {notification.priority}
          </Badge>
        </HelpTip>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
        {!notification.is_read && (
          <HelpTip label="Hasn't been marked read yet.">
            <Badge variant="secondary" className="text-[10px]">
              New
            </Badge>
          </HelpTip>
        )}
        {needsAck && (
          <HelpTip label="A formal signal from a PM/Board role — needs an explicit acknowledgement, tracked separately from read status.">
            <Badge variant="destructive" className="text-[10px]">
              Needs Ack
            </Badge>
          </HelpTip>
        )}
        <div className="ml-auto flex items-center gap-1">
          {!notification.is_read && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs"
              onClick={() => onMarkRead(notification.id)}
            >
              <MailOpen className="h-3 w-3 mr-1" />
              Mark Read
            </Button>
          )}
          {needsAck && (
            <Button
              variant="default"
              size="sm"
              className="h-6 px-2 text-xs"
              onClick={() => onAcknowledge(notification.id)}
            >
              <Check className="h-3 w-3 mr-1" />
              Acknowledge
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}