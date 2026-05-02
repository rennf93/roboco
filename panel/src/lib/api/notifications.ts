import api from "./client";
import type { Notification, NotificationListResponse, NotificationType, NotificationPriority } from "@/types";
import { isMockMode, mockNotifications } from "@/lib/mock-data";

export interface NotificationFilters {
  type?: NotificationType;
  priority?: NotificationPriority;
  unread_only?: boolean;
  pending_ack_only?: boolean;
}

export const notificationsApi = {
  // List notifications for CEO
  list: async (filters?: NotificationFilters): Promise<NotificationListResponse> => {
    if (isMockMode()) {
      let notifications = [...mockNotifications] as Notification[];
      if (filters?.type) {
        notifications = notifications.filter((n) => n.type === filters.type);
      }
      if (filters?.priority) {
        notifications = notifications.filter((n) => n.priority === filters.priority);
      }
      if (filters?.unread_only) {
        notifications = notifications.filter((n) => !n.is_read);
      }
      if (filters?.pending_ack_only) {
        notifications = notifications.filter((n) => n.requires_ack && !n.is_acknowledged);
      }
      return {
        items: notifications,
        total: notifications.length,
        unread_count: notifications.filter((n) => !n.is_read).length,
        pending_ack_count: notifications.filter((n) => n.requires_ack && !n.is_acknowledged).length,
      };
    }
    // Backend uses type_filter, priority_filter parameter names
    const params: Record<string, unknown> = {};
    if (filters?.type) params.type_filter = filters.type;
    if (filters?.priority) params.priority_filter = filters.priority;
    if (filters?.unread_only) params.unread_only = filters.unread_only;
    if (filters?.pending_ack_only) params.pending_ack_only = filters.pending_ack_only;

    const { data } = await api.get<NotificationListResponse>("/notifications", { params });
    return data;
  },

  // Get notification by ID
  get: async (notificationId: string): Promise<Notification> => {
    if (isMockMode()) {
      const notification = mockNotifications.find((n) => n.id === notificationId);
      if (notification) return notification as Notification;
      throw new Error("Notification not found");
    }
    const { data } = await api.get<Notification>("/notifications/" + notificationId);
    return data;
  },

  // Mark notification as read (backend returns 204 No Content)
  markRead: async (notificationId: string): Promise<void> => {
    if (isMockMode()) {
      const idx = mockNotifications.findIndex((n) => n.id === notificationId);
      if (idx !== -1) {
        const notification = mockNotifications[idx];
        mockNotifications[idx] = { ...notification, is_read: true };
      }
      return;
    }
    await api.post("/notifications/" + notificationId + "/read");
  },

  // Acknowledge notification (backend uses /ack)
  acknowledge: async (notificationId: string): Promise<Notification> => {
    if (isMockMode()) {
      const idx = mockNotifications.findIndex((n) => n.id === notificationId);
      if (idx !== -1) {
        const notification = mockNotifications[idx];
        const ackedNotification = {
          ...notification,
          is_acknowledged: true,
          is_fully_acknowledged: true,
        };
        mockNotifications[idx] = ackedNotification;
        return ackedNotification as Notification;
      }
      throw new Error("Notification not found");
    }
    const { data } = await api.post<Notification>(
      "/notifications/" + notificationId + "/ack"
    );
    return data;
  },

  // Mark all as read
  markAllRead: async (): Promise<void> => {
    if (isMockMode()) {
      for (let i = 0; i < mockNotifications.length; i++) {
        mockNotifications[i] = { ...mockNotifications[i], is_read: true };
      }
      return;
    }
    // Get all unread notifications
    const { data } = await api.get<{ items: Notification[] }>("/notifications", {
      params: { unread_only: true },
    });
    // Mark each as read
    await Promise.all(
      data.items.map((n) =>
        api.post("/notifications/" + n.id + "/read").catch(() => {
          // Ignore individual failures
        })
      )
    );
  },
};
