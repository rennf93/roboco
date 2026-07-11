/**
 * Scroll-restoration / session-navigation store.
 *
 * Persists scroll position and last-visited-route state across navigation using
 * Zustand with sessionStorage — state that doesn't belong in URL params but
 * should survive navigation. Renamed from the generic `useUIStore` to avoid a
 * name clash with the sidebar/theme UI store in `@/store`.
 */

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

interface ScrollPosition {
  x: number;
  y: number;
}

// A single entry in the current Tasks list order (filter + sort applied),
// captured by the Tasks list page so the task-detail page can compute
// prev/next without re-implementing the list's filter/sort logic.
export interface TaskListNavItem {
  id: string;
  title: string;
}

export interface TaskListNavContext {
  items: TaskListNavItem[];
  // The Tasks list's current query string (filters/sort/search), so a "Back
  // to list" link can restore the exact context the user navigated from.
  queryString: string;
}

interface ScrollRestorationState {
  // Scroll positions per route
  scrollPositions: Record<string, ScrollPosition>;

  // Expanded/collapsed state for accordions, cards, etc.
  expandedSections: Record<string, boolean>;

  // Selected items that aren't in URL (e.g., multi-select temporary state)
  selectedItems: Record<string, string[]>;

  // Last visited routes per section (for "back" behavior)
  lastVisited: Record<string, string>;

  // Current Tasks list order (filter/sort context) for task-detail prev/next
  // navigation. Null when no list has been visited this session — the
  // documented fallback for task-detail is to disable prev/next then.
  taskListNav: TaskListNavContext | null;

  // Actions
  setScrollPosition: (route: string, position: ScrollPosition) => void;
  getScrollPosition: (route: string) => ScrollPosition | undefined;

  toggleSection: (sectionId: string) => void;
  setSectionExpanded: (sectionId: string, expanded: boolean) => void;
  isSectionExpanded: (sectionId: string) => boolean;

  setSelectedItems: (key: string, items: string[]) => void;
  getSelectedItems: (key: string) => string[];
  clearSelectedItems: (key: string) => void;

  setLastVisited: (section: string, route: string) => void;
  getLastVisited: (section: string) => string | undefined;

  setTaskListNav: (context: TaskListNavContext) => void;
}

export const useScrollRestorationStore = create<ScrollRestorationState>()(
  persist(
    (set, get) => ({
      scrollPositions: {},
      expandedSections: {},
      selectedItems: {},
      lastVisited: {},
      taskListNav: null,

      // Scroll position management
      setScrollPosition: (route, position) =>
        set((state) => ({
          scrollPositions: { ...state.scrollPositions, [route]: position },
        })),

      getScrollPosition: (route) => get().scrollPositions[route],

      // Section expansion management
      toggleSection: (sectionId) =>
        set((state) => ({
          expandedSections: {
            ...state.expandedSections,
            [sectionId]: !state.expandedSections[sectionId],
          },
        })),

      setSectionExpanded: (sectionId, expanded) =>
        set((state) => ({
          expandedSections: {
            ...state.expandedSections,
            [sectionId]: expanded,
          },
        })),

      isSectionExpanded: (sectionId) =>
        get().expandedSections[sectionId] ?? true,

      // Selected items management
      setSelectedItems: (key, items) =>
        set((state) => ({
          selectedItems: { ...state.selectedItems, [key]: items },
        })),

      getSelectedItems: (key) => get().selectedItems[key] ?? [],

      clearSelectedItems: (key) =>
        set((state) => {
          const { [key]: _removed, ...rest } = state.selectedItems;
          void _removed; // Intentionally discarded
          return { selectedItems: rest };
        }),

      // Last visited route management
      setLastVisited: (section, route) =>
        set((state) => ({
          lastVisited: { ...state.lastVisited, [section]: route },
        })),

      getLastVisited: (section) => get().lastVisited[section],

      setTaskListNav: (context) => set({ taskListNav: context }),
    }),
    {
      name: "roboco-ui-state",
      storage: createJSONStorage(() => sessionStorage),
      // Only persist certain keys
      partialize: (state) => ({
        scrollPositions: state.scrollPositions,
        expandedSections: state.expandedSections,
        lastVisited: state.lastVisited,
        taskListNav: state.taskListNav,
        // Don't persist selectedItems - they're temporary
      }),
    },
  ),
);
