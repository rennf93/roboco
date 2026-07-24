"use client";

import { useState } from "react";
import { useUpdateProject } from "@/hooks/use-projects";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { HelpTip } from "@/components/ui/help-tip";
import { MapPin, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { Team, type Project, type ProjectUpdate } from "@/types";
import { SaveBar } from "./save-bar";

const cells: { value: Team; label: string }[] = [
  { value: Team.BACKEND, label: "Backend" },
  { value: Team.FRONTEND, label: "Frontend" },
  { value: Team.UX_UI, label: "UX/UI" },
];

export function PlacementCard({ project }: { project: Project }) {
  const updateProject = useUpdateProject();

  const [assignedCell, setAssignedCell] = useState(project.assigned_cell);
  const [defaultBranch, setDefaultBranch] = useState(project.default_branch);
  const [protectedBranches, setProtectedBranches] = useState<string[]>(
    project.protected_branches,
  );
  const [protectedBranchInput, setProtectedBranchInput] = useState("");
  const [isActive, setIsActive] = useState(project.is_active);

  // Shared by the single Enter/comma-key add and the multi-value paste
  // handler below — trims, drops empties, and dedups against both the
  // existing list and duplicates within the same batch.
  const addProtectedBranches = (names: string[]) => {
    const cleaned = names.map((n) => n.trim()).filter(Boolean);
    if (cleaned.length === 0) return;
    setProtectedBranches((prev) => {
      const next = [...prev];
      for (const name of cleaned) {
        if (!next.includes(name)) next.push(name);
      }
      return next;
    });
  };
  const addProtectedBranch = () => {
    addProtectedBranches([protectedBranchInput]);
    setProtectedBranchInput("");
  };
  const handleProtectedBranchPaste = (
    e: React.ClipboardEvent<HTMLInputElement>,
  ) => {
    const pasted = e.clipboardData.getData("text");
    // A single name (no comma) falls through to normal paste-into-input
    // behavior; only a multi-value paste is split into chips directly —
    // otherwise "release,hotfix,staging" lands as one malformed chip.
    if (!pasted.includes(",")) return;
    e.preventDefault();
    addProtectedBranches(pasted.split(","));
    setProtectedBranchInput("");
  };
  const removeProtectedBranch = (branch: string) => {
    setProtectedBranches((prev) => prev.filter((b) => b !== branch));
  };

  const dirty =
    assignedCell !== project.assigned_cell ||
    defaultBranch !== project.default_branch ||
    isActive !== project.is_active ||
    protectedBranches.length !== project.protected_branches.length ||
    protectedBranches.some((b, i) => b !== project.protected_branches[i]);

  const handleSave = async () => {
    if (!assignedCell) {
      toast.error("Assigned cell is required");
      return;
    }

    const updates: ProjectUpdate = {
      assigned_cell: assignedCell,
      default_branch: defaultBranch || "main",
      protected_branches: protectedBranches,
      is_active: isActive,
    };

    try {
      await updateProject.mutateAsync({ projectId: project.id, updates });
      toast.success("Placement & branches updated");
    } catch (error) {
      toast.error(
        `Failed to update: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MapPin className="h-5 w-5" />
          Placement & Branches
        </CardTitle>
        <CardDescription>
          Cell ownership, the default branch, and protected branches
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-2">
          <HelpTip label="Which cell owns this project — only that cell's agents can claim its tasks (enforced server-side, not just a UI filter).">
            <Label htmlFor="assigned_cell">Assigned Cell *</Label>
          </HelpTip>
          <Select
            value={assignedCell}
            onValueChange={(value: Team) => setAssignedCell(value)}
          >
            <SelectTrigger>
              <SelectValue placeholder="Select cell" />
            </SelectTrigger>
            <SelectContent>
              {cells.map((cell) => (
                <SelectItem key={cell.value} value={cell.value}>
                  {cell.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="grid gap-2">
          <HelpTip label="Used as both head and prod when no environment ladder is set (a degenerate single-rung ladder) — the PR review gate diffs against it and releases cut from it.">
            <Label htmlFor="default_branch">Default Branch</Label>
          </HelpTip>
          <Input
            id="default_branch"
            value={defaultBranch}
            onChange={(e) => setDefaultBranch(e.target.value)}
            placeholder="main"
          />
          <p className="text-xs text-muted-foreground">
            Where PRs land and releases are cut when no environment ladder is
            set (Environments card below).
          </p>
        </div>

        <div className="grid gap-2">
          <HelpTip label="Branches the fleet refuses to rebase onto or sync (force-push) as a task's own branch, in addition to the always-protected master/main defaults — matched exactly, case-sensitive. Every remote branch delete (task-branch cleanup, the stale-branch sweep, and a merged PR's source-branch cleanup) additionally refuses any environment-ladder rung, even one not listed here.">
            <Label htmlFor="protected_branch_input">Protected Branches</Label>
          </HelpTip>
          {protectedBranches.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {protectedBranches.map((branch) => (
                <Badge key={branch} variant="secondary" className="gap-1 pr-1">
                  {branch}
                  <button
                    type="button"
                    onClick={() => removeProtectedBranch(branch)}
                    aria-label={`Remove ${branch}`}
                    className="rounded-full hover:bg-muted-foreground/20"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </Badge>
              ))}
            </div>
          )}
          <div className="flex gap-2">
            <Input
              id="protected_branch_input"
              value={protectedBranchInput}
              onChange={(e) => setProtectedBranchInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === ",") {
                  e.preventDefault();
                  addProtectedBranch();
                }
              }}
              onPaste={handleProtectedBranchPaste}
              placeholder="Type a branch name, press Enter"
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={addProtectedBranch}
            >
              <Plus className="h-4 w-4" />
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Enter or comma adds a branch; click the × on a chip to remove it.
          </p>
        </div>

        <div className="flex items-center justify-between">
          <HelpTip label="Inactive projects are hidden from the default project list (toggle 'Show Inactive' to see them) and are skipped as the fallback project for idle-agent spawns.">
            <Label htmlFor="is_active">Active</Label>
          </HelpTip>
          <Switch
            id="is_active"
            checked={isActive}
            onCheckedChange={setIsActive}
          />
        </div>

        <SaveBar
          dirty={dirty}
          pending={updateProject.isPending}
          onSave={handleSave}
        />
      </CardContent>
    </Card>
  );
}
