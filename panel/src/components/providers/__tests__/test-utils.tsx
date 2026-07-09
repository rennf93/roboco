import type { ReactNode } from "react";
import { PageRefreshProvider } from "../page-refresh-provider";

/**
 * A `PageRefreshProvider` with nothing registered — `disabled` starts `true`
 * since it is derived from the registry, not a prop.
 */
export function PageRefreshWrapper({ children }: { children: ReactNode }) {
  return <PageRefreshProvider>{children}</PageRefreshProvider>;
}
