"use client";

import type { ReactNode } from "react";
import { currentPanelRole, isCeoRole } from "./panel-role";

/**
 * Renders children only for a CEO-role session; renders nothing otherwise.
 * Client-side twin of the backend's `require_ceo_role` gate — the backend also
 * 403s its CEO-only endpoints, but an agent-role browser should never even
 * fetch them.
 */
export function CeoGate({ children }: { children: ReactNode }) {
  if (!isCeoRole(currentPanelRole())) return null;
  return <>{children}</>;
}