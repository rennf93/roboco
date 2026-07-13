"use client";

import { useEffect, useRef } from "react";
import { usePageRefresh } from "@/hooks";
import { useUIStore } from "@/store";

/**
 * Ticks the page-refresh registry on an interval when the user's Auto Refresh
 * preference is on. Must render inside `PageRefreshProvider`. Renders nothing.
 */
export function AutoRefreshDriver() {
  const { refresh, disabled, loading } = usePageRefresh();
  const autoRefresh = useUIStore((s) => s.autoRefresh);
  const refreshIntervalSeconds = useUIStore((s) => s.refreshIntervalSeconds);

  // Read via ref inside the tick so an in-flight refresh only skips that one
  // tick, instead of tearing down/re-arming the interval on every loading flip.
  const loadingRef = useRef(loading);
  useEffect(() => {
    loadingRef.current = loading;
  }, [loading]);

  useEffect(() => {
    if (!autoRefresh || disabled) return;
    const id = setInterval(() => {
      if (!loadingRef.current) void refresh();
    }, refreshIntervalSeconds * 1000);
    return () => clearInterval(id);
  }, [autoRefresh, disabled, refreshIntervalSeconds, refresh]);

  return null;
}
