"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Plus, X, GripVertical } from "lucide-react";

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
        <Label>
          Acceptance Criteria <span className="text-destructive">*</span>
        </Label>
        <span className="text-xs text-muted-foreground">
          {criteria.length} item{criteria.length !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Existing criteria list */}
      {criteria.length > 0 && (
        <div className="space-y-2 border rounded-lg p-3 bg-muted/30">
          {criteria.map((criterion, index) => (
            <div key={index} className="flex items-center gap-2">
              <GripVertical className="h-4 w-4 text-muted-foreground shrink-0" />
              <span className="text-sm text-muted-foreground w-6">{index + 1}.</span>
              <Input
                value={criterion}
                onChange={(e) => handleUpdate(index, e.target.value)}
                className="flex-1 h-8"
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-8 w-8 shrink-0"
                onClick={() => handleRemove(index)}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          ))}
        </div>
      )}

      {/* Add new criterion */}
      <div className="flex items-center gap-2">
        <Input
          value={newCriterion}
          onChange={(e) => setNewCriterion(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Enter acceptance criterion and press Enter..."
          className="flex-1"
        />
        <Button type="button" variant="outline" onClick={handleAdd} disabled={!newCriterion.trim()}>
          <Plus className="h-4 w-4 mr-1" />
          Add
        </Button>
      </div>

      {/* Helper text */}
      <p className="text-xs text-muted-foreground">
        Define at least one acceptance criterion. Each criterion should describe a specific,
        testable condition for task completion.
      </p>

      {/* Error message */}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
