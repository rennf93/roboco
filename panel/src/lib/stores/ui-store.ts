/**
 * UI State Store
 *
 * Persists UI state across navigation using Zustand with sessionStorage.
 * This handles state that doesn't belong in URL params but should survive navigation.
 */

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

interface ScrollPosition {
  x: number;
  y: number;
}

interface UIState {
  // Scroll positions per route
  scrollPositions: Record<string, ScrollPosition>;

  // Expanded/collapsed state for accordions, cards, etc.
  expandedSections: Record<string, boolean>;

  // Selected items that aren't in URL (e.g., multi-select temporary state)
  selectedItems: Record<string, string[]>;

  // Last visited routes per section (for "back" behavior)
  lastVisited: Record<string, string>;

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
}

export const useUIStore = create<UIState>()(
  persist(
    (set, get) => ({
      scrollPositions: {},
      expandedSections: {},
      selectedItems: {},
      lastVisited: {},

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
          expandedSections: { ...state.expandedSections, [sectionId]: expanded },
        })),

      isSectionExpanded: (sectionId) => get().expandedSections[sectionId] ?? true,

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
    }),
    {
      name: "roboco-ui-state",
      storage: createJSONStorage(() => sessionStorage),
      // Only persist certain keys
      partialize: (state) => ({
        scrollPositions: state.scrollPositions,
        expandedSections: state.expandedSections,
        lastVisited: state.lastVisited,
        // Don't persist selectedItems - they're temporary
      }),
    }
  )
);
