"use client";

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  notificationKeys,
  useAcknowledgeNotification,
  useNotifications,
} from "@/hooks/use-notifications";
import { notificationsApi } from "@/lib/api/notifications";
import { isTgDemoMode } from "@/lib/telegram/demo";
import { getAgentDisplayName } from "@/lib/agent-utils";
import { haptics } from "@/lib/telegram/webapp";
import { humanizeIds, useTaskNameIndex } from "@/components/tg/tg-format";
import { NotificationType, type Notification } from "@/types";
import { Skeleton } from "@/components/ui/skeleton";
import { TG_CARD, TgRowIcon, TgSection } from "@/components/tg/ui";
import {
  ArrowsDownUp,
  At,
  Check,
  CheckSquare,
  ClipboardText,
  Eye,
  FileText,
  Lightbulb,
  Megaphone,
  Warning,
  WarningCircle,
} from "@phosphor-icons/react";
import { formatDistanceToNow, format, isToday, isYesterday } from "date-fns";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

/** Best-effort tone per notification type — the real enum has no
 * escalation/completion/system taxonomy, so this maps each real type onto
 * the cockpit's 5-tone language (danger/decision/info/positive/ambient). */
const NOTIF_TONE: Record<NotificationType, string> = {
  [NotificationType.TASK_ASSIGNMENT]: "sky",
  [NotificationType.PRIORITY_CHANGE]: "sky",
  [NotificationType.BLOCKER_ESCALATION]: "rose",
  [NotificationType.REVIEW_REQUEST]: "violet",
  [NotificationType.DOCUMENTATION_REQUEST]: "sky",
  [NotificationType.APPROVAL]: "violet",
  [NotificationType.ALERT]: "rose",
  [NotificationType.BROADCAST]: "muted",
  [NotificationType.KNOWLEDGE_SHARE]: "emerald",
  [NotificationType.MENTION]: "sky",
};

const NOTIF_ICON: Record<NotificationType, typeof Check> = {
  [NotificationType.TASK_ASSIGNMENT]: ClipboardText,
  [NotificationType.PRIORITY_CHANGE]: ArrowsDownUp,
  [NotificationType.BLOCKER_ESCALATION]: Warning,
  [NotificationType.REVIEW_REQUEST]: Eye,
  [NotificationType.DOCUMENTATION_REQUEST]: FileText,
  [NotificationType.APPROVAL]: CheckSquare,
  [NotificationType.ALERT]: WarningCircle,
  [NotificationType.BROADCAST]: Megaphone,
  [NotificationType.KNOWLEDGE_SHARE]: Lightbulb,
  [NotificationType.MENTION]: At,
};

function sentenceCase(s: string): string {
  return s.length === 0 ? s : s.charAt(0).toUpperCase() + s.slice(1);
}

/** Strips a leading `[something]` off a subject into its own chip — e.g.
 * a system-authored "[strategy engine] weekly digest ready" reads as a
 * "Strategy engine" chip plus a clean sentence. */
function splitBracketPrefix(subject: string): {
  chip: string | null;
  text: string;
} {
  const m = subject.match(/^\[([^\]]+)]\s*/);
  if (!m) return { chip: null, text: subject };
  return {
    chip: sentenceCase(m[1]),
    text: sentenceCase(subject.slice(m[0].length)),
  };
}

function dayLabel(iso: string): string {
  const d = new Date(iso);
  if (isToday(d)) return "Today";
  if (isYesterday(d)) return "Yesterday";
  return format(d, "MMM d");
}

/** Groups by day, preserving the feed's own (newest-first) order — so day
 * buckets surface in the same order the items already arrive in. */
function groupByDay(items: Notification[]): Array<[string, Notification[]]> {
  const groups = new Map<string, Notification[]>();
  for (const n of items) {
    const label = dayLabel(n.timestamp);
    const bucket = groups.get(label);
    if (bucket) bucket.push(n);
    else groups.set(label, [n]);
  }
  return Array.from(groups.entries());
}

