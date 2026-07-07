import { createContext } from "react";

/**
 * Callback invoked when the user requests a refresh for a registered page scope.
 * May be synchronous or asynchronous.
 */
export type RefreshCallback = () => void | Promise<void>;

/**
 * Value exposed by PageRefreshProvider. Pages register their scope-specific
 * refresh handler; callers such as the navbar refresh button trigger the
 * active scope's handler.
 */
export interface PageRefreshContextValue {
  /** The scope currently considered active, or null if none. */
  activeScope: string | null;

  /** Mark a scope as active (e.g. on page mount) or clear it. */
  setActiveScope: (scope: string | null) => void;

  /** Register a refresh callback for the given scope. */
  register: (scope: string, callback: RefreshCallback) => void;

  /** Remove a previously registered refresh callback. */
  unregister: (scope: string) => void;

  /**
   * Trigger the refresh callback for a specific scope.
   * If no scope is provided, the active scope is refreshed.
   */
  refresh: (scope?: string) => Promise<void>;
}

/**
 * React context for page-scoped refresh state. Consumers should use the public
 * `usePageRefresh` hook (added in a sibling task) rather than reading this
 * context directly.
 */
export const PageRefreshContext = createContext<PageRefreshContextValue | null>(
  null,
);
