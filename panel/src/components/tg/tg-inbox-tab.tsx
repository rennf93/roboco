"use client";

import {
  useNotifications,
  useAcknowledgeNotification,
} from "@/hooks/use-notifications";
import { getAgentDisplayName } from "@/lib/agent-utils";
import { getErrorMessage } from "@/lib/api/client";
import type { Notification } from "@/types";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Bell, Check } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

function TgNotificationRow({ notification }: { notification: Notification }) {
  const acknowledge = useAcknowledgeNotification();
  const needsAck = notification.requires_ack && !notification.is_acknowledged;

  return (
    <div
      className={cn(
        "rounded-xl border bg-card p-3 text-card-foreground",
        notification.is_read ? "opacity-70" : "border-l-4 border-l-primary",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-medium leading-snug">
          {notification.subject}
        </p>
        {needsAck && (
          <Button
            size="sm"
            className="h-7 shrink-0 px-2 text-xs"
            disabled={acknowledge.isPending}
            onClick={() =>
              acknowledge.mutate(notification.id, {
                onError: (err) => toast.error(getErrorMessage(err)),
              })
            }
          >
            <Check className="mr-1 h-3.5 w-3.5" />
            Ack
          </Button>
        )}
      </div>
      <p className="mt-1 text-xs text-muted-foreground line-clamp-2">
        {notification.body}
      </p>
      <p className="mt-1.5 text-[11px] text-muted-foreground">
        {getAgentDisplayName(notification.from_agent)} ·{" "}
        {formatDistanceToNow(new Date(notification.timestamp))} ago
      </p>
    </div>
  );
}

/**
 * Notification inbox for the /tg cockpit — every notification, newest
 * first, with an Ack button on the ones that require it. Polling rides
 * useNotifications' own 30s refetchInterval; no extra wiring needed here.
 */
export function TgInboxTab() {
  const { data, isLoading } = useNotifications();

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (!data?.items.length) {
    return (
      <div className="flex flex-col items-center gap-2 py-10 text-center text-muted-foreground">
        <Bell className="h-8 w-8 opacity-50" />
        <p className="text-sm">No notifications</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {data.items.map((n) => (
        <TgNotificationRow key={n.id} notification={n} />
      ))}
    </div>
  );
}
