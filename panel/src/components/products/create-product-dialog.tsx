"use client";

import { useState } from "react";
import { useCreateProduct } from "@/hooks/use-products";
import { useProjects } from "@/hooks/use-projects";
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
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { Team, type ProductCellMapping } from "@/types";
import { HelpTip } from "@/components/ui/help-tip";

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

// Build the cells payload from the per-cell project selections (only mapped cells)
function buildCells(
  mapping: Partial<Record<Team, string>>,
): ProductCellMapping[] {
  return cells
    .filter((cell) => mapping[cell.value])
    .map((cell) => ({
      team: cell.value,
      project_id: mapping[cell.value] as string,
    }));
}

export function CreateProductDialog() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [cellMapping, setCellMapping] = useState<Partial<Record<Team, string>>>(
    {},
  );

  const createProduct = useCreateProduct();
  const { data: projects } = useProjects();

  const handleNameChange = (value: string) => {
    setName(value);
    setSlug(generateSlug(value));
  };

  const resetForm = () => {
    setName("");
    setSlug("");
    setDescription("");
    setCellMapping({});
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!name || !slug) {
      toast.error("Please fill in all required fields");
      return;
    }

    try {
      await createProduct.mutateAsync({
        name,
        slug,
        description: description || undefined,
        cells: buildCells(cellMapping),
      });
      toast.success("Product created successfully");
      setOpen(false);
      resetForm();
    } catch (error) {
      toast.error(
        `Failed to create product: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <HelpTip label="Groups per-cell projects (e.g. a SaaS app + its OSS core) into one deliverable for MegaTask and board routing.">
        <DialogTrigger asChild>
          <Button>
            <Plus className="h-4 w-4 mr-2" />
            New Product
          </Button>
        </DialogTrigger>
      </HelpTip>
      <DialogContent className="sm:max-w-[525px] max-h-[90vh] overflow-y-auto">
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Create Product</DialogTitle>
            <DialogDescription>
              Define a product and map each cell to the project it works on.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            {/* Name */}
            <div className="grid gap-2">
              <HelpTip label="Display name shown across the panel; renaming it never touches the slug already generated.">
                <Label htmlFor="name">Product Name *</Label>
              </HelpTip>
              <Input
                id="name"
                value={name}
                onChange={(e) => handleNameChange(e.target.value)}
                placeholder="RoboCo Platform"
              />
            </div>

            {/* Slug */}
            <div className="grid gap-2">
              <HelpTip label="Identifier for this cell-to-project grouping, used to reference it across the panel and API.">
                <Label htmlFor="slug">Slug *</Label>
              </HelpTip>
              <Input
                id="slug"
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
                placeholder="roboco-platform"
                pattern="^[a-z0-9-]+$"
              />
              <p className="text-xs text-muted-foreground">
                URL-safe identifier (lowercase, hyphens only)
              </p>
            </div>

            {/* Description */}
            <div className="grid gap-2">
              <HelpTip label="Optional context shown in the panel only — not surfaced to agents or used in task routing.">
                <Label htmlFor="description">Description</Label>
              </HelpTip>
              <Textarea
                id="description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="What this product is about"
              />
            </div>

            {/* Cell -> Project mapping */}
            <div className="grid gap-3">
              <HelpTip label="Routes each cell's work for this product to its own repo; a cell left unmapped does no work for this product.">
                <Label>Cell Project Mapping</Label>
              </HelpTip>
              {cells.map((cell) => (
                <div key={cell.value} className="grid gap-2">
                  <HelpTip label={`Which project the ${cell.label} cell works on for this product.`}>
                    <Label
                      htmlFor={`cell-${cell.value}`}
                      className="text-sm text-muted-foreground"
                    >
                      {cell.label}
                    </Label>
                  </HelpTip>
                  <Select
                    value={cellMapping[cell.value] ?? ""}
                    onValueChange={(value) =>
                      setCellMapping({ ...cellMapping, [cell.value]: value })
                    }
                  >
                    <SelectTrigger id={`cell-${cell.value}`}>
                      <SelectValue placeholder="No project" />
                    </SelectTrigger>
                    <SelectContent>
                      {(projects ?? []).map((project) => (
                        <SelectItem key={project.id} value={project.id}>
                          {project.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              ))}
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={createProduct.isPending}>
              {createProduct.isPending ? "Creating..." : "Create Product"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
