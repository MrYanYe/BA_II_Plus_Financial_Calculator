/* ─────────────────────────────────────────────────────────────────────────
   panel_focus.js  --  stop a worksheet panel grabbing focus when it opens
   ─────────────────────────────────────────────────────────────────────────
   Pressing 8 then N opens the TVM worksheet and, upstream, immediately focuses
   the N field. On a desktop that is a small convenience: the caret is already
   where you would click next.

   On a phone it breaks the thing you are actually doing. The panel sits below
   the device, so focusing a field scrolls it into view -- animated, because
   styles.css sets `scroll-behavior: smooth` -- and the virtual keyboard covers
   what is left. The calculator you were typing on disappears, and every
   subsequent keypress scrolls back to the focused field. Your eye and your
   thumb end up in different places.

   script.js focuses a panel input from three places, all immediately after
   opening a panel:

       988   the N / I-Y / PV / PMT keys, on the matching field
      1021   the FV key, on FV
      1062   the NPV key, on #cfRate in the cash flow panel

   The register overlay has no inputs, so STO/RCL was never affected.

   The fix shadows `focus` on those inputs. A tap or click on a field is handled
   by the browser's own default action, which does not go through this method, so
   tapping still focuses normally -- the keyboard now opens when the user asks for
   it rather than the moment a panel appears. Nothing reads document.activeElement
   and no selection APIs are used anywhere in the engine, so the only thing lost
   is the automatic caret placement.

   Like ce_c_behavior.js, this is a separate file so script.js stays byte-for-byte
   upstream.
   ───────────────────────────────────────────────────────────────────────── */

(() => {
  "use strict";

  const PANEL_INPUTS = "#tvmPanel input, #cfPanel input, #registerOverlay input";

  for (const el of document.querySelectorAll(PANEL_INPUTS)) {
    el.focus = function () {
      /* Intentionally empty. See the note above: this swallows the three
         programmatic .focus() calls in script.js and nothing else. */
    };
  }
})();
