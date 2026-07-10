import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// Absolute inline timestamp shown alongside relative "Xm ago" text — e.g.
// "Jul 10, 2026, 3:45 PM". Shared so progress updates and checkpoints render
// the same format instead of each screen inventing its own.
export function formatAbsoluteTimestamp(timestamp: string): string {
  return new Date(timestamp).toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
