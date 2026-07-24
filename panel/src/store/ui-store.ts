import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Team } from "@/types";
import { DEFAULT_QUICK_ACTION_IDS } from "@/components/dashboard/quick-actions-registry";

export type CardTableView = "cards" | "table";

interface UIState {
  // Sidebar
  sidebarOpen: boolean;
  sidebarCollapsed: boolean;

  // Theme
  theme: "light" | "dark" | "system";

  // Current context
  currentTeam: Team | null;

  // Command palette (Cmd+K) — not persisted, always closed on reload
  commandPaletteOpen: boolean;

  // Settings: Notifications & data refresh — client-only prefs, never sent
  // to the backend.
  notificationsEnabled: boolean;
  soundEnabled: boolean;
  autoRefresh: boolean;
  refreshIntervalSeconds: number;

  // A2A: xl:+ context pane collapse
  a2aContextOpen: boolean;

  // Overview dashboard quick actions — ids + display order, customizable
  quickActionIds: string[];

  // Workstation tab view modes
  productsView: CardTableView;
  projectsView: CardTableView;

  // Actions
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  setTheme: (theme: "light" | "dark" | "system") => void;
  setCurrentTeam: (team: Team | null) => void;
  setCommandPaletteOpen: (open: boolean) => void;
  toggleCommandPaletteOpen: () => void;
  setNotificationsEnabled: (enabled: boolean) => void;
  setSoundEnabled: (enabled: boolean) => void;
  setAutoRefresh: (enabled: boolean) => void;
  setRefreshIntervalSeconds: (seconds: number) => void;
  toggleA2AContext: () => void;
  setQuickActionIds: (ids: string[]) => void;
  resetQuickActionIds: () => void;
  setProductsView: (view: CardTableView) => void;
  setProjectsView: (view: CardTableView) => void;
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarOpen: true,
      sidebarCollapsed: false,
      theme: "system",
      currentTeam: null,
      commandPaletteOpen: false,
      notificationsEnabled: true,
      soundEnabled: true,
      autoRefresh: false,
      refreshIntervalSeconds: 30,
      a2aContextOpen: true,
      quickActionIds: DEFAULT_QUICK_ACTION_IDS,
      productsView: "cards",
      projectsView: "cards",

      toggleSidebar: () =>
        set((state) => ({ sidebarOpen: !state.sidebarOpen })),
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      setTheme: (theme) => set({ theme }),
      setCurrentTeam: (team) => set({ currentTeam: team }),
      setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),
      toggleCommandPaletteOpen: () =>
        set((state) => ({ commandPaletteOpen: !state.commandPaletteOpen })),
      setNotificationsEnabled: (enabled) =>
        set({ notificationsEnabled: enabled }),
      setSoundEnabled: (enabled) => set({ soundEnabled: enabled }),
      setAutoRefresh: (enabled) => set({ autoRefresh: enabled }),
      setRefreshIntervalSeconds: (seconds) =>
        set({ refreshIntervalSeconds: seconds }),
      toggleA2AContext: () =>
        set((state) => ({ a2aContextOpen: !state.a2aContextOpen })),
      setQuickActionIds: (ids) => set({ quickActionIds: ids }),
      resetQuickActionIds: () =>
        set({ quickActionIds: DEFAULT_QUICK_ACTION_IDS }),
      setProductsView: (view) => set({ productsView: view }),
      setProjectsView: (view) => set({ projectsView: view }),
    }),
    {
      name: "roboco-ui-storage",
      partialize: (state) => ({
        sidebarCollapsed: state.sidebarCollapsed,
        theme: state.theme,
        currentTeam: state.currentTeam,
        notificationsEnabled: state.notificationsEnabled,
        soundEnabled: state.soundEnabled,
        autoRefresh: state.autoRefresh,
        refreshIntervalSeconds: state.refreshIntervalSeconds,
        a2aContextOpen: state.a2aContextOpen,
        quickActionIds: state.quickActionIds,
        productsView: state.productsView,
        projectsView: state.projectsView,
      }),
    },
  ),
);
