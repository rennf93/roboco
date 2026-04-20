"use client";

import { useState } from "react";
import Link from "next/link";
import { useNotificationStream } from "@/hooks/use-websocket";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Bell, Wifi, WifiOff } from "lucide-react";

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const { notifications, isConnected, clearMessages } = useNotificationStream();

  const unreadCount = notifications.length;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="relative">
          <Bell className="h-5 w-5" />
          {unreadCount > 0 && (
            <Badge 
              className="absolute -top-1 -right-1 h-5 w-5 flex items-center justify-center p-0 text-xs bg-red-500"
            >
              {unreadCount > 9 ? "9+" : unreadCount}
            </Badge>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80" align="end">
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h4 className="font-semibold">Notifications</h4>
            <div className="flex items-center gap-2">
              {isConnected ? (
                <Wifi className="h-4 w-4 text-green-500" />
              ) : (
                <WifiOff className="h-4 w-4 text-gray-400" />
              )}
              {unreadCount > 0 && (
                <Button variant="ghost" size="sm" onClick={clearMessages}>
                  Clear
                </Button>
              )}
            </div>
          </div>
          
          {notifications.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-4">
              No new notifications
            </p>
          ) : (
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {notifications.slice(-10).reverse().map((notification, i) => (
                <div
                  key={i}
                  className="p-2 rounded bg-muted hover:bg-muted/80 cursor-pointer"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-sm">
                      {notification.subject}
                    </span>
                    <Badge variant="outline" className="text-xs">
                      {notification.priority}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1">
                    {notification.notification_type}
                  </p>
                </div>
              ))}
            </div>
          )}
          
          <div className="pt-2 border-t">
            <Link href="/notifications" onClick={() => setOpen(false)}>
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
