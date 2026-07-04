import { Thumbnail } from "@remotion/player";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  calculateReleaseAnnouncementMetadata,
  ReleaseAnnouncement,
  type ReleaseAnnouncementProps,
} from "./ReleaseAnnouncement";

const baseProps: Omit<ReleaseAnnouncementProps, "orientation"> = {
  script: "Ship day. Faster renders, sharper docs, one less thing to babysit.",
  version: "0.18.0",
  highlights: [
    "Fable-mode adopted fleet-wide",
    "FE/UX-UI design bar shipped",
    "Feature spotlight goes live on X",
  ],
};

describe("calculateReleaseAnnouncementMetadata", () => {
  it("returns 1080x1920 for the vertical cut", async () => {
    // calculateMetadata may be sync or async per Remotion's type — awaiting
    // a plain (non-Promise) return value resolves it immediately either way.
    const metadata = await calculateReleaseAnnouncementMetadata({
      props: { ...baseProps, orientation: "vertical" },
      defaultProps: { ...baseProps, orientation: "vertical" },
      abortSignal: new AbortController().signal,
      isRendering: false,
      compositionId: "ReleaseAnnouncement",
    });
    expect(metadata.width).toBe(1080);
    expect(metadata.height).toBe(1920);
  });

  it("returns 1080x1080 for the square cut", async () => {
    const metadata = await calculateReleaseAnnouncementMetadata({
      props: { ...baseProps, orientation: "square" },
      defaultProps: { ...baseProps, orientation: "square" },
      abortSignal: new AbortController().signal,
      isRendering: false,
      compositionId: "ReleaseAnnouncement",
    });
    expect(metadata.width).toBe(1080);
    expect(metadata.height).toBe(1080);
  });
});

describe("ReleaseAnnouncement", () => {
  it.each(["vertical", "square"] as const)(
    "mounts without throwing (%s)",
    (orientation) => {
      const height = orientation === "square" ? 1080 : 1920;
      const { unmount, container } = render(
        <Thumbnail
          component={ReleaseAnnouncement}
          compositionWidth={1080}
          compositionHeight={height}
          durationInFrames={300}
          fps={30}
          frameToDisplay={120}
          inputProps={{ ...baseProps, orientation }}
        />,
      );
      expect(container).toBeTruthy();
      unmount();
    },
  );
});
