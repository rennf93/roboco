"use client";

import { useCallback, useMemo, useState, type ReactNode } from "react";
import {
  PageRefreshContext,
  type RefreshCallback,
} from "@/store/page-refresh-context";

/**
 * Provides page-scoped refresh state to the application.
 *
 * Pages or page-level components register a refresh callback keyed by scope.
 * The active scope is tracked independently so that global UI (e.g. a navbar
 * refresh button) can trigger the refresh handler for whatever page the user is
 * currently viewing.
 */
export function PageRefreshProvider({ children }: { children: ReactNode }) {
  const [activeScope, setActiveScope] = useState<string | null>(null);
  const [callbacks, setCallbacks] = useState<Map<string, RefreshCallback>>(
    () => new Map(),
  );

  const register = useCallback((scope: string, callback: RefreshCallback) => {
    setCallbacks((prev) => {
      const next = new Map(prev);
      next.set(scope, callback);
      return next;
    });
  }, []);

  const unregister = useCallback((scope: string) => {
    setCallbacks((prev) => {
      const next = new Map(prev);
      next.delete(scope);
      return next;
    });
  }, []);

  const refresh = useCallback(
    async (scope?: string) => {
      const target = scope ?? activeScope;
      if (!target) return;

      const callback = callbacks.get(target);
      if (callback) {
        await callback();
      }
    },
    [callbacks, activeScope],
  );

  const value = useMemo(
    () => ({
      activeScope,
      setActiveScope,
      register,
      unregister,
      refresh,
    }),
    [activeScope, register, unregister, refresh],
  );

  return (
    <PageRefreshContext.Provider value={value}>
      {children}
    </PageRefreshContext.Provider>
  );
}
