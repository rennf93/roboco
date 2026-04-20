"use client";

import { KBIndexType } from "@/types";
import { Checkbox } from "@/components/ui/checkbox";
import { Code, FileText, MessageSquare, BookOpen, AlertTriangle, Scale, GitBranch, ClipboardCheck, Lightbulb } from "lucide-react";

const indexTypeConfig: Record<KBIndexType, { label: string; icon: React.ReactNode }> = {
  [KBIndexType.CODE]: { label: "Code", icon: <Code className="h-4 w-4 text-purple-500" /> },
  [KBIndexType.DOCUMENTATION]: { label: "Documentation", icon: <FileText className="h-4 w-4 text-blue-500" /> },
  [KBIndexType.CONVERSATIONS]: { label: "Conversations", icon: <MessageSquare className="h-4 w-4 text-green-500" /> },
  [KBIndexType.JOURNALS]: { label: "Journals", icon: <BookOpen className="h-4 w-4 text-orange-500" /> },
  [KBIndexType.ERRORS]: { label: "Errors", icon: <AlertTriangle className="h-4 w-4 text-red-500" /> },
  [KBIndexType.STANDARDS]: { label: "Standards", icon: <Scale className="h-4 w-4 text-cyan-500" /> },
  [KBIndexType.DECISIONS]: { label: "Decisions", icon: <GitBranch className="h-4 w-4 text-indigo-500" /> },
  [KBIndexType.REVIEWS]: { label: "Reviews", icon: <ClipboardCheck className="h-4 w-4 text-pink-500" /> },
  [KBIndexType.LEARNINGS]: { label: "Learnings", icon: <Lightbulb className="h-4 w-4 text-yellow-500" /> },
};

interface KBFiltersProps {
  selectedTypes: KBIndexType[];
  onTypesChange: (types: KBIndexType[]) => void;
}

export function KBFilters({ selectedTypes, onTypesChange }: KBFiltersProps) {
  const toggleType = (type: KBIndexType) => {
    if (selectedTypes.includes(type)) {
      onTypesChange(selectedTypes.filter((t) => t !== type));
    } else {
      onTypesChange([...selectedTypes, type]);
    }
  };

  const allSelected = selectedTypes.length === 0;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">Filter by type</span>
        {selectedTypes.length > 0 && (
          <button
            onClick={() => onTypesChange([])}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            Clear
          </button>
        )}
      </div>
      <div className="space-y-2">
        {Object.values(KBIndexType).map((type) => {
          const config = indexTypeConfig[type];
          const isChecked = allSelected || selectedTypes.includes(type);

          return (
            <label
              key={type}
              className="flex items-center gap-2 cursor-pointer hover:bg-muted/50 p-1.5 rounded -ml-1.5"
            >
              <Checkbox
                checked={isChecked}
                onCheckedChange={() => toggleType(type)}
              />
              {config.icon}
              <span className="text-sm">{config.label}</span>
            </label>
          );
        })}
      </div>
      {!allSelected && (
        <p className="text-xs text-muted-foreground">
          Showing {selectedTypes.length} of {Object.values(KBIndexType).length} types
        </p>
      )}
    </div>
  );
}
