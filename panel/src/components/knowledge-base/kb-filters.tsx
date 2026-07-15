"use client";

import { KBIndexType } from "@/types";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import { HelpTip } from "@/components/ui/help-tip";
import { getIndexTypeDescription } from "./kb-index-type-badge";
import {
  FileText,
  MessageSquare,
  BookOpen,
  AlertTriangle,
  Scale,
  GitBranch,
  ClipboardCheck,
  Lightbulb,
  ScrollText,
  StickyNote,
} from "lucide-react";

const indexTypeConfig: Record<
  KBIndexType,
  { label: string; icon: React.ReactNode }
> = {
  [KBIndexType.DOCUMENTATION]: {
    label: "Documentation",
    icon: <FileText className="h-4 w-4 text-blue-500" />,
  },
  [KBIndexType.CONVERSATIONS]: {
    label: "Conversations",
    icon: <MessageSquare className="h-4 w-4 text-green-500" />,
  },
  [KBIndexType.JOURNALS]: {
    label: "Journals",
    icon: <BookOpen className="h-4 w-4 text-orange-500" />,
  },
  [KBIndexType.ERRORS]: {
    label: "Errors",
    icon: <AlertTriangle className="h-4 w-4 text-red-500" />,
  },
  [KBIndexType.STANDARDS]: {
    label: "Standards",
    icon: <Scale className="h-4 w-4 text-cyan-500" />,
  },
  [KBIndexType.DECISIONS]: {
    label: "Decisions",
    icon: <GitBranch className="h-4 w-4 text-indigo-500" />,
  },
  [KBIndexType.REVIEWS]: {
    label: "Reviews",
    icon: <ClipboardCheck className="h-4 w-4 text-pink-500" />,
  },
  [KBIndexType.LEARNINGS]: {
    label: "Learnings",
    icon: <Lightbulb className="h-4 w-4 text-yellow-500" />,
  },
  [KBIndexType.PLAYBOOKS]: {
    label: "Playbooks",
    icon: <ScrollText className="h-4 w-4 text-emerald-500" />,
  },
  [KBIndexType.VAULT_NOTES]: {
    label: "Vault Notes",
    icon: <StickyNote className="h-4 w-4 text-violet-500" />,
  },
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
          <HelpTip label="Reset — search every index type again instead of just these">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onTypesChange([])}
              className="h-auto py-0 px-1 text-xs text-muted-foreground hover:text-foreground"
            >
              Clear
            </Button>
          </HelpTip>
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
              <HelpTip label={getIndexTypeDescription(type)}>
                <span className="flex items-center gap-2 w-fit">
                  {config.icon}
                  <span className="text-sm">{config.label}</span>
                </span>
              </HelpTip>
            </label>
          );
        })}
      </div>
      {!allSelected && (
        <p className="text-xs text-muted-foreground">
          Showing {selectedTypes.length} of {Object.values(KBIndexType).length}{" "}
          types
        </p>
      )}
    </div>
  );
}
