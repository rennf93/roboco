import { create } from "zustand";
import type { Notification } from "@/types";

interface NotificationState {
  notifications: Notification[];
  unreadCount: number;
  pendingAckCount: number;

  // Actions
  addNotification: (notification: Notification) => void;
  markAsRead: (id: string) => void;
  markAsAcknowledged: (id: string) => void;
  setNotifications: (notifications: Notification[]) => void;
  setCounts: (unread: number, pendingAck: number) => void;
  clearAll: () => void;
}

export const useNotificationStore = create<NotificationState>((set) => ({
  notifications: [],
  unreadCount: 0,
  pendingAckCount: 0,

  addNotification: (notification) =>
    set((state) => {
      // Dedupe by id. A notification re-delivered (re-fetch / WebSocket replay)
      // must update in place, not stack a duplicate or re-increment the counts —
      // otherwise already-acknowledged notifications re-surfaced to the CEO and
      // the pending badge kept climbing.
      if (state.notifications.some((n) => n.id === notification.id)) {
        return {
          notifications: state.notifications.map((n) =>
            n.id === notification.id ? notification : n,
          ),
        };
      }
      return {
        notifications: [notification, ...state.notifications].slice(0, 50),
        unreadCount: state.unreadCount + (notification.is_read ? 0 : 1),
        pendingAckCount:
          state.pendingAckCount +
          (notification.requires_ack && !notification.is_acknowledged ? 1 : 0),
      };
    }),

  markAsRead: (id) =>
    set((state) => ({
      notifications: state.notifications.map((n) =>
        n.id === id ? { ...n, is_read: true } : n,
      ),
      unreadCount: Math.max(0, state.unreadCount - 1),
    })),

  markAsAcknowledged: (id) =>
    set((state) => ({
      notifications: state.notifications.map((n) =>
        n.id === id ? { ...n, is_acknowledged: true } : n,
      ),
      pendingAckCount: Math.max(0, state.pendingAckCount - 1),
    })),

  setNotifications: (notifications) => set({ notifications }),

  setCounts: (unread, pendingAck) =>
    set({ unreadCount: unread, pendingAckCount: pendingAck }),

  clearAll: () =>
    set({ notifications: [], unreadCount: 0, pendingAckCount: 0 }),
}));
