// Panel kit helpers — tiny, framework-free, run synchronously at load
// (no requestAnimationFrame, no timers). Loaded after props.js and before
// any inline script that reads window.PanelKit, same ordering convention as
// props.js itself (see motion/README.md).
(function () {
  "use strict";

  var DEFAULT_STAGGER = 0.045; // seconds between characters

  // Splits `el`'s current text into one <span class="pk-type__char"> per
  // character (whitespace preserved via CSS `white-space: pre` on the
  // container) and gives each an absolute animation-delay so the reveal
  // cascades left-to-right. `delay` is the composition-absolute second the
  // first character should appear — the same convention every other
  // animation-delay in this house uses (seconds from frame 0, not relative
  // to the element). Deterministic: runs once, before any animation fires.
  function typeText(el, options) {
    if (!el) return;
    var opts = options || {};
    var delay = typeof opts.delay === "number" ? opts.delay : 0;
    var stagger = typeof opts.stagger === "number" ? opts.stagger : DEFAULT_STAGGER;
    var text = el.textContent || "";

    el.textContent = "";
    el.classList.add("pk-type");
    for (var i = 0; i < text.length; i++) {
      var span = document.createElement("span");
      span.className = "pk-type__char";
      span.textContent = text[i];
      span.style.animationDelay = (delay + i * stagger).toFixed(3) + "s";
      el.appendChild(span);
    }
  }

  var pathCounter = 0;

  // Choreographs a .pk-cursor along data-waypoints: "t x y [click]; ..." —
  // composition-absolute seconds, content-box px. Injects generated
  // @keyframes so the cursor TRAVELS through every waypoint with per-leg
  // easing, fades in at the first stop and out at the last, sways idly
  // like a resting hand, and pulses a click ring (plus a glyph press dip)
  // at each waypoint flagged `click`. The single-glide --pk-cursor-x0/x1
  // API stays untouched for cursors without data-waypoints — this engine
  // only takes over when the attribute is present.
  function choreographCursor(el) {
    if (!el || !el.getAttribute) return;
    var raw = el.getAttribute("data-waypoints");
    if (!raw) return;
    var points = [];
    raw.split(";").forEach(function (chunk) {
      var parts = chunk.trim().split(/\s+/);
      if (parts.length < 3) return;
      points.push({
        t: parseFloat(parts[0]),
        x: parseFloat(parts[1]),
        y: parseFloat(parts[2]),
        click: parts[3] === "click"
      });
    });
    if (points.length < 2) return;

    var t0 = points[0].t;
    var span = points[points.length - 1].t - t0;
    if (!(span > 0)) return;
    var name = "pk-cursor-path-" + (++pathCounter);
    var pct = function (t) {
      return Math.min(100, Math.max(0, ((t - t0) / span) * 100)).toFixed(3);
    };

    var css = "@keyframes " + name + " {\n";
    points.forEach(function (p, i) {
      css += "  " + pct(p.t) + "% { transform: translate(" + p.x + "px, " + p.y + "px);";
      if (i < points.length - 1) {
        css += " animation-timing-function: cubic-bezier(0.4, 0, 0.2, 1);";
      }
      css += " }\n";
    });
    css += "}\n";
    // Fade in over the first 0.4s, out over the last 0.6s — a cursor that
    // pops in or blinks out of existence reads as a bug, not a hand.
    css +=
      "@keyframes " + name + "-fade {\n" +
      "  0% { opacity: 0; }\n" +
      "  " + pct(t0 + 0.4) + "% { opacity: 1; }\n" +
      "  " + pct(t0 + span - 0.6) + "% { opacity: 1; }\n" +
      "  100% { opacity: 0; }\n}\n";

    var clicks = points.filter(function (p) { return p.click; });
    if (clicks.length) {
      // Press dip on the glyph, one dip per click, expressed as stops on a
      // single generated keyframes rule spanning the whole path.
      css += "@keyframes " + name + "-press {\n  0% { transform: scale(1); }\n";
      clicks.forEach(function (p) {
        css +=
          "  " + pct(p.t - 0.12) + "% { transform: scale(1); }\n" +
          "  " + pct(p.t) + "% { transform: scale(0.85); }\n" +
          "  " + pct(p.t + 0.22) + "% { transform: scale(1); }\n";
      });
      css += "  100% { transform: scale(1); }\n}\n";
    }

    var style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);

    // Sway wrapper: the path moves the cursor, the wrapper breathes — a
    // perfectly still pointer between legs reads as a freeze-frame.
    var glyph = el.querySelector(".pk-cursor__glyph");
    if (glyph) {
      var sway = document.createElement("div");
      sway.className = "pk-cursor__sway";
      glyph.parentNode.insertBefore(sway, glyph);
      sway.appendChild(glyph);
      if (clicks.length) {
        glyph.style.animation = name + "-press " + span + "s linear " + t0 + "s both";
      }
    }
    clicks.forEach(function (p) {
      var ring = document.createElement("div");
      ring.className = "pk-cursor__ring";
      ring.style.setProperty("--pk-click-delay", p.t + "s");
      el.appendChild(ring);
    });

    el.style.animation =
      name + " " + span + "s linear " + t0 + "s both, " +
      name + "-fade " + span + "s linear " + t0 + "s both";
  }

  function choreographAllCursors() {
    var els = document.querySelectorAll(".pk-cursor[data-waypoints]");
    for (var i = 0; i < els.length; i++) choreographCursor(els[i]);
  }

  // Camera moves on a .pk-camera wrapper via data-shots: "t x y scale; ..."
  // — composition-absolute seconds, a translate in px and a zoom factor per
  // shot, eased leg by leg like the cursor path. A locked-off camera for a
  // whole clip is the single biggest "screen recording, not a video" tell;
  // push toward what matters each beat, pull wide for reveals, settle to
  // end. Keep it subtle: scale <= 1.08, translate <= ~160px.
  function choreographCamera(el) {
    if (!el || !el.getAttribute) return;
    var raw = el.getAttribute("data-shots");
    if (!raw) return;
    var shots = [];
    raw.split(";").forEach(function (chunk) {
      var parts = chunk.trim().split(/\s+/);
      if (parts.length < 4) return;
      shots.push({
        t: parseFloat(parts[0]),
        x: parseFloat(parts[1]),
        y: parseFloat(parts[2]),
        s: parseFloat(parts[3])
      });
    });
    if (shots.length < 2) return;

    var t0 = shots[0].t;
    var span = shots[shots.length - 1].t - t0;
    if (!(span > 0)) return;
    var name = "pk-camera-shots-" + (++pathCounter);
    var css = "@keyframes " + name + " {\n";
    shots.forEach(function (p, i) {
      var pct = Math.min(100, Math.max(0, ((p.t - t0) / span) * 100)).toFixed(3);
      css += "  " + pct + "% { transform: translate(" + p.x + "px, " + p.y + "px) scale(" + p.s + ");";
      if (i < shots.length - 1) {
        css += " animation-timing-function: cubic-bezier(0.45, 0, 0.2, 1);";
      }
      css += " }\n";
    });
    css += "}\n";
    var style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
    el.style.animation = name + " " + span + "s linear " + t0 + "s both";
  }

  function choreographAllCameras() {
    var els = document.querySelectorAll(".pk-camera[data-shots]");
    for (var i = 0; i < els.length; i++) choreographCamera(els[i]);
  }

  window.PanelKit = {
    typeText: typeText,
    choreographCursor: choreographCursor,
    choreographAllCursors: choreographAllCursors,
    choreographCamera: choreographCamera,
    choreographAllCameras: choreographAllCameras
  };
})();
