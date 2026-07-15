"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Plus, X, GripVertical } from "lucide-react";
import { HelpTip } from "@/components/ui/help-tip";

interface AcceptanceCriteriaEditorProps {
  criteria: string[];
  onChange: (criteria: string[]) => void;
  error?: string;
}

export function AcceptanceCriteriaEditor({
  criteria,
  onChange,
  error,
}: AcceptanceCriteriaEditorProps) {
  const [newCriterion, setNewCriterion] = useState("");

  const handleAdd = () => {
    const trimmed = newCriterion.trim();
    if (trimmed && !criteria.includes(trimmed)) {
      onChange([...criteria, trimmed]);
      setNewCriterion("");
    }
  };

  const handleRemove = (index: number) => {
    const updated = criteria.filter((_, i) => i !== index);
    onChange(updated);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleAdd();
    }
  };

  const handleUpdate = (index: number, value: string) => {
    const updated = [...criteria];
    updated[index] = value;
    onChange(updated);
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <HelpTip label="QA and reviewers check each of these directly against the shipped work to decide pass/fail.">
          <Label>
            Acceptance Criteria <span className="text-destructive">*</span>
          </Label>
        </HelpTip>
        <HelpTip label="How many criteria are defined so far — at least one is required to submit.">
          <span className="text-xs text-muted-foreground">
            {criteria.length} item{criteria.length !== 1 ? "s" : ""}
          </span>
        </HelpTip>
      </div>

      {/* Existing criteria list */}
      {criteria.length > 0 && (
        <div className="space-y-2 border rounded-lg p-3 bg-muted/30">
          {criteria.map((criterion, index) => (
            <div key={index} className="flex items-center gap-2">
              <GripVertical className="h-4 w-4 text-muted-foreground shrink-0" />
              <span className="text-sm text-muted-foreground w-6">
                {index + 1}.
              </span>
              <HelpTip label="Edit this criterion's text directly — changes save as you type.">
                <Input
                  value={criterion}
                  onChange={(e) => handleUpdate(index, e.target.value)}
                  className="flex-1 h-8"
                />
              </HelpTip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8 shrink-0"
                    onClick={() => handleRemove(index)}
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Remove this criterion</TooltipContent>
              </Tooltip>
            </div>
          ))}
        </div>
      )}

      {/* Add new criterion */}
      <div className="flex items-center gap-2">
        <HelpTip label="A specific, testable condition — Enter or Add appends it to the list above.">
          <Input
            value={newCriterion}
            onChange={(e) => setNewCriterion(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Enter acceptance criterion and press Enter..."
            className="flex-1"
          />
        </HelpTip>
        <HelpTip label="Appends the text on the left as a new criterion; disabled until you type something.">
          <span
            className="inline-block"
            tabIndex={!newCriterion.trim() ? 0 : undefined}
          >
            <Button
              type="button"
              variant="outline"
              onClick={handleAdd}
              disabled={!newCriterion.trim()}
            >
              <Plus className="h-4 w-4 mr-1" />
              Add
            </Button>
          </span>
        </HelpTip>
      </div>

      {/* Helper text */}
      <p className="text-xs text-muted-foreground">
        Define at least one acceptance criterion. Each criterion should describe
        a specific, testable condition for task completion.
      </p>

      {/* Error message */}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
