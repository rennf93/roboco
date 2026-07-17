"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { ArrowUp, ArrowDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { HelpTip } from "@/components/ui/help-tip";

// Fraction of main's visible height a user must have scrolled (or have left
// to scroll) before the corresponding button engages.
const ENGAGE_RATIO = 0.5;

/**
 * Floating back-to-top / jump-to-bottom control for the shared <main>
 * scroll container (see layout.tsx) — same querySelector("main") target
 * scroll-restoration.tsx uses. Self-contained: no context/store.
 */
export function ScrollJumpButtons() {
  const pathname = usePathname();
  const [canScrollUp, setCanScrollUp] = useState(false);
  const [canScrollDown, setCanScrollDown] = useState(false);

  useEffect(() => {
    const mainElement = document.querySelector("main");
    if (!mainElement) return;

    const update = () => {
      const { scrollTop, scrollHeight, clientHeight } = mainElement;
      const overflows = scrollHeight > clientHeight + 1;
      const threshold = clientHeight * ENGAGE_RATIO;
      setCanScrollUp(overflows && scrollTop > threshold);
      setCanScrollDown(
        overflows && scrollHeight - scrollTop - clientHeight > threshold,
      );
    };

    update();
    mainElement.addEventListener("scroll", update, { passive: true });

    // main's own box is pinned by the flex layout, so overflowing content
    // never resizes main itself — watch its children (a page may render a
    // multi-root fragment, so all of them, not just the first). main is
    // observed too for viewport resizes; re-run on route change since
    // navigation swaps the content nodes.
    const observer = new ResizeObserver(update);
    observer.observe(mainElement);
    Array.from(mainElement.children).forEach((child) => observer.observe(child));

    return () => {
      mainElement.removeEventListener("scroll", update);
      observer.disconnect();
    };
  }, [pathname]);

  if (!canScrollUp && !canScrollDown) return null;

  const scrollTo = (top: number) =>
    document.querySelector("main")?.scrollTo({ top, behavior: "smooth" });

  return (
    <div className="fixed right-4 bottom-20 z-30 flex flex-col gap-2 md:right-6 md:bottom-6">
      {canScrollUp && (
        <HelpTip label="Back to top" side="left">
          <Button
            variant="secondary"
            size="icon"
            className="rounded-full shadow-lg"
            onClick={() => scrollTo(0)}
          >
            <ArrowUp className="h-4 w-4" />
            <span className="sr-only">Back to top</span>
          </Button>
        </HelpTip>
      )}
      {canScrollDown && (
        <HelpTip label="Jump to bottom" side="left">
          <Button
            variant="secondary"
            size="icon"
            className="rounded-full shadow-lg"
            onClick={() =>
              scrollTo(document.querySelector("main")?.scrollHeight ?? 0)
            }
          >
            <ArrowDown className="h-4 w-4" />
            <span className="sr-only">Jump to bottom</span>
          </Button>
        </HelpTip>
      )}
    </div>
  );
}
