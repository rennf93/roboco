"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

/**
 * Collapsible reject-with-reason form shared by every approval detail.
 * The minimum length mirrors each queue's server guard (release wants a
 * substantive change request, the rest a short reason).
 */
export function RejectForm({
  minChars,
  placeholder,
  pending,
  onSubmit,
}: {
  minChars: number;
  placeholder: string;
  pending: boolean;
  onSubmit: (reason: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const tooShort = reason.trim().length < minChars;

  if (!open) {
    return (
      <Button
        variant="outline"
        className="w-full text-destructive"
        onClick={() => setOpen(true)}
      >
        Reject…
      </Button>
    );
  }

  return (
    <div className="space-y-2">
      <Textarea
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder={placeholder}
        rows={3}
        className="text-sm"
      />
      <div className="flex items-center gap-2">
        <Button
          variant="destructive"
          className="flex-1"
          disabled={pending || tooShort}
          onClick={() => onSubmit(reason.trim())}
        >
          Reject
        </Button>
        <Button
          variant="ghost"
          disabled={pending}
          onClick={() => {
            setOpen(false);
            setReason("");
          }}
        >
          Cancel
        </Button>
      </div>
      {tooShort && (
        <p className="text-[11px] text-muted-foreground">
          At least {minChars} characters.
        </p>
      )}
    </div>
  );
}
