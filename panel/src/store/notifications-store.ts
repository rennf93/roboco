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
    set((state) => ({
      notifications: [notification, ...state.notifications].slice(0, 50),
      unreadCount: state.unreadCount + (notification.is_read ? 0 : 1),
      pendingAckCount: state.pendingAckCount + (notification.requires_ack && !notification.is_acknowledged ? 1 : 0),
    })),

  markAsRead: (id) =>
    set((state) => ({
      notifications: state.notifications.map((n) =>
        n.id === id ? { ...n, is_read: true } : n
      ),
      unreadCount: Math.max(0, state.unreadCount - 1),
    })),

  markAsAcknowledged: (id) =>
    set((state) => ({
      notifications: state.notifications.map((n) =>
        n.id === id ? { ...n, is_acknowledged: true } : n
      ),
      pendingAckCount: Math.max(0, state.pendingAckCount - 1),
    })),

  setNotifications: (notifications) => set({ notifications }),

  setCounts: (unread, pendingAck) =>
    set({ unreadCount: unread, pendingAckCount: pendingAck }),

  clearAll: () => set({ notifications: [], unreadCount: 0, pendingAckCount: 0 }),
}));
