"use client";

import { useState } from "react";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Markdown } from "@/components/ui/markdown";
import { Eye, Edit3 } from "lucide-react";
import { HelpTip } from "@/components/ui/help-tip";

interface MarkdownEditorProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  required?: boolean;
  minLength?: number;
  error?: string;
}

export function MarkdownEditor({
  label,
  value,
  onChange,
  placeholder,
  required,
  minLength,
  error,
}: MarkdownEditorProps) {
  const [mode, setMode] = useState<"write" | "preview">("write");

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label>
          {label} {required && <span className="text-destructive">*</span>}
        </Label>
        <Tabs
          value={mode}
          onValueChange={(v) => setMode(v as "write" | "preview")}
        >
          <TabsList className="h-8">
            <TabsTrigger value="write" className="text-xs px-2 h-6">
              <HelpTip label="Edit the raw markdown text.">
                <span className="flex items-center">
                  <Edit3 className="h-3 w-3 mr-1" />
                  Write
                </span>
              </HelpTip>
            </TabsTrigger>
            <TabsTrigger value="preview" className="text-xs px-2 h-6">
              <HelpTip label="Renders the markdown as it will appear elsewhere in the app.">
                <span className="flex items-center">
                  <Eye className="h-3 w-3 mr-1" />
                  Preview
                </span>
              </HelpTip>
            </TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {mode === "write" ? (
        <Textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="min-h-[120px] font-mono text-sm"
        />
      ) : (
        <div className="min-h-[120px] p-3 border rounded-md bg-muted/30">
          {value ? (
            <Markdown>{value}</Markdown>
          ) : (
            <p className="text-muted-foreground text-sm italic">
              Nothing to preview
            </p>
          )}
        </div>
      )}

      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <HelpTip label="GitHub Flavored Markdown: headings, bold/italic, lists, tables, checkboxes, links, code blocks.">
          <span>Markdown supported</span>
        </HelpTip>
        {minLength && (
          <HelpTip label="Submission is blocked until the count reaches this minimum.">
            <span className={value.length < minLength ? "text-destructive" : ""}>
              {value.length}/{minLength} min characters
            </span>
          </HelpTip>
        )}
      </div>

      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
