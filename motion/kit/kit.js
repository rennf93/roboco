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

  window.PanelKit = { typeText: typeText };
})();
