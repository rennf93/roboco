/**
 * Scroll Restoration Hook
 *
 * Saves and restores scroll position when navigating between pages.
 */

"use client";

import { useEffect, useRef } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { useUIStore } from "@/lib/stores/ui-store";

export function useScrollRestoration(scrollContainerRef?: React.RefObject<HTMLElement>) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { setScrollPosition, getScrollPosition } = useUIStore();

  // Create a unique key for current route including search params
  const routeKey = `${pathname}?${searchParams.toString()}`;
  const hasRestored = useRef(false);

  // Save scroll position on scroll
  useEffect(() => {
    const container = scrollContainerRef?.current ?? window;
    const isWindow = container === window;

    const handleScroll = () => {
      const position = isWindow
        ? { x: window.scrollX, y: window.scrollY }
        : { x: (container as HTMLElement).scrollLeft, y: (container as HTMLElement).scrollTop };

      setScrollPosition(routeKey, position);
    };

    // Debounce scroll handler
    let timeout: NodeJS.Timeout;
    const debouncedScroll = () => {
      clearTimeout(timeout);
      timeout = setTimeout(handleScroll, 100);
    };

    container.addEventListener("scroll", debouncedScroll, { passive: true });

    return () => {
      clearTimeout(timeout);
      container.removeEventListener("scroll", debouncedScroll);
    };
  }, [routeKey, scrollContainerRef, setScrollPosition]);

  // Restore scroll position on mount
  useEffect(() => {
    if (hasRestored.current) return;

    const savedPosition = getScrollPosition(routeKey);
    if (savedPosition) {
      const container = scrollContainerRef?.current ?? window;
      const isWindow = container === window;

      // Delay restoration to ensure content is rendered
      requestAnimationFrame(() => {
        if (isWindow) {
          window.scrollTo(savedPosition.x, savedPosition.y);
        } else {
          (container as HTMLElement).scrollLeft = savedPosition.x;
          (container as HTMLElement).scrollTop = savedPosition.y;
        }
        hasRestored.current = true;
      });
    }
  }, [routeKey, scrollContainerRef, getScrollPosition]);

  // Reset restoration flag when route changes
  useEffect(() => {
    hasRestored.current = false;
  }, [routeKey]);
}
