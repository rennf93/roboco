"use client";

import { useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { HelpTip } from "@/components/ui/help-tip";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ArrowDown, ArrowUp, Pencil, RotateCcw } from "lucide-react";
import { useUIStore } from "@/store";
import {
  QUICK_ACTIONS_REGISTRY,
  resolveQuickActions,
} from "./quick-actions-registry";

// Per-user honesty: the panel is a single-operator surface today. This
// customization is persisted per-browser via the existing localStorage-backed
// useUIStore — different browsers/profiles keep their own chosen set, which
// IS the "different people need different quick access buttons" mechanism
// for now. No server-side per-user storage is built.

/**
 * Compact icon+label grid of the CEO's chosen quick actions, in their chosen
 * order — replaces the old hardcoded QuickActionsBar. A gear/pencil affordance
 * opens the customize dialog (below) to pick which actions show and reorder
 * them.
 */
export function QuickActionsCard() {
  const quickActionIds = useUIStore((s) => s.quickActionIds);
  const actions = resolveQuickActions(quickActionIds);

  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="grid flex-1 grid-cols-[repeat(auto-fill,minmax(9rem,1fr))] gap-2">
        {actions.map((action) => (
          <HelpTip key={action.id} label={action.tip}>
            <Link href={action.href} prefetch={false}>
              <Button
                variant="outline"
                className="h-auto w-full flex-col gap-1.5 py-3"
              >
                <action.icon className="h-5 w-5" />
                <span className="text-xs font-medium">{action.label}</span>
              </Button>
            </Link>
          </HelpTip>
        ))}
      </div>
      <QuickActionsCustomizeDialog />
    </div>
  );
}

const CUSTOMIZE_LABEL = "Customize Quick Actions";

function QuickActionsCustomizeDialog() {
  const [open, setOpen] = useState(false);
  const quickActionIds = useUIStore((s) => s.quickActionIds);
  const setQuickActionIds = useUIStore((s) => s.setQuickActionIds);
  const resetQuickActionIds = useUIStore((s) => s.resetQuickActionIds);

  // Stale ids (an action removed from the registry since it was picked) are
  // dropped here too — the dialog only ever shows/reorders real actions.
  const enabledIds = resolveQuickActions(quickActionIds).map((a) => a.id);
  const enabledSet = new Set(enabledIds);
  // Enabled actions first (in the user's chosen order), then everything else
  // available to add, in registry order.
  const orderedIds = [
    ...enabledIds,
    ...QUICK_ACTIONS_REGISTRY.filter((a) => !enabledSet.has(a.id)).map(
      (a) => a.id,
    ),
  ];

  const toggle = (id: string) => {
    if (enabledSet.has(id)) {
      setQuickActionIds(enabledIds.filter((x) => x !== id));
    } else {
      setQuickActionIds([...enabledIds, id]);
    }
  };

  const move = (id: string, direction: -1 | 1) => {
    const index = enabledIds.indexOf(id);
    if (index === -1) return;
    const target = index + direction;
    if (target < 0 || target >= enabledIds.length) return;
    const next = [...enabledIds];
    [next[index], next[target]] = [next[target], next[index]];
    setQuickActionIds(next);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <HelpTip label={CUSTOMIZE_LABEL}>
        <DialogTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            aria-label={CUSTOMIZE_LABEL}
            title={CUSTOMIZE_LABEL}
          >
            <Pencil className="h-4 w-4" />
          </Button>
        </DialogTrigger>
      </HelpTip>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Customize Quick Actions</DialogTitle>
          <DialogDescription>
            Pick which shortcuts show on the Overview dashboard and reorder
            them with the arrows. Saved to this browser only.
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-96 space-y-1 overflow-y-auto">
          {orderedIds.map((id) => {
            const action = QUICK_ACTIONS_REGISTRY.find((a) => a.id === id);
            if (!action) return null;
            const isEnabled = enabledSet.has(id);
            const index = enabledIds.indexOf(id);
            const isFirst = index === 0;
            const isLast = index === enabledIds.length - 1;
            return (
              <div
                key={id}
                className="flex items-center gap-2 rounded-md px-1 py-1.5"
              >
                <Checkbox
                  checked={isEnabled}
                  onCheckedChange={() => toggle(id)}
                  aria-label={`Show ${action.label}`}
                />
                <action.icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                <HelpTip label={action.tip}>
                  <span className="flex-1 truncate text-sm">
                    {action.label}
                  </span>
                </HelpTip>
                {isEnabled && (
                  <div className="flex shrink-0">
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="inline-block">
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="h-6 w-6"
                            disabled={isFirst}
                            onClick={() => move(id, -1)}
                            aria-label={`Move ${action.label} earlier`}
                          >
                            <ArrowUp className="h-3.5 w-3.5" />
                          </Button>
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>Move earlier</TooltipContent>
                    </Tooltip>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="inline-block">
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="h-6 w-6"
                            disabled={isLast}
                            onClick={() => move(id, 1)}
                            aria-label={`Move ${action.label} later`}
                          >
                            <ArrowDown className="h-3.5 w-3.5" />
                          </Button>
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>Move later</TooltipContent>
                    </Tooltip>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={resetQuickActionIds}
        >
          <RotateCcw className="h-4 w-4 mr-1.5" />
          Reset to defaults
        </Button>
      </DialogContent>
    </Dialog>
  );
}
