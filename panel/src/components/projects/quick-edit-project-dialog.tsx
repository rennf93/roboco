"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useProject, useUpdateProject } from "@/hooks/use-projects";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DIALOG_SIZES,
} from "@/components/ui/dialog";
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
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import { toast } from "sonner";
import { Team, type ProjectUpdate, type Project } from "@/types";

const cells: { value: Team; label: string }[] = [
  { value: Team.BACKEND, label: "Backend" },
  { value: Team.FRONTEND, label: "Frontend" },
  { value: Team.UX_UI, label: "UX/UI" },
];

interface QuickEditProjectDialogProps {
  projectId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

// Inner form component — receives project directly, manages its own state.
function QuickEditProjectForm({
  project,
  onSuccess,
  onCancel,
}: {
  project: Project;
  onSuccess: () => void;
  onCancel: () => void;
}) {
  const router = useRouter();
  const updateProject = useUpdateProject();

  const [name, setName] = useState(project.name);
  const [assignedCell, setAssignedCell] = useState(project.assigned_cell);
  const [isActive, setIsActive] = useState(project.is_active);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!name || !assignedCell) {
      toast.error("Please fill in all required fields");
      return;
    }

    const updates: ProjectUpdate = {
      name,
      assigned_cell: assignedCell,
      is_active: isActive,
    };

    try {
      await updateProject.mutateAsync({ projectId: project.id, updates });
      toast.success("Project updated successfully");
      onSuccess();
    } catch (error) {
      toast.error(
        `Failed to update project: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  };

  return (
    <form onSubmit={handleSubmit}>
      <DialogHeader>
        <DialogTitle>Edit Project</DialogTitle>
        <DialogDescription>
          Quick edits only — everything else (git, environments, CI/CD, budget,
          sandbox, conventions) lives on the full settings page.
        </DialogDescription>
      </DialogHeader>
      <div className="grid gap-4 py-4">
        <div className="grid gap-2">
          <HelpTip label="Display name shown across the panel and CEO approval queues; renaming it never touches the slug or workspace path.">
            <Label htmlFor="name">Project Name *</Label>
          </HelpTip>
          <Input
            id="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="RoboCo API"
          />
        </div>

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
      </div>
      <DialogFooter className="sm:justify-between">
        <HelpTip label="Git auth, placement, environments, CI/CD commands, budget & ops, sandbox, and conventions">
          <Button
            type="button"
            variant="link"
            className="h-auto p-0 text-muted-foreground"
            onClick={() => {
              onCancel();
              router.push(`/projects/${project.id}/settings`);
            }}
          >
            Full settings →
          </Button>
        </HelpTip>
        <div className="flex gap-2">
          <Button type="button" variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <Button type="submit" disabled={updateProject.isPending}>
            {updateProject.isPending ? "Saving..." : "Save Changes"}
          </Button>
        </div>
      </DialogFooter>
    </form>
  );
}

// Slim in-list dialog for the three fields worth a one-click edit without
// leaving the projects list; everything else lives on the full settings page
// (/projects/[id]/settings, reached via the pencil action or "Full settings").
export function QuickEditProjectDialog({
  projectId,
  open,
  onOpenChange,
}: QuickEditProjectDialogProps) {
  const { data: project, isLoading } = useProject(projectId);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={DIALOG_SIZES.sm}>
        {isLoading ? (
          <div className="space-y-4 py-4">
            <Skeleton className="h-8 w-48" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : project ? (
          <QuickEditProjectForm
            key={project.id}
            project={project}
            onSuccess={() => onOpenChange(false)}
            onCancel={() => onOpenChange(false)}
          />
        ) : (
          <div className="py-8 text-center text-muted-foreground">
            Project not found
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
