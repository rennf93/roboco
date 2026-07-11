"use client";

import { useEffect } from "react";
import { Task } from "@/types";
import { useScrollRestorationStore } from "@/lib/stores";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ChevronLeft, ChevronRight } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

interface TaskListNavProps {
  task: Task;
}

const NO_CONTEXT_TOOLTIP =
  "Open this task from the Tasks list to enable prev/next navigation within that list's filter/sort order.";

// Don't hijack Alt+Arrow while the user is typing (input/textarea/contenteditable).
function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.isContentEditable
  );
}

// Moves to the adjacent task within the Tasks list order the user last
// visited (filters + sort applied), captured in useScrollRestorationStore by
// the Tasks list page. Documented fallback: when no list context exists for
// this session (task opened via a direct link, search result, notification,
// etc.) or the current task isn't part of the captured order (it was
// navigated to some other way), both buttons render disabled with a tooltip
// explaining why — there is no list order to fall back to, so we don't guess.
export function TaskListNav({ task }: TaskListNavProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const context = useScrollRestorationStore((state) => state.taskListNav);

  const items = context?.items ?? [];
  const index = items.findIndex((item) => item.id === task.id);
  const hasContext = index !== -1;
  const prevItem = hasContext && index > 0 ? items[index - 1] : null;
  const nextItem =
    hasContext && index < items.length - 1 ? items[index + 1] : null;

  // Carry the active detail tab (?tab=) into the adjacent task's URL so
  // prev/next keeps the user on the same tab.
  const params = new URLSearchParams(context?.queryString ?? "");
  const activeTab = searchParams.get("tab");
  if (activeTab) params.set("tab", activeTab);
  else params.delete("tab");
  const qs = params.toString();
  const query = qs ? `?${qs}` : "";

  // Alt+ArrowLeft/Right mirrors the prev/next buttons above.
  useEffect(() => {
    const handleKeydown = (e: KeyboardEvent) => {
      if (!e.altKey || isEditableTarget(e.target)) return;
      if (e.key === "ArrowLeft" && prevItem) {
        e.preventDefault();
        router.push(`/tasks/${prevItem.id}${query}`);
      } else if (e.key === "ArrowRight" && nextItem) {
        e.preventDefault();
        router.push(`/tasks/${nextItem.id}${query}`);
      }
    };
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, [prevItem, nextItem, query, router]);

  return (
    <div className="flex items-center gap-1 shrink-0">
      <NavButton
        direction="prev"
        item={prevItem}
        query={query}
        disabledReason={!hasContext ? NO_CONTEXT_TOOLTIP : undefined}
      />
      <NavButton
        direction="next"
        item={nextItem}
        query={query}
        disabledReason={!hasContext ? NO_CONTEXT_TOOLTIP : undefined}
      />
    </div>
  );
}

function NavButton({
  direction,
  item,
  query,
  disabledReason,
}: {
  direction: "prev" | "next";
  item: { id: string; title: string } | null;
  query: string;
  disabledReason?: string;
}) {
  const Icon = direction === "prev" ? ChevronLeft : ChevronRight;
  const label = direction === "prev" ? "Previous task" : "Next task";
  const disabled = item === null;

  const button = (
    <Button
      variant="outline"
      size="icon"
      disabled={disabled}
      aria-label={label}
      asChild={!disabled}
    >
      {disabled ? (
        <Icon className="h-4 w-4" />
      ) : (
        <Link href={`/tasks/${item.id}${query}`} prefetch={false}>
          <Icon className="h-4 w-4" />
        </Link>
      )}
    </Button>
  );

  const tooltipText = disabledReason ?? item?.title ?? label;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span>{button}</span>
      </TooltipTrigger>
      <TooltipContent>{tooltipText}</TooltipContent>
    </Tooltip>
  );
}
