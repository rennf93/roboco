"use client";

import { useCallback, useRef } from "react";
import ReactMarkdown, { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";
import { Checkbox } from "@/components/ui/checkbox";

interface MarkdownProps {
  children: string;
  className?: string;
  /** Callback when a checkbox is toggled. Receives the updated markdown string. */
  onCheckboxChange?: (newContent: string) => void;
  /** Whether checkbox interactions are disabled */
  disabled?: boolean;
  /** Compact mode for cards - normalizes all text to small size */
  compact?: boolean;
}

/**
 * Renders markdown content with proper styling.
 * Uses Tailwind's prose classes for typography.
 * Supports GitHub Flavored Markdown (tables, checkboxes, strikethrough, autolinks).
 */
export function Markdown({ children, className, onCheckboxChange, disabled, compact }: MarkdownProps) {
  const checkboxIndexRef = useRef(0);

  // Reset checkbox index on each render
  checkboxIndexRef.current = 0;

  // Toggle checkbox in markdown content
  const toggleCheckbox = useCallback(
    (index: number, checked: boolean) => {
      if (!onCheckboxChange) return;

      // Find and replace the nth checkbox in the markdown
      let currentIndex = 0;
      const newContent = children.replace(/- \[([ xX])\]/g, (match) => {
        if (currentIndex === index) {
          currentIndex++;
          return checked ? "- [x]" : "- [ ]";
        }
        currentIndex++;
        return match;
      });

      onCheckboxChange(newContent);
    },
    [children, onCheckboxChange]
  );

  // Custom components for ReactMarkdown
  const components: Components = onCheckboxChange
    ? {
        input: ({ type, checked, ...props }) => {
          if (type === "checkbox") {
            const index = checkboxIndexRef.current++;
            return (
              <Checkbox
                checked={!!checked}
                disabled={disabled}
                onCheckedChange={(newChecked) => toggleCheckbox(index, !!newChecked)}
                className="mr-2 align-middle"
              />
            );
          }
          return <input type={type} checked={checked} {...props} />;
        },
      }
    : {};

  if (!children) {
    return null;
  }

  // Compact mode styles - normalize everything to small text
  const compactStyles = compact
    ? [
        "prose-sm",
        // Normalize all headings to same small size
        "prose-headings:text-xs prose-headings:font-medium prose-headings:my-0",
        "prose-h1:text-xs prose-h2:text-xs prose-h3:text-xs prose-h4:text-xs",
        "prose-h2:border-0 prose-h2:pb-0",
        // Minimal spacing
        "prose-p:my-0 prose-p:leading-normal",
        "prose-ul:my-0 prose-ol:my-0 prose-li:my-0",
        // Smaller code
        "prose-code:text-xs prose-code:px-1 prose-code:py-0",
        // Hide pre blocks in compact mode
        "prose-pre:hidden",
        // Compact tables
        "prose-table:text-xs",
      ]
    : [
        // Headings - make them stand out
        "prose-headings:font-bold prose-headings:tracking-tight prose-headings:text-foreground",
        "prose-h1:text-2xl prose-h1:mt-6 prose-h1:mb-4",
        "prose-h2:text-xl prose-h2:mt-5 prose-h2:mb-3 prose-h2:border-b prose-h2:border-border prose-h2:pb-2",
        "prose-h3:text-lg prose-h3:mt-4 prose-h3:mb-2",
        "prose-h4:text-base prose-h4:mt-3 prose-h4:mb-1",
        // Paragraphs
        "prose-p:my-3 prose-p:leading-relaxed",
        // Lists - better spacing
        "prose-ul:my-3 prose-ol:my-3 prose-li:my-1",
        // Pre blocks
        "prose-pre:bg-muted prose-pre:border prose-pre:rounded-lg",
      ];

  return (
    <div
      className={cn(
        "prose prose-sm dark:prose-invert max-w-none",
        ...compactStyles,
        // Links
        "prose-a:text-primary prose-a:no-underline hover:prose-a:underline",
        // Code
        "prose-code:bg-muted prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-code:font-mono",
        // Task list checkboxes - proper styling (for non-interactive mode)
        "[&_input[type=checkbox]]:mr-2 [&_input[type=checkbox]]:h-4 [&_input[type=checkbox]]:w-4 [&_input[type=checkbox]]:accent-primary [&_input[type=checkbox]]:rounded",
        // Task list items - remove bullet when checkbox present
        "[&_li:has(input[type=checkbox])]:list-none [&_li:has(input[type=checkbox])]:pl-0",
        "[&_li:has(button[role=checkbox])]:list-none [&_li:has(button[role=checkbox])]:pl-0",
        // Tables
        "prose-table:border prose-table:border-border prose-th:border prose-th:border-border prose-th:bg-muted prose-td:border prose-td:border-border",
        // Strong/bold
        "prose-strong:font-semibold prose-strong:text-foreground",
        className
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
