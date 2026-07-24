"use client";

import { useEffect, useRef, type KeyboardEvent } from "react";
import { Search, ListTodo, Bot, FolderGit2, Compass } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  useCommandPalette,
  type CommandItem,
} from "@/hooks/use-command-palette";
import type { CommandRecentType } from "@/lib/command-palette-recents";

const TYPE_ICON: Record<CommandRecentType, typeof ListTodo> = {
  task: ListTodo,
  agent: Bot,
  project: FolderGit2,
  page: Compass,
};

/**
 * Global Cmd+K / Ctrl+K command palette: fuzzy search over tasks, agents,
 * projects, and nav pages, falling back to localStorage recents when the
 * query is empty. Mounted once (dashboard layout) so the hotkey works on
 * every page; a click trigger elsewhere can open it via the shared
 * `useUIStore().setCommandPaletteOpen` action instead of duplicating state.
 */
export function CommandPalette() {
  const {
    open,
    setOpen,
    query,
    setQuery,
    groups,
    flatItems,
    selectedIndex,
    moveSelection,
    selectCurrent,
    navigateTo,
  } = useCommandPalette();
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function handleKeyDown(e: globalThis.KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(true);
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [setOpen]);

  function handleInputKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      moveSelection(1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      moveSelection(-1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      selectCurrent();
    }
    // Escape closes via Radix Dialog's built-in behavior — nothing to do here.
  }

  let renderedIndex = -1;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent
        showCloseButton={false}
        className="top-[20%] max-w-xl translate-y-0 gap-0 overflow-hidden p-0"
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          inputRef.current?.focus();
        }}
      >
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <DialogDescription className="sr-only">
          Search tasks, agents, projects, and pages, then press Enter to
          navigate.
        </DialogDescription>
        <div className="flex items-center gap-2 border-b px-4">
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
          <Input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleInputKeyDown}
            placeholder="Search tasks, agents, projects, pages..."
            className="h-12 border-0 shadow-none focus-visible:ring-0"
            role="combobox"
            aria-expanded={open}
            aria-controls="command-palette-listbox"
            aria-activedescendant={
              flatItems.length > 0
                ? `command-palette-item-${selectedIndex}`
                : undefined
            }
          />
        </div>
        <div
          id="command-palette-listbox"
          role="listbox"
          className="max-h-80 overflow-y-auto p-2"
        >
          {flatItems.length === 0 ? (
            <p className="px-2 py-6 text-center text-sm text-muted-foreground">
              {query.trim() ? "No results" : "No recent items yet"}
            </p>
          ) : (
            groups.map((group) =>
              group.items.length === 0 ? null : (
                <div key={group.label} className="mb-2 last:mb-0">
                  <p className="px-2 py-1 text-xs font-medium text-muted-foreground">
                    {group.label}
                  </p>
                  {group.items.map((item: CommandItem) => {
                    renderedIndex += 1;
                    const index = renderedIndex;
                    const Icon = TYPE_ICON[item.type];
                    return (
                      <button
                        key={`${item.type}-${item.id}`}
                        id={`command-palette-item-${index}`}
                        type="button"
                        role="option"
                        aria-selected={index === selectedIndex}
                        onClick={() => navigateTo(item)}
                        className={cn(
                          "flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm",
                          index === selectedIndex
                            ? "bg-accent text-accent-foreground"
                            : "hover:bg-muted",
                        )}
                      >
                        <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                        <span className="flex-1 truncate">{item.title}</span>
                        {item.subtitle && (
                          <span className="shrink-0 text-xs text-muted-foreground">
                            {item.subtitle}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              ),
            )
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
