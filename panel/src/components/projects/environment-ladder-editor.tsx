"use client";

import { Button } from "@/components/ui/button";
import { HelpTip } from "@/components/ui/help-tip";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ArrowDown, ArrowUp, Plus, X } from "lucide-react";
import type { EnvironmentRung } from "@/types";

interface EnvironmentLadderEditorProps {
  // null/empty => degenerate 1-rung ladder synthesized from default_branch.
  rungs: EnvironmentRung[] | null;
  onChange: (rungs: EnvironmentRung[] | null) => void;
}

// An empty editor (no rungs) means "inherit default_branch" via the backend
// shim, so we keep a plain array internally and emit null when it empties.
function toRungs(value: EnvironmentRung[] | null): EnvironmentRung[] {
  return value ?? [];
}

export function EnvironmentLadderEditor({
  rungs,
  onChange,
}: EnvironmentLadderEditorProps) {
  const items = toRungs(rungs);

  const emit = (next: EnvironmentRung[]) => {
    onChange(next.length ? next : null);
  };

  const handleAdd = () => {
    emit([...items, { name: "", branch: "" }]);
  };

  const handleRemove = (index: number) => {
    emit(items.filter((_, i) => i !== index));
  };

  const handleUpdate = (index: number, field: keyof EnvironmentRung, value: string) => {
    const next = items.map((r, i) => (i === index ? { ...r, [field]: value } : r));
    emit(next);
  };

  const handleMove = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= items.length) return;
    const next = [...items];
    [next[index], next[target]] = [next[target], next[index]];
    emit(next);
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <HelpTip
          label={
            "Maps this project's branches to a promotion flow. Typical two-branch setup: " +
            "rung 1 name 'dev' branch 'slave' (all PRs land there), rung 2 name 'prod' " +
            "branch 'master' (releases are cut and tagged there). Add middle rungs (qa, " +
            "staging) only if those branches really exist. No ladder = the default " +
            "branch plays both roles."
          }
        >
          <Label>Environment ladder</Label>
        </HelpTip>
        <span className="text-xs text-muted-foreground">
          {items.length} rung{items.length !== 1 ? "s" : ""}
        </span>
      </div>

      {items.length > 0 && (
        <div className="space-y-2 border rounded-lg p-3 bg-muted/30">
          {items.map((rung, index) => {
            const isFirst = index === 0;
            const isLast = index === items.length - 1;
            const role = isFirst
              ? isLast
                ? "PRs + release"
                : "PRs land"
              : isLast
                ? "release"
                : "";
            return (
              <div key={index}>
                {!isFirst && (
                  <div className="flex items-center gap-1 py-0.5 pl-16 text-[10px] text-muted-foreground">
                    <ArrowDown className="h-3 w-3" />
                    promotes to
                  </div>
                )}
                <div className="flex items-center gap-2">
                <div className="flex flex-col">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      {/* span-wrap: disabled Button has pointer-events-none,
                          so the tooltip needs a hoverable wrapper to fire
                          when isFirst disables the button. */}
                      <span className="inline-block">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-6 w-6"
                          disabled={isFirst}
                          onClick={() => handleMove(index, -1)}
                          aria-label="Move earlier in the flow"
                        >
                          <ArrowUp className="h-3.5 w-3.5" />
                        </Button>
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>
                      {isFirst
                        ? "Already first — lands PRs, nothing to promote from."
                        : "Move earlier in the flow"}
                    </TooltipContent>
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
                          onClick={() => handleMove(index, 1)}
                          aria-label="Move later in the flow"
                        >
                          <ArrowDown className="h-3.5 w-3.5" />
                        </Button>
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>
                      {isLast
                        ? "Already last — the release target, nothing further to promote to."
                        : "Move later in the flow"}
                    </TooltipContent>
                  </Tooltip>
                </div>
                <HelpTip
                  label={
                    role
                      ? `This rung ${role === "PRs land" ? "is where PRs from this project land" : role === "release" ? "is where releases are cut and tagged" : "both receives PRs and is the release target (single-rung ladder)"}.`
                      : "An intermediate rung — merged into from the rung above, promoted to the rung below."
                  }
                >
                  <span className="w-20 text-center text-[10px] font-medium uppercase text-muted-foreground">
                    {role}
                  </span>
                </HelpTip>
                <HelpTip label="Display label for this rung (e.g. 'dev', 'qa') — shown in the panel only, not matched against branch names.">
                  <span className="flex-1">
                    <Input
                      value={rung.name}
                      onChange={(e) => handleUpdate(index, "name", e.target.value)}
                      placeholder="Name (e.g. dev, qa, stag)"
                      className="h-8"
                    />
                  </span>
                </HelpTip>
                <HelpTip label="The real git branch this rung maps to. Must be unique across rungs; saving rejects an empty or duplicate branch.">
                  <span className="flex-1">
                    <Input
                      value={rung.branch}
                      onChange={(e) => handleUpdate(index, "branch", e.target.value)}
                      placeholder="Branch (e.g. dev, master)"
                      className="h-8"
                    />
                  </span>
                </HelpTip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8 shrink-0"
                      onClick={() => handleRemove(index)}
                      aria-label="Remove this rung"
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Remove this rung</TooltipContent>
                </Tooltip>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <HelpTip label="Appends a blank rung at the bottom of the ladder (the new release target) — fill in its name/branch, then reorder with the arrows.">
        <Button type="button" variant="outline" size="sm" onClick={handleAdd}>
          <Plus className="h-4 w-4 mr-1" />
          Add rung
        </Button>
      </HelpTip>

      <p className="text-xs text-muted-foreground">
        Top to bottom is the promotion flow: <strong>PRs land</strong> on the
        first branch, each rung promotes to the next, and{" "}
        <strong>releases are cut</strong> from the last — e.g.{" "}
        <code>dev → qa → staging → prod</code>. Leave empty to use{" "}
        <em>default branch</em> for everything; when set, this overrides it for
        both the PR target and the release target.
      </p>
    </div>
  );
}