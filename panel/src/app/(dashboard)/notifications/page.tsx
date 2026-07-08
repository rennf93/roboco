"use client";

import { Suspense, useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import {
  useNotifications,
  useMarkNotificationRead,
  useAcknowledgeNotification,
  useMarkAllNotificationsRead,
} from "@/hooks/use-notifications";
import { Notification, NotificationType, NotificationPriority } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { OfflineState } from "@/components/ui/offline-state";
import { usePageRefresh } from "@/hooks";
import {
  Bell,
  Check,
  CheckCheck,
  AlertTriangle,
  Info,
  ListTodo,
  ArrowUpCircle,
  Mail,
  MailOpen,
  BookOpen,
  AtSign,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import { Markdown } from "@/components/ui/markdown";

const typeIcons: Record<NotificationType, React.ReactNode> = {
  [NotificationType.TASK_ASSIGNMENT]: (
    <ListTodo className="h-4 w-4 text-green-500" />
  ),
  [NotificationType.PRIORITY_CHANGE]: (
    <ArrowUpCircle className="h-4 w-4 text-orange-500" />
  ),
  [NotificationType.BLOCKER_ESCALATION]: (
    <AlertTriangle className="h-4 w-4 text-red-500" />
  ),
  [NotificationType.REVIEW_REQUEST]: (
    <Check className="h-4 w-4 text-purple-500" />
  ),
  [NotificationType.DOCUMENTATION_REQUEST]: (
    <Info className="h-4 w-4 text-blue-500" />
  ),
  [NotificationType.ALERT]: (
    <AlertTriangle className="h-4 w-4 text-yellow-500" />
  ),
  [NotificationType.BROADCAST]: <Bell className="h-4 w-4 text-gray-500" />,
  [NotificationType.KNOWLEDGE_SHARE]: (
    <BookOpen className="h-4 w-4 text-cyan-500" />
  ),
  [NotificationType.MENTION]: <AtSign className="h-4 w-4 text-indigo-500" />,
};

const priorityColors: Record<NotificationPriority, string> = {
  [NotificationPriority.NORMAL]:
    "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  [NotificationPriority.HIGH]:
    "bg-orange-100 text-orange-700 dark:bg-orange-900 dark:text-orange-300",
  [NotificationPriority.URGENT]:
    "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
};

interface NotificationCardProps {
  notification: Notification;
  onMarkRead: () => void;
  onAcknowledge: () => void;
}

function NotificationCard({
  notification,
  onMarkRead,
  onAcknowledge,
}: NotificationCardProps) {
  return (
    <Card
      className={
        notification.is_read ? "opacity-70" : "border-l-4 border-l-primary"
      }
    >
      <CardContent className="p-4">
        <div className="flex items-start gap-3">
          <div className="mt-1">{typeIcons[notification.type]}</div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-medium">{notification.subject}</span>
              <Badge
                className={priorityColors[notification.priority] + " text-xs"}
              >
                {notification.priority}
              </Badge>
              {!notification.is_read && (
                <Badge variant="secondary" className="text-xs">
                  New
                </Badge>
              )}
              {notification.requires_ack && !notification.is_acknowledged && (
                <Badge variant="destructive" className="text-xs">
                  Needs Ack
                </Badge>
              )}
            </div>
            <div className="text-sm text-muted-foreground mt-1">
              <Markdown>{notification.body}</Markdown>
            </div>
            <div className="flex items-center justify-between mt-3">
              <div className="text-xs text-muted-foreground">
                From: {notification.from_agent.slice(0, 8)} •{" "}
                {formatDistanceToNow(new Date(notification.timestamp))} ago
              </div>
              <div className="flex items-center gap-2">
                {!notification.is_read && (
                  <Button variant="ghost" size="sm" onClick={onMarkRead}>
                    <MailOpen className="h-4 w-4 mr-1" />
                    Mark Read
                  </Button>
                )}
                {notification.requires_ack && !notification.is_acknowledged && (
                  <Button variant="default" size="sm" onClick={onAcknowledge}>
                    <CheckCheck className="h-4 w-4 mr-1" />
                    Acknowledge
                  </Button>
                )}
              </div>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

type TabValue = "all" | "unread" | "pending";

const VALID_TABS: TabValue[] = ["all", "unread", "pending"];

function isValidTab(value: string | null): value is TabValue {
  return VALID_TABS.includes(value as TabValue);
}

function NotificationsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Read ?tab= from URL, default to "unread" (most actionable view)
  const rawTab = searchParams.get("tab");
  const activeTab: TabValue = isValidTab(rawTab) ? rawTab : "unread";

  function handleTabChange(value: string) {
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", value);
    router.push(`?${params.toString()}`);
  }

  const { data, isLoading, error, refetch } = useNotifications(
    activeTab === "unread"
      ? { unread_only: true }
      : activeTab === "pending"
        ? { pending_ack_only: true }
        : undefined,
  );

  const { register, unregister, refresh } = usePageRefresh();

  useEffect(() => {
    const cb = () => {
      void refetch();
    };
    register(cb);
    return () => unregister(cb);
  }, [register, unregister, refetch]);

  const markRead = useMarkNotificationRead();
  const acknowledge = useAcknowledgeNotification();
  const markAllRead = useMarkAllNotificationsRead();

  const isOffline =
    error &&
    (error.message?.includes("Network Error") ||
      (error as { code?: string })?.code === "ERR_NETWORK");

  const handleMarkRead = async (id: string) => {
    try {
      await markRead.mutateAsync(id);
      toast.success("Marked as read");
    } catch {
      toast.error("Failed to mark as read");
    }
  };

  const handleAcknowledge = async (id: string) => {
    try {
      await acknowledge.mutateAsync(id);
      toast.success("Notification acknowledged");
    } catch {
      toast.error("Failed to acknowledge");
    }
  };

  const handleMarkAllRead = async () => {
    try {
      await markAllRead.mutateAsync();
      toast.success("All notifications marked as read");
    } catch {
      toast.error("Failed to mark all as read");
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Notifications</h1>
          <p className="text-muted-foreground">
            Manage alerts, approvals, and escalations
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={handleMarkAllRead}>
            <CheckCheck className="h-4 w-4 mr-2" />
            Mark All Read
          </Button>
        </div>
      </div>

      {/* Stats — tighter padding below sm so 3 columns still fit at 375px */}
      {!isOffline && data && (
        <div className="grid grid-cols-3 gap-2 sm:gap-4">
          <Card className="py-4 sm:py-6">
            <CardHeader className="px-3 pb-2 sm:px-6">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Total
              </CardTitle>
            </CardHeader>
            <CardContent className="px-3 sm:px-6">
              <div className="text-2xl font-bold">{data.total}</div>
            </CardContent>
          </Card>
          <Card className="py-4 sm:py-6">
            <CardHeader className="px-3 pb-2 sm:px-6">
              <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-1">
                <Mail className="h-4 w-4" />
                Unread
              </CardTitle>
            </CardHeader>
            <CardContent className="px-3 sm:px-6">
              <div className="text-2xl font-bold text-blue-600">
                {data.unread_count}
              </div>
            </CardContent>
          </Card>
          <Card className="py-4 sm:py-6">
            <CardHeader className="px-3 pb-2 sm:px-6">
              <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-1">
                <Bell className="h-4 w-4" />
                Pending Ack
              </CardTitle>
            </CardHeader>
            <CardContent className="px-3 sm:px-6">
              <div className="text-2xl font-bold text-red-600">
                {data.pending_ack_count}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {isOffline ? (
        <OfflineState
          title="Cannot Load Notifications"
          description="Start the RoboCo orchestrator to view and manage notifications."
          onRetry={() => void refresh()}
        />
      ) : (
        <Tabs value={activeTab} onValueChange={handleTabChange}>
          <TabsList>
            <TabsTrigger value="all">All</TabsTrigger>
            <TabsTrigger value="unread">
              Unread {data && data.unread_count > 0 && `(${data.unread_count})`}
            </TabsTrigger>
            <TabsTrigger value="pending">
              Pending{" "}
              {data &&
                data.pending_ack_count > 0 &&
                `(${data.pending_ack_count})`}
            </TabsTrigger>
          </TabsList>

          <TabsContent value={activeTab} className="mt-4 space-y-3">
            {isLoading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <Card key={i}>
                  <CardContent className="p-4">
                    <Skeleton className="h-20 w-full" />
                  </CardContent>
                </Card>
              ))
            ) : !data?.items.length ? (
              <Card>
                <CardContent className="py-8 text-center text-muted-foreground">
                  <Bell className="h-12 w-12 mx-auto mb-2 opacity-50" />
                  <p>No notifications to display</p>
                </CardContent>
              </Card>
            ) : (
              data.items.map((notification) => (
                <NotificationCard
                  key={notification.id}
                  notification={notification}
                  onMarkRead={() => handleMarkRead(notification.id)}
                  onAcknowledge={() => handleAcknowledge(notification.id)}
                />
              ))
            )}
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}

// Wrap in Suspense for useSearchParams
export default function NotificationsPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <Skeleton className="h-9 w-48 mb-2" />
              <Skeleton className="h-5 w-72" />
            </div>
            <div className="flex items-center gap-2">
              <Skeleton className="h-9 w-36" />
              <Skeleton className="h-9 w-24" />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <Card key={i}>
                <CardHeader className="pb-2">
                  <Skeleton className="h-4 w-16" />
                </CardHeader>
                <CardContent>
                  <Skeleton className="h-8 w-12" />
                </CardContent>
              </Card>
            ))}
          </div>
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Card key={i}>
                <CardContent className="p-4">
                  <Skeleton className="h-20 w-full" />
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      }
    >
      <NotificationsPageContent />
    </Suspense>
  );
}