function NotificationRow({
  notification,
  resolveTask,
  onAck,
  ackPending,
}: {
  notification: Notification;
  resolveTask: (id: string) => string | undefined;
  onAck: () => void;
  ackPending: boolean;
}) {
  const sender = getAgentDisplayName(notification.from_agent);
  const { chip, text } = splitBracketPrefix(
    humanizeIds(notification.subject, resolveTask),
  );
  const needsAck = notification.requires_ack && !notification.is_acknowledged;
  const tone = NOTIF_TONE[notification.type] ?? "muted";
  const Icon = NOTIF_ICON[notification.type] ?? Check;

  return (
    <div
      className={cn(
        "flex min-h-12 w-full items-center gap-3 rounded-xl px-1.5 py-2",
        !notification.is_read && "bg-primary/[0.04]",
      )}
    >
      <TgRowIcon icon={Icon} tone={tone} />
      <div className="min-w-0 flex-1">
        <p className="line-clamp-2 text-[15px] font-medium leading-snug">
          {chip && (
            <span className="mr-1.5 inline-flex items-center rounded-full bg-violet-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-violet-300">
              {chip}
            </span>
          )}
          {text}
        </p>
        <p className="mt-0.5 truncate text-xs leading-tight text-muted-foreground">
          {sender} · {formatDistanceToNow(new Date(notification.timestamp))} ago
        </p>
      </div>
      {needsAck && (
        <button
          type="button"
          disabled={ackPending}
          onClick={onAck}
          className="flex h-9 shrink-0 items-center gap-1 rounded-full bg-primary px-2.5 text-xs font-semibold text-primary-foreground disabled:opacity-60"
        >
          <Check className="h-3.5 w-3.5" />
          Ack
        </button>
      )}
    </div>
  );
}

/**
 * Notification inbox for the /tg cockpit — grouped by day, newest first,
 * with an Ack pill on the ones that require it and a batched "Ack all".
 * Renders inside the page shell's own `TgSubPage`, so this is content
 * only — no title/back-button chrome here. Polling rides useNotifications'
 * own 30s refetchInterval. Demo mode renders the canned fixtures instead
 * (the live query still mounts — dev-only noise, same trade as the Board
 * tab).
 */
export function TgInboxTab() {
  const queryClient = useQueryClient();
  const { data: fetched, isLoading: fetchLoading } = useNotifications();
  const [demoItems, setDemoItems] = useState<Notification[] | undefined>(
    undefined,
  );
  useEffect(() => {
    if (!isTgDemoMode()) return;
    void import("@/lib/telegram/demo-data").then((m) =>
      setDemoItems(m.DEMO_NOTIFICATIONS),
    );
  }, []);
  const resolveTask = useTaskNameIndex();
  const ack = useAcknowledgeNotification();
  const [ackAllBusy, setAckAllBusy] = useState(false);

  const items = demoItems ?? fetched?.items ?? [];
  const isLoading = demoItems ? false : fetchLoading;
  const unreadCount = items.filter((n) => !n.is_read).length;
  const pendingAcks = items.filter((n) => n.requires_ack && !n.is_acknowledged);

  const runAckAll = async () => {
    haptics.tap();
    setAckAllBusy(true);
    const results = await Promise.allSettled(
      pendingAcks.map((n) => notificationsApi.acknowledge(n.id)),
    );
    const ok = results.filter((r) => r.status === "fulfilled").length;
    await queryClient.invalidateQueries({ queryKey: notificationKeys.all });
    setAckAllBusy(false);
    if (ok === results.length) {
      haptics.success();
      toast.success(`Acknowledged ${ok} notification${ok === 1 ? "" : "s"}`);
    } else {
      haptics.error();
      toast.warning(`Acknowledged ${ok} of ${results.length}`);
    }
  };

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-14 w-full rounded-xl" />
        ))}
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div
        className={cn(
          TG_CARD,
          "flex flex-col items-center gap-2 p-8 text-center text-muted-foreground",
        )}
      >
        <TgRowIcon icon={Check} tone="emerald" />
        <p className="text-sm">Inbox zero.</p>
      </div>
    );
  }

  return (
    <div className="tg-stagger space-y-3">
      <div className="flex items-center justify-between px-1">
        <span className="text-xs text-muted-foreground">
          {unreadCount} unread
        </span>
        {pendingAcks.length > 0 && (
          <button
            type="button"
            disabled={ackAllBusy}
            onClick={() => void runAckAll()}
            className="text-xs font-medium text-primary disabled:opacity-60"
          >
            {ackAllBusy ? "Acking…" : "Ack all"}
          </button>
        )}
      </div>
      {groupByDay(items).map(([day, dayItems]) => (
        <TgSection key={day} title={day}>
          <div className="divide-y divide-white/[0.04]">
            {dayItems.map((n) => (
              <NotificationRow
                key={n.id}
                notification={n}
                resolveTask={resolveTask}
                ackPending={ack.isPending}
                onAck={() =>
                  ack.mutate(n.id, {
                    onError: () =>
                      toast.error("Couldn't acknowledge. Try again."),
                  })
                }
              />
            ))}
          </div>
        </TgSection>
      ))}
    </div>
  );
}
