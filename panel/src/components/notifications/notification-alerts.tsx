"use client";

import { useEffect, useRef } from "react";
import { toast } from "sonner";
import { useNotificationStream } from "@/hooks/use-websocket";
import { useUIStore } from "@/store";

/** ~120ms, low-volume beep via Web Audio — no audio asset. Never throws. */
function playChime() {
  try {
    const AudioCtx =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = 880;
    gain.gain.value = 0.05;
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.12);
    osc.onended = () => void ctx.close();
  } catch {
    // Autoplay/permission blocks are expected on some browsers — no-op.
  }
}

/**
 * Watches the live notification stream and toasts + optionally chimes for
 * newly-arrived entries. Mount exactly once (next to the bell). Renders
 * nothing.
 */
export function NotificationAlerts() {
  const { notifications } = useNotificationStream();
  const notificationsEnabled = useUIStore((s) => s.notificationsEnabled);
  const soundEnabled = useUIStore((s) => s.soundEnabled);

  // null = not yet initialized; primes on first render so a page load never
  // toasts a backlog the stream replays on connect.
  const seenCountRef = useRef<number | null>(null);

  useEffect(() => {
    if (seenCountRef.current === null) {
      seenCountRef.current = notifications.length;
      return;
    }
    const newOnes = notifications.slice(seenCountRef.current);
    seenCountRef.current = notifications.length;
    if (newOnes.length === 0 || !notificationsEnabled) return;

    for (const n of newOnes) {
      toast(n.subject ?? "New notification", {
        description: n.priority ? `Priority: ${n.priority}` : undefined,
      });
    }
    if (soundEnabled) playChime();
  }, [notifications, notificationsEnabled, soundEnabled]);

  return null;
}
