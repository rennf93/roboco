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

// Hand an in-memory text payload to the browser's save-file machinery: wrap
// it in a Blob, object-URL it, click a transient download anchor, and revoke
// the URL. No navigation, no second origin — the receipt never leaves the
// client except as the user's own file. Shared so every download action
// (attestation receipts, future exports) rides the same tested path.
export function triggerTextFileDownload(
  content: string,
  filename: string,
  mimeType: string,
): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
