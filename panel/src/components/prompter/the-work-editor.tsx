"use client";

import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Team } from "@/types";
import type { CellWork } from "@/lib/api/prompter";
import { StringListEditor } from "./string-list-editor";

interface TheWorkEditorProps {
  cells: CellWork[];
  onChange: (cells: CellWork[]) => void;
  disabled?: boolean;
}

// Only delivery cells do The Work; coordination teams are not pickable here.
const CELL_OPTIONS: Team[] = [Team.BACKEND, Team.FRONTEND, Team.UX_UI];

const cellLabel = (team: Team) =>
  team === Team.UX_UI ? "UX/UI" : team.charAt(0).toUpperCase() + team.slice(1);

/**
 * Editor for the per-cell breakdown of The Work. One row per participating
 * cell; the number of cells is what makes a task single- vs multi-cell.
 */
export function TheWorkEditor({
  cells,
  onChange,
  disabled = false,
}: TheWorkEditorProps) {
  const update = (index: number, patch: Partial<CellWork>) => {
    onChange(cells.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  };

  const remove = (index: number) => {
    onChange(cells.filter((_, i) => i !== index));
  };

  const addCell = () => {
    const used = new Set(cells.map((c) => c.team));
    const next = CELL_OPTIONS.find((t) => !used.has(t)) ?? Team.BACKEND;
    onChange([...cells, { team: next, summary: "", items: [] }]);
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <Label>The Work {cells.length > 1 && "(board-led, per cell)"}</Label>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={addCell}
          disabled={disabled}
        >
          <Plus className="mr-1.5 h-3.5 w-3.5" />
          Add cell
        </Button>
      </div>

      {cells.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No cells yet — add one, or keep chatting to let the assistant scope it.
        </p>
      )}

      {cells.map((cell, i) => (
        <div
          key={`${cell.team}-${i}`}
          className="space-y-3 rounded-lg border p-3"
        >
          <div className="flex items-center gap-2">
            <Select
              value={cell.team}
              onValueChange={(v) => update(i, { team: v as Team })}
              disabled={disabled}
            >
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CELL_OPTIONS.map((t) => (
                  <SelectItem key={t} value={t}>
                    {cellLabel(t)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              value={cell.summary}
              onChange={(e) => update(i, { summary: e.target.value })}
              placeholder="One-line summary of this cell's slice"
              disabled={disabled}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-9 w-9 shrink-0"
              onClick={() => remove(i)}
              disabled={disabled}
              aria-label={`Remove ${cellLabel(cell.team)} work`}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
          <StringListEditor
            label="Deliverables"
            items={cell.items}
            onChange={(items) => update(i, { items })}
            placeholder="Add a deliverable…"
            disabled={disabled}
          />
        </div>
      ))}
    </div>
  );
}
