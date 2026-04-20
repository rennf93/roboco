"use client";

import { useEffect, useRef } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { useUIStore } from "@/lib/stores/ui-store";

/**
 * Global scroll restoration component.
 * Add this to the layout to automatically save/restore scroll positions.
 * Works with the parent scrollable container (the <main> element).
 */
export function ScrollRestoration() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { setScrollPosition, getScrollPosition, setLastVisited } = useUIStore();
  const hasRestored = useRef(false);
  const prevRouteKey = useRef<string>("");

  const routeKey = `${pathname}?${searchParams.toString()}`;

  // Track last visited route per section
  useEffect(() => {
    const section = pathname.split("/")[1] || "home";
    setLastVisited(section, routeKey);
  }, [pathname, routeKey, setLastVisited]);

  // Find the scrollable main container
  useEffect(() => {
    // The main element is the parent with overflow-auto
    const mainElement = document.querySelector("main");
    if (!mainElement) return;

    // Reset restoration flag when route changes
    if (prevRouteKey.current !== routeKey) {
      hasRestored.current = false;
      prevRouteKey.current = routeKey;
    }

    const saveScroll = () => {
      setScrollPosition(routeKey, {
        x: mainElement.scrollLeft,
        y: mainElement.scrollTop,
      });
    };

    // Save on scroll (debounced)
    let timeout: NodeJS.Timeout;
    const handleScroll = () => {
      clearTimeout(timeout);
      timeout = setTimeout(saveScroll, 150);
    };

    mainElement.addEventListener("scroll", handleScroll, { passive: true });

    // Restore scroll position
    if (!hasRestored.current) {
      const savedPosition = getScrollPosition(routeKey);
      if (savedPosition && savedPosition.y > 0) {
        requestAnimationFrame(() => {
          mainElement.scrollTo({
            top: savedPosition.y,
            left: savedPosition.x,
            behavior: "instant",
          });
          hasRestored.current = true;
        });
      } else {
        // Scroll to top for new routes
        mainElement.scrollTo({ top: 0, behavior: "instant" });
        hasRestored.current = true;
      }
    }

    return () => {
      clearTimeout(timeout);
      mainElement.removeEventListener("scroll", handleScroll);
    };
  }, [routeKey, setScrollPosition, getScrollPosition]);

  return null;
}
