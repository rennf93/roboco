"use client";

import { useMainButton } from "@/lib/telegram/hooks";
import { useTgWebApp } from "@/lib/telegram/hooks";
import { Button } from "@/components/ui/button";
import { Loader2 } from "lucide-react";

/**
 * The focused card's one primary action. Inside Telegram it drives the
 * native MainButton (and renders nothing); outside — the dev mock, an old
 * client — it falls back to a visible full-width button. Mount at most one
 * per screen: the MainButton is global singleton chrome.
 */
export function PrimaryAction({
  text,
  onClick,
  disabled = false,
  loading = false,
}: {
  text: string;
  onClick: () => void;
  disabled?: boolean;
  loading?: boolean;
}) {
  const webApp = useTgWebApp();
  useMainButton({ text, visible: true, disabled, loading, onClick });
  if (webApp?.MainButton) return null;
  return (
    <Button className="w-full" disabled={disabled || loading} onClick={onClick}>
      {loading && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
      {text}
    </Button>
  );
}
