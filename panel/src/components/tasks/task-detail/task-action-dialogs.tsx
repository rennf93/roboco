"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

// Escalate to CEO Dialog
interface EscalateToCeoDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (reason: string) => void;
  isPending?: boolean;
}

export function EscalateToCeoDialog({
  open,
  onOpenChange,
  onConfirm,
  isPending,
}: EscalateToCeoDialogProps) {
  const [reason, setReason] = useState("");

  const handleConfirm = () => {
    if (reason.trim()) {
      onConfirm(reason.trim());
      setReason("");
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Escalate to CEO</DialogTitle>
          <DialogDescription>
            Explain why this task needs CEO review and approval.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="grid gap-2">
            <Label htmlFor="reason">Reason for escalation</Label>
            <Textarea
              id="reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="This task requires CEO approval because..."
              rows={4}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleConfirm} disabled={!reason.trim() || isPending}>
            {isPending ? "Escalating..." : "Escalate"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// CEO Reject Dialog
interface CeoRejectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (notes: string) => void;
  isPending?: boolean;
}

export function CeoRejectDialog({
  open,
  onOpenChange,
  onConfirm,
  isPending,
}: CeoRejectDialogProps) {
  const [notes, setNotes] = useState("");

  const handleConfirm = () => {
    if (notes.trim()) {
      onConfirm(notes.trim());
      setNotes("");
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Request Changes</DialogTitle>
          <DialogDescription>
            Explain what changes are needed before this task can be approved.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="grid gap-2">
            <Label htmlFor="notes">Required changes</Label>
            <Textarea
              id="notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Please address the following..."
              rows={4}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={handleConfirm}
            disabled={!notes.trim() || isPending}
          >
            {isPending ? "Submitting..." : "Request Changes"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// Create Branch Dialog
interface CreateBranchDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (branchType: string) => void;
  isPending?: boolean;
  taskId: string;
}

export function CreateBranchDialog({
  open,
  onOpenChange,
  onConfirm,
  isPending,
}: CreateBranchDialogProps) {
  const [branchType, setBranchType] = useState("feature");

  const handleConfirm = () => {
    onConfirm(branchType);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create Branch</DialogTitle>
          <DialogDescription>
            Create a new branch for this task. The branch name will be generated automatically.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="grid gap-2">
            <Label htmlFor="branch-type">Branch Type</Label>
            <Select value={branchType} onValueChange={setBranchType}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="feature">feature</SelectItem>
                <SelectItem value="bug">bug</SelectItem>
                <SelectItem value="chore">chore</SelectItem>
                <SelectItem value="docs">docs</SelectItem>
                <SelectItem value="hotfix">hotfix</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Branch will be named: {branchType}/[team]/[task-id]
            </p>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleConfirm} disabled={isPending}>
            {isPending ? "Creating..." : "Create Branch"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// Create PR Dialog
interface CreatePRDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (title: string, body: string) => void;
  isPending?: boolean;
  defaultTitle?: string;
}

export function CreatePRDialog({
  open,
  onOpenChange,
  onConfirm,
  isPending,
  defaultTitle = "",
}: CreatePRDialogProps) {
  const [title, setTitle] = useState(defaultTitle);
  const [body, setBody] = useState("");

  const handleConfirm = () => {
    if (title.trim()) {
      onConfirm(title.trim(), body.trim());
      setTitle("");
      setBody("");
    }
  };

  // Reset title when dialog opens with new default
  const handleOpenChange = (newOpen: boolean) => {
    if (newOpen && defaultTitle) {
      setTitle(defaultTitle);
    }
    onOpenChange(newOpen);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[525px]">
        <DialogHeader>
          <DialogTitle>Create Pull Request</DialogTitle>
          <DialogDescription>
            Create a pull request for this task&apos;s branch.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="grid gap-2">
            <Label htmlFor="pr-title">Title</Label>
            <Input
              id="pr-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="PR title..."
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="pr-body">Description</Label>
            <Textarea
              id="pr-body"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="Describe the changes..."
              rows={6}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleConfirm} disabled={!title.trim() || isPending}>
            {isPending ? "Creating..." : "Create PR"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
