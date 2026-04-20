"use client";

interface HealthIndicatorProps {
  status: "ok" | "slow" | "critical";
  size?: "sm" | "md" | "lg";
}

const statusEmoji: Record<string, string> = {
  ok: "\uD83D\uDFE2",
  slow: "\uD83D\uDFE1",
  critical: "\uD83D\uDD34",
};

const statusLabel: Record<string, string> = {
  ok: "OK",
  slow: "SLOW",
  critical: "CRITICAL",
};

const statusColor: Record<string, string> = {
  ok: "text-green-600",
  slow: "text-yellow-600",
  critical: "text-red-600",
};

export function HealthIndicator({ status, size = "md" }: HealthIndicatorProps) {
  const sizeClasses = {
    sm: "text-sm",
    md: "text-base",
    lg: "text-lg",
  };

  return (
    <span className={`${sizeClasses[size]} ${statusColor[status]} font-medium`}>
      {statusEmoji[status]} {statusLabel[status]}
    </span>
  );
}
