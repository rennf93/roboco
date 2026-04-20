"use client";

import { Badge } from "@/components/ui/badge";
import { AlertTriangle } from "lucide-react";

export function BlockedBadge() {
  return (
    <Badge variant="destructive" className="text-xs gap-1">
      <AlertTriangle className="h-3 w-3" />
      Blocked
    </Badge>
  );
}
