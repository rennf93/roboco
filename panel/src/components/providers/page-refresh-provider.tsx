"use client";

import * as React from "react";

/**
 * A callback that will be invoked when the page refresh is triggered.
 * Synchronous callbacks are allowed; asynchronous callbacks are awaited.
 */
export type RefreshCallback = () => void | Promise<void>;

/**
 * The API exposed to consumers of the page refresh context.
 */
export interface PageRefreshState {
  /** Whether refresh actions are currently disabled. */
  disabled: boolean;
  /** Whether a refresh cycle is currently in progress. */
  loading: boolean;
  /** Register a callback to be invoked on the next refresh. */
  register: (callback: RefreshCallback) => void;
  /** Unregister a previously registered callback. */
  unregister: (callback: RefreshCallback) => void;
  /** Trigger all registered callbacks and update the loading state. */
  refresh: () => Promise<void>;
}

export interface PageRefreshProviderProps {
  children: React.ReactNode;
}

export const PageRefreshContext = React.createContext<PageRefreshState | null>(
  null,
);

/**
 * Provides a scoped page-refresh registry for child components.
 *
 * Pages and panels can register callbacks that refetch data; UI chrome can call
 * `refresh()` and reflect the combined loading/disabled state. `disabled` reflects
 * whether any callback is currently registered — there's nothing to refresh when
 * the registry is empty.
 */
export function PageRefreshProvider({ children }: PageRefreshProviderProps) {
  const [loading, setLoading] = React.useState(false);
  const [registeredCount, setRegisteredCount] = React.useState(0);
  const callbacksRef = React.useRef(new Set<RefreshCallback>());
  const refreshingRef = React.useRef(false);
  const disabled = registeredCount === 0;

  const register = React.useCallback((callback: RefreshCallback) => {
    callbacksRef.current.add(callback);
    setRegisteredCount(callbacksRef.current.size);
  }, []);

  const unregister = React.useCallback((callback: RefreshCallback) => {
    callbacksRef.current.delete(callback);
    setRegisteredCount(callbacksRef.current.size);
  }, []);

  const refresh = React.useCallback(async () => {
    if (disabled || refreshingRef.current) {
      return;
    }

    refreshingRef.current = true;
    setLoading(true);
    try {
      const callbacks = Array.from(callbacksRef.current);
      await Promise.all(
        callbacks.map((callback) => Promise.resolve(callback())),
      );
    } finally {
      refreshingRef.current = false;
      setLoading(false);
    }
  }, [disabled]);

  const value = React.useMemo<PageRefreshState>(
    () => ({
      disabled,
      loading,
      register,
      unregister,
      refresh,
    }),
    [disabled, loading, register, unregister, refresh],
  );

  return (
    <PageRefreshContext.Provider value={value}>
      {children}
    </PageRefreshContext.Provider>
  );
}
