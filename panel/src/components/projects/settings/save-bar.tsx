"use client";

import { Button } from "@/components/ui/button";
import { Save } from "lucide-react";

interface SaveBarProps {
  dirty: boolean;
  pending: boolean;
  onSave: () => void;
}

// Shared per-card save action: disabled until the card has unsaved edits,
// with a subtle indicator next to the button while it does. Every settings
// card below renders this identically instead of re-wiring the same three
// lines seven times.
export function SaveBar({ dirty, pending, onSave }: SaveBarProps) {
  return (
    <div className="flex items-center gap-3">
      <Button onClick={onSave} disabled={!dirty || pending}>
        <Save className="h-4 w-4 mr-2" />
        {pending ? "Saving..." : "Save"}
      </Button>
      {dirty && !pending && (
        <span className="flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400">
          <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
          Unsaved changes
        </span>
      )}
    </div>
  );
}
