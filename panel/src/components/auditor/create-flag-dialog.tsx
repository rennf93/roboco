"use client";

import { useState } from "react";
import { FlagSeverity } from "@/types";
import { useCreateAuditorFlag } from "@/hooks/use-dashboard";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";

interface CreateFlagDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const SEVERITY_OPTIONS = [
  { value: FlagSeverity.INFO, label: "Info", color: "text-blue-600" },
  { value: FlagSeverity.WARNING, label: "Warning", color: "text-yellow-600" },
  { value: FlagSeverity.URGENT, label: "Urgent", color: "text-red-600" },
];

const CATEGORY_OPTIONS = [
  "quality",
  "process",
  "communication",
  "performance",
  "security",
  "documentation",
  "other",
];

export function CreateFlagDialog({ open, onOpenChange }: CreateFlagDialogProps) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [severity, setSeverity] = useState<FlagSeverity>(FlagSeverity.INFO);
  const [category, setCategory] = useState("quality");
  const [relatedTaskId, setRelatedTaskId] = useState("");
  const [relatedAgentId, setRelatedAgentId] = useState("");

  const createFlag = useCreateAuditorFlag();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!title.trim() || !description.trim()) {
      toast.error("Title and description are required");
      return;
    }

    try {
      await createFlag.mutateAsync({
        title: title.trim(),
        description: description.trim(),
        severity,
        category,
        related_task_id: relatedTaskId.trim() || undefined,
        related_agent_id: relatedAgentId.trim() || undefined,
      });
      toast.success("Flag created successfully");
      onOpenChange(false);
      resetForm();
    } catch {
      toast.error("Failed to create flag");
    }
  };

  const resetForm = () => {
    setTitle("");
    setDescription("");
    setSeverity(FlagSeverity.INFO);
    setCategory("quality");
    setRelatedTaskId("");
    setRelatedAgentId("");
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Create Quality Flag</DialogTitle>
          <DialogDescription>
            Flag an issue for tracking and resolution.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4 mt-4">
          <div className="space-y-2">
            <Label htmlFor="title">Title *</Label>
            <Input
              id="title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Brief description of the issue"
              required
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="description">Description *</Label>
            <Textarea
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Detailed explanation of the issue..."
              className="min-h-[100px]"
              required
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Severity</Label>
              <Select value={severity} onValueChange={(v) => setSeverity(v as FlagSeverity)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SEVERITY_OPTIONS.map((s) => (
                    <SelectItem key={s.value} value={s.value}>
                      <span className={s.color}>{s.label}</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Category</Label>
              <Select value={category} onValueChange={setCategory}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CATEGORY_OPTIONS.map((c) => (
                    <SelectItem key={c} value={c}>
                      {c.charAt(0).toUpperCase() + c.slice(1)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="task">Related Task ID (optional)</Label>
              <Input
                id="task"
                value={relatedTaskId}
                onChange={(e) => setRelatedTaskId(e.target.value)}
                placeholder="Task UUID"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="agent">Related Agent ID (optional)</Label>
              <Input
                id="agent"
                value={relatedAgentId}
                onChange={(e) => setRelatedAgentId(e.target.value)}
                placeholder="Agent ID"
              />
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-4">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                onOpenChange(false);
                resetForm();
              }}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={createFlag.isPending}>
              {createFlag.isPending ? "Creating..." : "Create Flag"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
