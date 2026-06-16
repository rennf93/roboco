"use client";

import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";

interface RequiredNotesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Title shown in the dialog header */
  title?: string;
  /** Description shown below the title */
  description?: string;
  /** Label for the notes textarea */
  notesLabel?: string;
  /** Placeholder text for the textarea */
  placeholder?: string;
  /** Called with the entered notes when the user clicks Submit */
  onSubmit: (notes: string) => void;
  /** Whether the submit action is currently pending (disables buttons) */
  isPending?: boolean;
  /** Label for the submit button */
  submitLabel?: string;
}

/**
 * A dialog that requires the user to enter a non-empty reason / notes before
 * confirming a destructive or significant action.  The Submit button is
 * disabled while the notes textarea is empty or whitespace-only.  Cancel
 * closes the dialog without invoking `onSubmit`.
 *
 * The dialog is keyed on `open` so its internal state resets cleanly on each
 * open; this avoids a `setState-in-effect` pattern.
 */
function RequiredNotesDialogInner({
  open,
  onOpenChange,
  title = "Add a note",
  description = "Please provide a reason before continuing.",
  notesLabel = "Notes",
  placeholder = "Enter your reason…",
  onSubmit,
  isPending = false,
  submitLabel = "Submit",
}: RequiredNotesDialogProps) {
  const [notes, setNotes] = useState("");

  const isBlank = notes.trim() === "";

  const handleSubmit = () => {
    if (isBlank || isPending) return;
    onSubmit(notes.trim());
  };

  const handleCancel = () => {
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && (
            <DialogDescription>{description}</DialogDescription>
          )}
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="required-notes">{notesLabel}</Label>
          <Textarea
            id="required-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder={placeholder}
            rows={4}
            disabled={isPending}
          />
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleCancel} disabled={isPending}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={isBlank || isPending}>
            {isPending ? "Submitting…" : submitLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Exported wrapper that remounts the inner component each time the dialog
 * opens, giving us a fresh empty notes field without using setState-in-effect.
 */
export function RequiredNotesDialog(props: RequiredNotesDialogProps) {
  // Using open as the key causes the inner component to remount (and reset its
  // local state) each time the dialog transitions from closed → open.
  return <RequiredNotesDialogInner key={String(props.open)} {...props} />;
}
