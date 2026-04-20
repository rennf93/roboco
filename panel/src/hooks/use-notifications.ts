"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { notificationsApi, type NotificationFilters } from "@/lib/api/notifications";

export const notificationKeys = {
  all: ["notifications"] as const,
  list: (filters?: NotificationFilters) => [...notificationKeys.all, "list", filters] as const,
  detail: (id: string) => [...notificationKeys.all, "detail", id] as const,
};

export function useNotifications(filters?: NotificationFilters) {
  return useQuery({
    queryKey: notificationKeys.list(filters),
    queryFn: () => notificationsApi.list(filters),
    refetchInterval: 30000, // Refetch every 30 seconds
  });
}

export function useNotification(notificationId: string | null) {
  return useQuery({
    queryKey: notificationKeys.detail(notificationId || ""),
    queryFn: () => notificationsApi.get(notificationId!),
    enabled: !!notificationId,
  });
}

export function useMarkNotificationRead() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (notificationId: string) => notificationsApi.markRead(notificationId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: notificationKeys.all });
    },
  });
}

export function useAcknowledgeNotification() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (notificationId: string) => notificationsApi.acknowledge(notificationId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: notificationKeys.all });
    },
  });
}

export function useMarkAllNotificationsRead() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => notificationsApi.markAllRead(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: notificationKeys.all });
    },
  });
}
