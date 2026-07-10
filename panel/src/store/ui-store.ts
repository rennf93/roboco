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

  // Actions
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  setTheme: (theme: "light" | "dark" | "system") => void;
  setCurrentTeam: (team: Team | null) => void;
  toggleA2AContext: () => void;
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarOpen: true,
      sidebarCollapsed: false,
      theme: "system",
      currentTeam: null,
      a2aContextOpen: true,

      toggleSidebar: () =>
        set((state) => ({ sidebarOpen: !state.sidebarOpen })),
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      setTheme: (theme) => set({ theme }),
      setCurrentTeam: (team) => set({ currentTeam: team }),
      toggleA2AContext: () =>
        set((state) => ({ a2aContextOpen: !state.a2aContextOpen })),
    }),
    {
      name: "roboco-ui-storage",
      partialize: (state) => ({
        sidebarCollapsed: state.sidebarCollapsed,
        theme: state.theme,
        currentTeam: state.currentTeam,
        a2aContextOpen: state.a2aContextOpen,
      }),
    },
  ),
);
