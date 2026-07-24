import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { tasksApi } from "@/lib/api/tasks";
import { taskKeys } from "@/hooks/use-tasks";
import { useAgentDefinitions } from "@/hooks/use-agents";
import { useProjects } from "@/hooks/use-projects";
import { useUIStore } from "@/store";
import { navItems } from "@/components/layout/sidebar";
import { fuzzyScore } from "@/lib/fuzzy-match";
import {
  addRecent,
  loadRecents,
  type CommandRecentType,
} from "@/lib/command-palette-recents";

export interface CommandItem {
  type: CommandRecentType;
  id: string;
  title: string;
  subtitle?: string;
  href: string;
}

const MAX_RESULTS_PER_GROUP = 6;
const DEBOUNCE_MS = 150;

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

function scoreAndSort<T>(
  query: string,
  items: T[],
  getLabel: (item: T) => string,
): T[] {
  return items
    .map((item) => ({ item, score: fuzzyScore(query, getLabel(item)) }))
    .filter((entry): entry is { item: T; score: number } => entry.score !== null)
    .sort((a, b) => a.score - b.score)
    .slice(0, MAX_RESULTS_PER_GROUP)
    .map((entry) => entry.item);
}

/** A group of command items with a section label, in fixed display order. */
export interface CommandGroup {
  label: string;
  items: CommandItem[];
}

/**
 * Data + keyboard-navigation state for the command palette: fuzzy search
 * across tasks/agents/projects/pages (or localStorage recents when the
 * query is empty), a flat selectable index across every visible item, and
 * navigation that records the pick as a recent before routing to it.
 */
export function useCommandPalette() {
  const open = useUIStore((s) => s.commandPaletteOpen);
  const setOpen = useUIStore((s) => s.setCommandPaletteOpen);
  const router = useRouter();

  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const debouncedQuery = useDebouncedValue(query, DEBOUNCE_MS);
  const trimmedQuery = debouncedQuery.trim();

  // Typing a new query jumps the selection back to the top match; this runs
  // from the input's onChange event, not an effect, so there's no
  // render-triggers-setState-triggers-render cascade.
  const handleQueryChange = useCallback((value: string) => {
    setQuery(value);
    setSelectedIndex(0);
  }, []);

  // Dialog open/close is driven by Radix's onOpenChange event. Closing also
  // resets the transient query/selection so the next open starts fresh.
  const setDialogOpen = useCallback(
    (next: boolean) => {
      setOpen(next);
      if (!next) {
        setQuery("");
        setSelectedIndex(0);
      }
    },
    [setOpen],
  );

  // Tasks: the backend already fuzzy-searches title/description/id-prefix
  // (see TaskFilters.q), so this is a plain server-side query, only run
  // while the dialog is open and the query is non-empty.
  const { data: taskResults = [] } = useQuery({
    queryKey: taskKeys.list({ q: trimmedQuery, limit: MAX_RESULTS_PER_GROUP }),
    queryFn: () =>
      tasksApi.list({ q: trimmedQuery, limit: MAX_RESULTS_PER_GROUP }),
    enabled: open && trimmedQuery.length > 0,
    staleTime: 30000,
  });

  const { data: agentDefinitions = [] } = useAgentDefinitions();
  const { data: projects = [] } = useProjects();

  const groups = useMemo<CommandGroup[]>(() => {
    if (!trimmedQuery) {
      // Read directly (no cached state) each time this recomputes — cheap,
      // and it means a pick made just before closing shows up immediately
      // the next time the dialog opens.
      const recents = open ? loadRecents() : [];
      return [
        {
          label: "Recent",
          items: recents.map((r) => ({ ...r, href: hrefFor(r.type, r.id) })),
        },
      ];
    }

    const taskItems: CommandItem[] = taskResults.map((t) => ({
      type: "task",
      id: t.id,
      title: t.title,
      subtitle: `#${t.id.slice(0, 8)}`,
      href: hrefFor("task", t.id),
    }));

    const agentItems: CommandItem[] = scoreAndSort(
      trimmedQuery,
      agentDefinitions,
      (a) => `${a.name} ${a.id}`,
    ).map((a) => ({
      type: "agent",
      id: a.id,
      title: a.name,
      subtitle: `@${a.id}`,
      href: hrefFor("agent", a.id),
    }));

    // No dedicated project detail route exists, so the project's `id` here
    // is its name — the value hrefFor uses to build the /projects?q= filter
    // link — kept consistent between a fresh search hit and a replayed
    // recent (which only carries {type, id, title}, not the full project).
    const projectItems: CommandItem[] = scoreAndSort(
      trimmedQuery,
      projects,
      (p) => p.name,
    ).map((p) => ({
      type: "project",
      id: p.name,
      title: p.name,
      subtitle: p.slug,
      href: hrefFor("project", p.name),
    }));

    const pageItems: CommandItem[] = scoreAndSort(
      trimmedQuery,
      navItems,
      (p) => p.title,
    ).map((p) => ({
      type: "page",
      id: p.href,
      title: p.title,
      href: p.href,
    }));

    return [
      { label: "Tasks", items: taskItems },
      { label: "Agents", items: agentItems },
      { label: "Projects", items: projectItems },
      { label: "Pages", items: pageItems },
    ];
  }, [trimmedQuery, taskResults, agentDefinitions, projects, open]);

  const flatItems = useMemo(
    () => groups.flatMap((g) => g.items),
    [groups],
  );

  const moveSelection = useCallback(
    (delta: number) => {
      if (flatItems.length === 0) return;
      setSelectedIndex((prev) => {
        const next = (prev + delta + flatItems.length) % flatItems.length;
        return next;
      });
    },
    [flatItems.length],
  );

  const navigateTo = useCallback(
    (item: CommandItem) => {
      addRecent({ type: item.type, id: item.id, title: item.title });
      setDialogOpen(false);
      router.push(item.href);
    },
    [router, setDialogOpen],
  );

  const selectCurrent = useCallback(() => {
    const item = flatItems[selectedIndex];
    if (item) navigateTo(item);
  }, [flatItems, selectedIndex, navigateTo]);

  return {
    open,
    setOpen: setDialogOpen,
    query,
    setQuery: handleQueryChange,
    groups,
    flatItems,
    selectedIndex,
    moveSelection,
    selectCurrent,
    navigateTo,
  };
}

function hrefFor(type: CommandRecentType, id: string): string {
  switch (type) {
    case "task":
      return `/tasks/${id}`;
    case "agent":
      return `/agents/${id}`;
    case "project":
      // No dedicated project detail route exists — filter the list to it.
      return `/projects?q=${encodeURIComponent(id)}`;
    case "page":
      return id;
  }
}
