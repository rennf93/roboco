import type { ReactNode } from "react";
import { PageRefreshProvider } from "../page-refresh-provider";

export function PageRefreshWrapper({ children }: { children: ReactNode }) {
  return <PageRefreshProvider>{children}</PageRefreshProvider>;
}

export function DisabledPageRefreshWrapper({
  children,
}: {
  children: ReactNode;
}) {
  return <PageRefreshProvider disabled>{children}</PageRefreshProvider>;
}
