import React from "react";
import { Composition } from "remotion";
import {
  calculateReleaseAnnouncementMetadata,
  ReleaseAnnouncement,
  type ReleaseAnnouncementProps,
} from "./compositions/ReleaseAnnouncement";

const releaseAnnouncementDefaultProps: ReleaseAnnouncementProps = {
  script:
    "Ship day. Faster renders, sharper docs, one less thing to babysit.",
  version: "0.18.0",
  highlights: [
    "Fable-mode adopted fleet-wide",
    "FE/UX-UI design bar shipped",
    "Feature spotlight goes live on X",
  ],
  orientation: "vertical",
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
      {/*
        width/height/fps/durationInFrames below are the Studio-preview
        defaults only. At bundle time the sidecar's selectComposition() +
        renderMedia() calls both pass the real inputProps, and
        calculateReleaseAnnouncementMetadata overrides width/height from
        inputProps.orientation — see that function's doc comment.
      */}
      <Composition
        id="ReleaseAnnouncement"
        component={ReleaseAnnouncement}
        durationInFrames={300}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={releaseAnnouncementDefaultProps}
        calculateMetadata={calculateReleaseAnnouncementMetadata}
      />
    </>
  );
};
