"use client";

import { Button } from "@/components/ui/button";
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
        <Label>Environment ladder</Label>
        <span className="text-xs text-muted-foreground">
          {items.length} rung{items.length !== 1 ? "s" : ""}
        </span>
      </div>

      {items.length > 0 && (
        <div className="space-y-2 border rounded-lg p-3 bg-muted/30">
          {items.map((rung, index) => {
            const isFirst = index === 0;
            const isLast = index === items.length - 1;
            return (
              <div key={index} className="flex items-center gap-2">
                <div className="flex flex-col">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6"
                        disabled={isFirst}
                        onClick={() => handleMove(index, -1)}
                        aria-label="Move rung up, toward head"
                      >
                        <ArrowUp className="h-3.5 w-3.5" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Move up (toward head)</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6"
                        disabled={isLast}
                        onClick={() => handleMove(index, 1)}
                        aria-label="Move rung down, toward prod"
                      >
                        <ArrowDown className="h-3.5 w-3.5" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Move down (toward prod)</TooltipContent>
                  </Tooltip>
                </div>
                <span className="text-[10px] uppercase text-muted-foreground w-10 text-center">
                  {isFirst ? "head" : isLast ? "prod" : `rung ${index + 1}`}
                </span>
                <Input
                  value={rung.name}
                  onChange={(e) => handleUpdate(index, "name", e.target.value)}
                  placeholder="Name (e.g. dev, qa, stag)"
                  className="flex-1 h-8"
                />
                <Input
                  value={rung.branch}
                  onChange={(e) => handleUpdate(index, "branch", e.target.value)}
                  placeholder="Branch (e.g. dev, master)"
                  className="flex-1 h-8"
                />
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
            );
          })}
        </div>
      )}

      <Button type="button" variant="outline" size="sm" onClick={handleAdd}>
        <Plus className="h-4 w-4 mr-1" />
        Add rung
      </Button>

      <p className="text-xs text-muted-foreground">
        Ordered top→bottom: the first rung is <strong>head</strong> (where dev PRs
        land) and the last is <strong>prod</strong> (the release target). Leave
        empty to inherit <em>default branch</em> for both — e.g.{" "}
        <code>dev → qa → stag → prod</code>, or just <code>prod</code> for a
        single-branch project. When set, this overrides <em>default branch</em>{" "}
        for the PR target and the release target.
      </p>
    </div>
  );
}