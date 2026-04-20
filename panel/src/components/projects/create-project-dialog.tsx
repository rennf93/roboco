"use client";

import { useState } from "react";
import { useCreateProject } from "@/hooks/use-projects";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
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
import { Plus, Key } from "lucide-react";
import { toast } from "sonner";
import { Team, type ProjectCreate } from "@/types";

const cells: { value: Team; label: string }[] = [
  { value: Team.BACKEND, label: "Backend" },
  { value: Team.FRONTEND, label: "Frontend" },
  { value: Team.UX_UI, label: "UX/UI" },
];

// Generate slug from name
function generateSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function CreateProjectDialog() {
  const [open, setOpen] = useState(false);
  const [formData, setFormData] = useState<Partial<ProjectCreate>>({
    name: "",
    slug: "",
    git_url: "",
    git_token: "",
    assigned_cell: Team.BACKEND,
    default_branch: "main",
  });
  const [showAdvanced, setShowAdvanced] = useState(false);

  const createProject = useCreateProject();

  const handleNameChange = (name: string) => {
    setFormData({
      ...formData,
      name,
      slug: generateSlug(name),
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!formData.name || !formData.slug || !formData.git_url || !formData.assigned_cell) {
      toast.error("Please fill in all required fields");
      return;
    }

    try {
      await createProject.mutateAsync({
        name: formData.name,
        slug: formData.slug,
        git_url: formData.git_url,
        assigned_cell: formData.assigned_cell,
        git_token: formData.git_token || undefined,
        default_branch: formData.default_branch || "main",
        test_command: formData.test_command || undefined,
        lint_command: formData.lint_command || undefined,
        format_command: formData.format_command || undefined,
        typecheck_command: formData.typecheck_command || undefined,
        build_command: formData.build_command || undefined,
      });
      toast.success("Project created successfully");
      setOpen(false);
      setFormData({
        name: "",
        slug: "",
        git_url: "",
        git_token: "",
        assigned_cell: Team.BACKEND,
        default_branch: "main",
      });
      setShowAdvanced(false);
    } catch (error) {
      toast.error(
        `Failed to create project: ${error instanceof Error ? error.message : "Unknown error"}`
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus className="h-4 w-4 mr-2" />
          New Project
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-[525px]">
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Create Project</DialogTitle>
            <DialogDescription>
              Register a git repository for agents to work on.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            {/* Name */}
            <div className="grid gap-2">
              <Label htmlFor="name">Project Name *</Label>
              <Input
                id="name"
                value={formData.name}
                onChange={(e) => handleNameChange(e.target.value)}
                placeholder="RoboCo API"
              />
            </div>

            {/* Slug */}
            <div className="grid gap-2">
              <Label htmlFor="slug">Slug *</Label>
              <Input
                id="slug"
                value={formData.slug}
                onChange={(e) => setFormData({ ...formData, slug: e.target.value })}
                placeholder="roboco-api"
                pattern="^[a-z0-9-]+$"
              />
              <p className="text-xs text-muted-foreground">
                URL-safe identifier (lowercase, hyphens only)
              </p>
            </div>

            {/* Git URL */}
            <div className="grid gap-2">
              <Label htmlFor="git_url">Git URL *</Label>
              <Input
                id="git_url"
                value={formData.git_url}
                onChange={(e) => setFormData({ ...formData, git_url: e.target.value })}
                placeholder="https://github.com/org/repo.git"
              />
              <p className="text-xs text-muted-foreground">
                Use HTTPS URL for token-based authentication
              </p>
            </div>

            {/* Git Token */}
            <div className="grid gap-2">
              <Label htmlFor="git_token" className="flex items-center gap-1">
                <Key className="h-3.5 w-3.5" />
                GitHub Token
              </Label>
              <Input
                id="git_token"
                type="password"
                value={formData.git_token || ""}
                onChange={(e) => setFormData({ ...formData, git_token: e.target.value })}
                placeholder="ghp_xxxxxxxxxxxx..."
              />
              <p className="text-xs text-muted-foreground">
                Personal access token with repo access for clone, push, and PR operations
              </p>
            </div>

            {/* Assigned Cell */}
            <div className="grid gap-2">
              <Label htmlFor="assigned_cell">Assigned Cell *</Label>
              <Select
                value={formData.assigned_cell}
                onValueChange={(value: Team) => setFormData({ ...formData, assigned_cell: value })}
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

            {/* Default Branch */}
            <div className="grid gap-2">
              <Label htmlFor="default_branch">Default Branch</Label>
              <Input
                id="default_branch"
                value={formData.default_branch}
                onChange={(e) => setFormData({ ...formData, default_branch: e.target.value })}
                placeholder="main"
              />
            </div>

            {/* Advanced Options Toggle */}
            <Button
              type="button"
              variant="ghost"
              className="justify-start px-0 text-muted-foreground"
              onClick={() => setShowAdvanced(!showAdvanced)}
            >
              {showAdvanced ? "Hide" : "Show"} CI/CD Commands
            </Button>

            {showAdvanced && (
              <>
                {/* CI/CD Commands */}
                <div className="grid gap-2">
                  <Label htmlFor="test_command">Test Command</Label>
                  <Input
                    id="test_command"
                    value={formData.test_command || ""}
                    onChange={(e) => setFormData({ ...formData, test_command: e.target.value })}
                    placeholder="uv run pytest"
                  />
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="lint_command">Lint Command</Label>
                  <Input
                    id="lint_command"
                    value={formData.lint_command || ""}
                    onChange={(e) => setFormData({ ...formData, lint_command: e.target.value })}
                    placeholder="uv run ruff check ."
                  />
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="format_command">Format Command</Label>
                  <Input
                    id="format_command"
                    value={formData.format_command || ""}
                    onChange={(e) => setFormData({ ...formData, format_command: e.target.value })}
                    placeholder="uv run ruff format ."
                  />
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="typecheck_command">Typecheck Command</Label>
                  <Input
                    id="typecheck_command"
                    value={formData.typecheck_command || ""}
                    onChange={(e) =>
                      setFormData({ ...formData, typecheck_command: e.target.value })
                    }
                    placeholder="uv run mypy src/"
                  />
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="build_command">Build Command</Label>
                  <Input
                    id="build_command"
                    value={formData.build_command || ""}
                    onChange={(e) => setFormData({ ...formData, build_command: e.target.value })}
                    placeholder="pnpm build"
                  />
                </div>
              </>
            )}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={createProject.isPending}>
              {createProject.isPending ? "Creating..." : "Create Project"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
