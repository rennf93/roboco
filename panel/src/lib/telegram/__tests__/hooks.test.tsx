import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import {
  TgWebAppProvider,
  useMainButton,
  useBackButton,
  type MainButtonOptions,
} from "../hooks";
import type { TelegramWebApp } from "../webapp";

function fakeMainButton() {
  return {
    setText: vi.fn(),
    show: vi.fn(),
    hide: vi.fn(),
    enable: vi.fn(),
    disable: vi.fn(),
    showProgress: vi.fn(),
    hideProgress: vi.fn(),
    onClick: vi.fn(),
    offClick: vi.fn(),
  };
}

function fakeBackButton() {
  return {
    show: vi.fn(),
    hide: vi.fn(),
    onClick: vi.fn(),
    offClick: vi.fn(),
  };
}

function webAppWith(overrides: Partial<TelegramWebApp>): TelegramWebApp {
  return {
    ready: () => undefined,
    expand: () => undefined,
    initData: "",
    ...overrides,
  };
}

function MainButtonHarness(props: MainButtonOptions) {
  useMainButton(props);
  return null;
}

function BackButtonHarness({ onBack }: { onBack: (() => void) | null }) {
  useBackButton(onBack);
  return null;
}

describe("useMainButton", () => {
  let mainButton: ReturnType<typeof fakeMainButton>;
  let webApp: TelegramWebApp;

  beforeEach(() => {
    mainButton = fakeMainButton();
    webApp = webAppWith({ MainButton: mainButton });
  });

  it("configures and shows the button declaratively", () => {
    render(
      <TgWebAppProvider webApp={webApp}>
        <MainButtonHarness text="Approve" visible onClick={() => undefined} />
      </TgWebAppProvider>,
    );
    expect(mainButton.setText).toHaveBeenCalledWith("Approve");
    expect(mainButton.enable).toHaveBeenCalled();
    expect(mainButton.hideProgress).toHaveBeenCalled();
    expect(mainButton.show).toHaveBeenCalled();
    expect(mainButton.onClick).toHaveBeenCalledTimes(1);
  });

  it("reflects loading/disabled and invokes the latest onClick closure", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = render(
      <TgWebAppProvider webApp={webApp}>
        <MainButtonHarness text="Approve" visible onClick={first} />
      </TgWebAppProvider>,
    );
    rerender(
      <TgWebAppProvider webApp={webApp}>
        <MainButtonHarness text="Approve" visible loading disabled onClick={second} />
      </TgWebAppProvider>,
    );
    expect(mainButton.showProgress).toHaveBeenCalled();
    expect(mainButton.disable).toHaveBeenCalled();
    // Same subscribed handler survives rerenders but calls the fresh closure.
    expect(mainButton.onClick).toHaveBeenCalledTimes(1);
    const handler = mainButton.onClick.mock.calls[0][0] as () => void;
    handler();
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("unhooks and hides on unmount", () => {
    const { unmount } = render(
      <TgWebAppProvider webApp={webApp}>
        <MainButtonHarness text="Approve" visible onClick={() => undefined} />
      </TgWebAppProvider>,
    );
    unmount();
    expect(mainButton.offClick).toHaveBeenCalledTimes(1);
    expect(mainButton.hide).toHaveBeenCalled();
  });

  it("no-ops without a provider (outside Telegram)", () => {
    expect(() =>
      render(
        <MainButtonHarness text="Approve" visible onClick={() => undefined} />,
      ),
    ).not.toThrow();
  });
});

describe("useBackButton", () => {
  it("shows while a handler is set, hides when null, unhooks on unmount", () => {
    const backButton = fakeBackButton();
    const webApp = webAppWith({ BackButton: backButton });
    const onBack = vi.fn();

    const { rerender, unmount } = render(
      <TgWebAppProvider webApp={webApp}>
        <BackButtonHarness onBack={onBack} />
      </TgWebAppProvider>,
    );
    expect(backButton.show).toHaveBeenCalled();

    const handler = backButton.onClick.mock.calls[0][0] as () => void;
    handler();
    expect(onBack).toHaveBeenCalledTimes(1);

    rerender(
      <TgWebAppProvider webApp={webApp}>
        <BackButtonHarness onBack={null} />
      </TgWebAppProvider>,
    );
    expect(backButton.hide).toHaveBeenCalled();

    unmount();
    expect(backButton.offClick).toHaveBeenCalledTimes(1);
  });
});
