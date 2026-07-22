"use client";

/**
 * The cockpit's motion primitives — a count-up hook for hero numerals and
 * the bottom sheet every detail view rides. Kept dependency-free (rAF + CSS
 * keyframes from globals.css); prefers-reduced-motion users get the final
 * state instantly.
 */

import { useEffect, useRef, useState } from "react";
import { X } from "@phosphor-icons/react";
import { haptics } from "@/lib/telegram/webapp";
import { useBackButton } from "@/lib/telegram/hooks";

function reducedMotion(): boolean {
  // No matchMedia (SSR, bare jsdom) counts as reduced — jump to the target.
  return (
    typeof window === "undefined" ||
    typeof window.matchMedia !== "function" ||
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** Animate a numeric value toward `target` (ease-out cubic). First mount
 * counts up from zero — the wallet-style hero entrance. */
export function useCountUp(target: number, durationMs = 650): number {
  const [value, setValue] = useState(0);
  const fromRef = useRef(0);
  useEffect(() => {
    const from = fromRef.current;
    fromRef.current = target;
    let raf = 0;
    if (from === target || reducedMotion()) {
      raf = requestAnimationFrame(() => setValue(target));
      return () => cancelAnimationFrame(raf);
    }
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min((now - start) / durationMs, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(from + (target - from) * eased);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, durationMs]);
  return value;
}

/**
 * Bottom sheet — slide-up detail surface over the active tab. Renders
 * inside #tg-shell (never a portal) so the cockpit theme variables apply.
 * Telegram's native BackButton dismisses it while it's open; outside
 * Telegram the backdrop tap and the X do the same job.
 */
export function TgSheet({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}) {
  useBackButton(open ? onClose : null);
  useEffect(() => {
    if (open) haptics.tap();
  }, [open]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50">
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="tg-backdrop absolute inset-0 bg-black/50"
      />
      <div
        role="dialog"
        aria-modal="true"
        className="tg-sheet absolute inset-x-0 bottom-0 mx-auto flex max-h-[85dvh] w-full max-w-[430px] flex-col rounded-t-[28px] bg-card text-card-foreground shadow-[inset_0_1px_0_rgba(255,255,255,0.06),0_-16px_48px_-16px_rgba(0,0,0,0.9)]"
      >
        <div className="mx-auto mt-2.5 h-1 w-9 shrink-0 rounded-full bg-muted-foreground/30" />
        <header className="flex items-center justify-between gap-2 px-4 pb-2 pt-3">
          <h2 className="text-[15px] font-semibold text-foreground">{title}</h2>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="flex h-9 w-9 items-center justify-center rounded-full bg-muted text-muted-foreground transition-transform active:scale-90"
          >
            <X weight="bold" className="h-4 w-4" />
          </button>
        </header>
        <div className="overflow-y-auto px-4 pb-[calc(1.25rem+env(safe-area-inset-bottom))]">
          {children}
        </div>
      </div>
    </div>
  );
}
