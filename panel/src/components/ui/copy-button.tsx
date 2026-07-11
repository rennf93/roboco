"use client";

import { useState } from "react";
import { Copy, Check } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/**
 * Put `text` on the clipboard.
 *
 * Tries the async Clipboard API first, then falls back to a hidden
 * textarea + `execCommand("copy")`. The fallback matters: the panel is served
 * over plain http on a LAN IP, and `navigator.clipboard` only exists in a
 * secure context (https / localhost) — so on the real deployment the modern
 * API is simply absent and the legacy path is what actually works.
 */
async function writeClipboard(text: string): Promise<boolean> {
  if (
    typeof navigator !== "undefined" &&
    navigator.clipboard &&
    window.isSecureContext
  ) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // fall through to the legacy path
    }
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    ta.setAttribute("readonly", "");
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

interface CopyButtonProps {
  /** The text placed on the clipboard. */
  value: string;
  /** Visible label next to the icon; omit for an icon-only button. */
  label?: string;
  className?: string;
}

/** A small copy-to-clipboard button that flips to a check for ~1.5s on success. */
export function CopyButton({ value, label, className }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const onCopy = async () => {
    if (await writeClipboard(value)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }
  };

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={onCopy}
          aria-label={label ?? "Copy"}
          className={cn(
            "inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
            className,
          )}
        >
          {copied ? (
            <Check className="h-3.5 w-3.5" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
          {label ? <span>{copied ? "Copied" : label}</span> : null}
        </button>
      </TooltipTrigger>
      <TooltipContent>
        {copied ? "Copied!" : (label ?? "Copy to clipboard")}
      </TooltipContent>
    </Tooltip>
  );
}
