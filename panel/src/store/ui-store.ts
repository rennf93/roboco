import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Team } from "@/types";

interface UIState {
  // Sidebar
  sidebarOpen: boolean;
  sidebarCollapsed: boolean;

  // Theme
  theme: "light" | "dark" | "system";

  // Current context
  currentTeam: Team | null;

  // A2A live view: xl:+ context pane collapse (conversation-first layout
  // design doc §1) — same persisted-preference idiom as sidebar/theme.
  a2aContextOpen: boolean;

  // Client-only Settings-page prefs (never sent to the backend — the
  // server's settings allowlist is transcript_retention_days + feature
  // flags only). Same persisted-preference idiom as sidebar/theme.
  notificationsEnabled: boolean;
  soundEnabled: boolean;
  autoRefresh: boolean;
  refreshIntervalSeconds: number;

  // Actions
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  setTheme: (theme: "light" | "dark" | "system") => void;
  setCurrentTeam: (team: Team | null) => void;
  toggleA2AContext: () => void;
  setNotificationsEnabled: (enabled: boolean) => void;
  setSoundEnabled: (enabled: boolean) => void;
  setAutoRefresh: (enabled: boolean) => void;
  setRefreshIntervalSeconds: (seconds: number) => void;
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarOpen: true,
      sidebarCollapsed: false,
      theme: "system",
      currentTeam: null,
      a2aContextOpen: true,
      notificationsEnabled: true,
      soundEnabled: true,
      autoRefresh: false, // default-off: never start a background poller unasked
      refreshIntervalSeconds: 30,

      toggleSidebar: () =>
        set((state) => ({ sidebarOpen: !state.sidebarOpen })),
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      setTheme: (theme) => set({ theme }),
      setCurrentTeam: (team) => set({ currentTeam: team }),
      toggleA2AContext: () =>
        set((state) => ({ a2aContextOpen: !state.a2aContextOpen })),
      setNotificationsEnabled: (enabled) =>
        set({ notificationsEnabled: enabled }),
      setSoundEnabled: (enabled) => set({ soundEnabled: enabled }),
      setAutoRefresh: (enabled) => set({ autoRefresh: enabled }),
      setRefreshIntervalSeconds: (seconds) =>
        set({ refreshIntervalSeconds: seconds }),
    }),
    {
      name: "roboco-ui-storage",
      partialize: (state) => ({
        sidebarCollapsed: state.sidebarCollapsed,
        theme: state.theme,
        currentTeam: state.currentTeam,
        a2aContextOpen: state.a2aContextOpen,
        notificationsEnabled: state.notificationsEnabled,
        soundEnabled: state.soundEnabled,
        autoRefresh: state.autoRefresh,
        refreshIntervalSeconds: state.refreshIntervalSeconds,
      }),
    },
  ),
);
