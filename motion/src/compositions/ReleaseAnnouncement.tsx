import type { CSSProperties } from "react";
import React from "react";
import type { CalculateMetadataFunction } from "remotion";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { color, font } from "../theme";

export type Orientation = "vertical" | "square";

// A `type` alias, not an `interface`: Remotion's Composition/
// CalculateMetadataFunction generics constrain Props to
// `Record<string, unknown>`, which only object-literal type aliases satisfy
// structurally — an `interface` (open for declaration merging) does not.
export type ReleaseAnnouncementProps = {
  /** One or two spoken/on-screen sentences — the voiceover-style hook. */
  script: string;
  /** Release version, e.g. "0.18.0" — rendered as "v0.18.0". */
  version: string;
  /** Shipped-feature bullets. Only the first 4 are shown. */
  highlights: string[];
  /** Drives calculateMetadata's aspect-ratio switch below. */
  orientation: Orientation;
};

/**
 * The whole reason this composition can produce two cuts from one timeline:
 * width is always 1080, only height (and therefore the available vertical
 * canvas) changes. No width/height render param exists in Remotion — this
 * is the documented mechanism (selectComposition + renderMedia both receive
 * the identical inputProps object; this function reads inputProps.orientation
 * off it and returns the matching frame).
 */
export const calculateReleaseAnnouncementMetadata: CalculateMetadataFunction<
  ReleaseAnnouncementProps
> = ({ props }) => {
  const isSquare = props.orientation === "square";
  return {
    width: 1080,
    height: isSquare ? 1080 : 1920,
  };
};

// One spring signature for every entrance in this composition — a
// consistent, restrained motion signature (slight overshoot, quick settle)
// rather than ad hoc easing per element.
const springConfig = { damping: 17, mass: 0.7, stiffness: 140 };

const enter = (frame: number, delayFrames: number, fps: number) =>
  spring({ frame: frame - delayFrames, fps, config: springConfig });

const rise = (progress: number, distance = 22) =>
  interpolate(progress, [0, 1], [distance, 0], {
    extrapolateLeft: "clamp",
  });

const fadeIn = (progress: number) =>
  interpolate(progress, [0, 1], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

const HIGHLIGHT_STAGGER = 14;
const HIGHLIGHTS_START = 50;
const MAX_HIGHLIGHTS = 4;

export const ReleaseAnnouncement: React.FC<ReleaseAnnouncementProps> = ({
  script,
  version,
  highlights,
  orientation,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const isSquare = orientation === "square";

  const versionLabel = version.startsWith("v") ? version : `v${version}`;
  const visibleHighlights = highlights.slice(0, MAX_HIGHLIGHTS);

  const kickerP = enter(frame, 0, fps);
  const headlineP = enter(frame, 8, fps);
  const scriptP = enter(frame, 24, fps);
  const outroP = enter(frame, durationInFrames - 70, fps);

  // A slow "signal scan" that runs the whole clip — the one piece of
  // continuous ambient motion beyond the entrance beats, so the frame
  // never goes fully static once everything has landed.
  const scan = interpolate(frame, [0, durationInFrames], [-0.12, 1.12], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const bottomPad = isSquare ? 72 : 148;
  const scanTop = isSquare ? 72 : 104;

  const kickerStyle: CSSProperties = {
    opacity: fadeIn(kickerP),
    transform: `translateY(${rise(kickerP, 16)}px)`,
    fontFamily: font.body,
    fontSize: 30,
    fontWeight: 600,
    letterSpacing: 6,
    textTransform: "uppercase",
    color: color.accent,
    marginBottom: 20,
  };

  const headlineStyle: CSSProperties = {
    opacity: fadeIn(headlineP),
    transform: `translateY(${rise(headlineP)}px) scale(${interpolate(
      headlineP,
      [0, 1],
      [0.94, 1],
      { extrapolateLeft: "clamp" },
    )})`,
    transformOrigin: "left bottom",
    fontFamily: font.display,
    fontSize: 148,
    // Share Tech Mono ships one static weight only (400) — no real bold to
    // request; forcing 700 would just make Chromium synthesize a faux-bold.
    fontWeight: 400,
    lineHeight: 1,
    color: color.paper,
    margin: 0,
    marginBottom: 28,
  };

  const scriptStyle: CSSProperties = {
    opacity: fadeIn(scriptP),
    transform: `translateY(${rise(scriptP)}px)`,
    fontFamily: font.body,
    fontSize: 44,
    fontWeight: 500,
    lineHeight: 1.25,
    color: color.paper,
    maxWidth: 820,
    margin: 0,
    marginBottom: 40,
  };

  const outroStyle: CSSProperties = {
    position: "absolute",
    bottom: 56,
    left: 84,
    opacity: fadeIn(outroP),
    transform: `translateY(${rise(outroP, 14)}px)`,
    fontFamily: font.body,
    fontSize: 26,
    fontWeight: 500,
    letterSpacing: 2,
    color: color.muted,
  };

  return (
    <AbsoluteFill style={{ backgroundColor: color.ink }}>
      <div
        style={{
          position: "absolute",
          top: scanTop,
          left: 84,
          right: 84,
          height: 2,
          backgroundColor: color.hairline,
        }}
      >
        <div
          style={{
            position: "absolute",
            top: -3,
            left: `${scan * 100}%`,
            width: 64,
            height: 8,
            borderRadius: 4,
            transform: "translateX(-50%)",
            backgroundColor: color.accent,
            boxShadow: `0 0 28px ${color.accent}`,
          }}
        />
      </div>

      <AbsoluteFill
        style={{
          justifyContent: "flex-end",
          paddingLeft: 84,
          paddingRight: 96,
          paddingBottom: bottomPad,
        }}
      >
        <div style={kickerStyle}>New release</div>
        <h1 style={headlineStyle}>{versionLabel}</h1>
        <p style={scriptStyle}>{script}</p>
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {visibleHighlights.map((item, index) => {
            const delay = HIGHLIGHTS_START + index * HIGHLIGHT_STAGGER;
            const progress = enter(frame, delay, fps);
            const itemStyle: CSSProperties = {
              display: "flex",
              alignItems: "center",
              opacity: fadeIn(progress),
              transform: `translateX(${interpolate(
                progress,
                [0, 1],
                [-36, 0],
                { extrapolateLeft: "clamp" },
              )}px)`,
              marginBottom: 18,
              fontFamily: font.body,
              fontSize: 34,
              fontWeight: 400,
              color: color.paper,
            };
            return (
              <li key={item} style={itemStyle}>
                <span
                  style={{
                    display: "inline-block",
                    width: 22,
                    height: 3,
                    marginRight: 18,
                    flexShrink: 0,
                    backgroundColor: color.accent,
                  }}
                />
                {item}
              </li>
            );
          })}
        </ul>
      </AbsoluteFill>

      <div style={outroStyle}>roboco.tech</div>
    </AbsoluteFill>
  );
};
