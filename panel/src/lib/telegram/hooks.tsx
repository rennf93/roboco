"use client";

/**
 * React bindings for the Telegram WebApp bridge. The page bootstraps the
 * bridge once (real object or dev mock) and provides it here; components
 * reach native chrome (MainButton, BackButton) through these hooks and
 * never touch `window.Telegram` directly — that keeps every consumer
 * null-safe outside Telegram by construction.
 */

import { createContext, useContext, useEffect, useRef } from "react";
import type { TelegramWebApp } from "./webapp";

const TgWebAppContext = createContext<TelegramWebApp | null>(null);

export function TgWebAppProvider({
  webApp,
  children,
}: {
  webApp: TelegramWebApp | null;
  children: React.ReactNode;
}) {
  return (
    <TgWebAppContext.Provider value={webApp}>
      {children}
    </TgWebAppContext.Provider>
  );
}

/** The bootstrapped bridge, or null when rendered outside the provider
 * (tests) or before bootstrap resolves. */
export function useTgWebApp(): TelegramWebApp | null {
  return useContext(TgWebAppContext);
}

export interface MainButtonOptions {
  text: string;
  visible: boolean;
  disabled?: boolean;
  /** Shows Telegram's spinner on the button while a mutation is in flight. */
  loading?: boolean;
  onClick: () => void;
}

/**
 * Drives Telegram's native bottom action button declaratively. The button is
 * global singleton chrome, so exactly one mounted component should own it at
 * a time (the focused card, not every card). Hidden + unhooked on unmount.
 * No-ops when the bridge (or its MainButton) is absent — callers that need a
 * fallback can render their own button when `useTgWebApp()?.MainButton` is
 * missing.
 */
export function useMainButton({
  text,
  visible,
  disabled = false,
  loading = false,
  onClick,
}: MainButtonOptions): void {
  const webApp = useTgWebApp();
  const mainButton = webApp?.MainButton;
  const onClickRef = useRef(onClick);
  useEffect(() => {
    onClickRef.current = onClick;
  }, [onClick]);

  useEffect(() => {
    if (!mainButton) return;
    const handler = () => onClickRef.current();
    mainButton.onClick(handler);
    return () => {
      mainButton.offClick(handler);
      mainButton.hide();
    };
  }, [mainButton]);

  useEffect(() => {
    if (!mainButton) return;
    mainButton.setText(text);
    if (disabled) {
      mainButton.disable();
    } else {
      mainButton.enable();
    }
    if (loading) {
      mainButton.showProgress();
    } else {
      mainButton.hideProgress();
    }
    if (visible) {
      mainButton.show();
    } else {
      mainButton.hide();
    }
  }, [mainButton, text, visible, disabled, loading]);
}

/**
 * Shows Telegram's native header back button while `onBack` is non-null and
 * invokes it on tap. Pass null to hide (e.g. at the root of a card stack).
 */
export function useBackButton(onBack: (() => void) | null): void {
  const webApp = useTgWebApp();
  const backButton = webApp?.BackButton;
  const onBackRef = useRef(onBack);
  useEffect(() => {
    onBackRef.current = onBack;
  }, [onBack]);

  useEffect(() => {
    if (!backButton) return;
    const handler = () => onBackRef.current?.();
    backButton.onClick(handler);
    return () => {
      backButton.offClick(handler);
      backButton.hide();
    };
  }, [backButton]);

  useEffect(() => {
    if (!backButton) return;
    if (onBack) {
      backButton.show();
    } else {
      backButton.hide();
    }
  }, [backButton, onBack]);
}
