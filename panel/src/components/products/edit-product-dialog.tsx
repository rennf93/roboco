"use client";

import { useState } from "react";
import { useProduct, useUpdateProduct } from "@/hooks/use-products";
import { useProjects } from "@/hooks/use-projects";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
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
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import {
  Team,
  type Product,
  type ProductCellMapping,
  type ProductUpdate,
} from "@/types";
import { HelpTip } from "@/components/ui/help-tip";

const cells: { value: Team; label: string }[] = [
  { value: Team.BACKEND, label: "Backend" },
  { value: Team.FRONTEND, label: "Frontend" },
  { value: Team.UX_UI, label: "UX/UI" },
];

// Build a team -> project_id lookup from the product's cell mappings
function mappingFromCells(
  productCells: ProductCellMapping[],
): Partial<Record<Team, string>> {
  return productCells.reduce<Partial<Record<Team, string>>>((acc, cell) => {
    acc[cell.team] = cell.project_id;
    return acc;
  }, {});
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

interface EditProductDialogProps {
  productId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

// Inner form component - receives product directly, manages its own state
function EditProductForm({
  product,
  onSuccess,
  onCancel,
}: {
  product: Product;
  onSuccess: () => void;
  onCancel: () => void;
}) {
  const updateProduct = useUpdateProduct();
  const { data: projects } = useProjects();

  const [name, setName] = useState(product.name);
  const [description, setDescription] = useState(product.description ?? "");
  const [cellMapping, setCellMapping] = useState<Partial<Record<Team, string>>>(
    mappingFromCells(product.cells),
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!name) {
      toast.error("Please fill in all required fields");
      return;
    }

    const updates: ProductUpdate = {
      name,
      description: description || undefined,
      cells: buildCells(cellMapping),
    };

    try {
      await updateProduct.mutateAsync({ id: product.id, patch: updates });
      toast.success("Product updated successfully");
      onSuccess();
    } catch (error) {
      toast.error(
        `Failed to update product: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  };

  return (
    <form onSubmit={handleSubmit}>
      <DialogHeader>
        <DialogTitle>Edit Product</DialogTitle>
        <DialogDescription>
          Update product settings. Slug cannot be changed.
        </DialogDescription>
      </DialogHeader>
      <div className="grid gap-4 py-4">
        {/* Slug (read-only) */}
        <div className="grid gap-2">
          <HelpTip label="Identifier for this cell-to-project grouping, used to reference it across the panel and API. Set at creation, fixed here.">
            <Label htmlFor="slug">Slug</Label>
          </HelpTip>
          <Input
            id="slug"
            value={product.slug}
            disabled
            className="font-mono text-muted-foreground"
          />
        </div>

        {/* Name */}
        <div className="grid gap-2">
          <HelpTip label="Display name shown across the panel; renaming it never touches the slug.">
            <Label htmlFor="name">Product Name *</Label>
          </HelpTip>
          <Input
            id="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="RoboCo Platform"
          />
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
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={updateProduct.isPending}>
          {updateProduct.isPending ? "Saving..." : "Save Changes"}
        </Button>
      </DialogFooter>
    </form>
  );
}

// Main dialog component - handles data fetching and dialog state
export function EditProductDialog({
  productId,
  open,
  onOpenChange,
}: EditProductDialogProps) {
  const { data: product, isLoading } = useProduct(productId);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[525px] max-h-[90vh] overflow-y-auto">
        {isLoading ? (
          <div className="space-y-4 py-4">
            <Skeleton className="h-8 w-48" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : product ? (
          // Key forces remount when product changes, resetting form state
          <EditProductForm
            key={product.id}
            product={product}
            onSuccess={() => onOpenChange(false)}
            onCancel={() => onOpenChange(false)}
          />
        ) : (
          <div className="py-8 text-center text-muted-foreground">
            Product not found
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
