/* ─────────────────────────────────────────────────────────────────────────
   ce_c_behavior.js  --  make CE|C two-stage, like the real BA II Plus
   ─────────────────────────────────────────────────────────────────────────
   On the physical BA II Plus the bottom-left key is two functions on one key:

       press once   CE   clear the current ENTRY, keep any pending operation
       press again  C    clear EVERYTHING, pending operation included

   The web version upstream always does the full clear -- a single press with
   "12+" on the display discards the "+", where the real device keeps it. This
   file restores the two-stage behaviour.

   It is a separate file on purpose. script.js stays byte-for-byte upstream, so
   an upstream re-sync stays a clean diff and the calculator engine is never
   touched by hand. This is the only file in the project that changes how the
   calculator behaves rather than how it looks.

   How it can reach the engine at all: script.js declares its state with
   top-level `let`/`const` (expression, currentMode, is2nd, ...). In classic
   scripts those live in the shared global lexical environment rather than on
   window, so any script loaded afterwards can read and write them. Verified, not
   assumed: an appended script can both read `expression` and assign to it.

   Where it applies: standard calculation only. The worksheet modes (BGN, P/Y,
   FORMAT, AMORT) already clear the field you are typing into on a single press,
   which is the CE half of the behaviour already; their second press exits the
   worksheet. Overriding those would break the navigation, so they are left
   alone.

   Both input routes are covered -- the on-screen key and the keyboard shortcut
   -- because otherwise they would disagree, and pressing `c` after clicking
   CE|C would wipe everything unexpectedly.
   ───────────────────────────────────────────────────────────────────────── */

(() => {
  "use strict";

  const keypad = document.getElementById("keypad");

  /* True between the first and second press. Any other input clears it, which is
     what makes the pair "consecutive": press CE, then a digit, then CE again and
     you get CE twice, not a C. */
  let armed = false;

  /* True after a CE, until the user types the next entry.

     CE leaves an explicit 0 on the display, and typing over it has to REPLACE
     that 0 rather than append to it. Without this, "12+5" → CE → "12+0", then
     pressing 4 gives "12+04" rather than "12+4". That is not just cosmetic: in
     AOS the engine evaluates with Function('"use strict"; return (...)'), and in
     strict mode 04 is a legacy octal literal, so it is a SyntaxError and the
     display shows "Error". */
  let freshEntry = false;

  /* CE: drop the entry the user is currently typing, keep what came before it.

     `expression` holds the whole expression, not just the entry, so the trailing
     run of digits and dots is replaced with a fresh 0 and everything up to and
     including the last operator survives:

         "12+5"  -> "12+0"      "12+"   -> "12+0"
         "5"     -> "0"         "2**"   -> "2**0"
         "12+5*3"-> "12+5*0"    "-5"    -> "0"

     A bare "-" is treated as no operator, so a lone minus collapses to 0 rather
     than becoming "-0". */
  const clearEntry = () => {
    let i = expression.length;
    while (i > 0 && /[\d.]/.test(expression[i - 1])) i--;
    const head = expression.slice(0, i);

    if (head && head !== "-" && /[+\-*/]$/.test(head)) {
      // Something is still pending, so keep it and zero just the entry:
      // "12+5" -> "12+0", "2+3*4" -> "2+3*0".
      expression = head + "0";
      setScreen(expression);
    } else {
      // Nothing was pending, so this leaves exactly the state a full clear
      // would -- and it has to be RENDERED the same way, too.
      //
      // The engine has two conventions: setScreen(expression) shows a raw
      // expression while the user is typing, setScreen(fmt(n)) shows a formatted
      // number otherwise. Taking the raw path here would render "0" where a full
      // clear renders "0.00", so pressing CE|C on an already-clear calculator
      // would flip the readout between "0.00" and "0" on every press. Both states
      // are empty; only their spelling differed. fmt(0) keeps them identical, so
      // the display simply does not change -- which is what the real device does.
      expression = "0";
      setScreen(fmt(0));
    }

    setExpr("");
    updateDisplay();
    freshEntry = true;
  };

  const DIGIT = /^[0-9]$/;
  const OPERATOR = /^[+\-*/]$/;

  /* Drop the placeholder 0 that CE left, so the next keystroke starts a clean
     entry. Called just before the engine's own handler runs, so the engine's
     appendValue() sees "12+" and produces "12+7".

     A decimal point is deliberately excluded: after CE, "." should extend the 0
     into "0.", not start from nothing. */
  const startFreshEntry = () => {
    expression = expression.replace(/0$/, "") || "0";
    freshEntry = false;
  };

  /* Decide whether this press is CE, C, or none of our business.

     Returns true when the press has been handled as CE and the engine's own
     handler must be suppressed. Returning false lets the press through, so the
     engine performs its full clear -- which is exactly the C half. */
  const intercept = () => {
    // 2ND + CE|C is CLR WORK, a different function. Never interfere.
    if (is2nd) {
      armed = false;
      return false;
    }

    // Worksheet modes handle their own clearing and navigation.
    if (currentMode !== "standard") {
      armed = false;
      return false;
    }

    if (armed) {
      armed = false;
      return false; // second consecutive press -> engine does the full clear
    }

    clearEntry();
    armed = true;
    return true;
  };

  /* On-screen key.

     Capture phase on #keypad: the engine listens on #keypad in the bubble
     phase, and a capture listener on the same element runs first, so
     stopPropagation() here keeps the engine's handler from seeing the click. */
  keypad.addEventListener(
    "click",
    (e) => {
      const btn = e.target.closest("button.key");
      if (!btn) return;

      if (btn.dataset.action !== "clearAll") {
        armed = false; // any other key breaks the pair

        // Type-over: replace the 0 CE left instead of appending to it. Must run
        // before the engine's handler, which is why this is the capture phase.
        if (freshEntry) {
          const v = btn.dataset.value;
          if (v !== undefined && (DIGIT.test(v) || OPERATOR.test(v))) {
            startFreshEntry();
          } else if (v === undefined) {
            freshEntry = false; // any non-entry key ends the fresh entry
          }
        }
        return;
      }

      if (intercept()) {
        e.stopPropagation();
        e.preventDefault();
      }
    },
    true
  );

  /* Keyboard shortcut (Escape or C), which the engine wires up separately from
     the keypad. Without this the on-screen key would be two-stage while the
     keyboard stayed one-stage.

     Capture phase on window for the same reason as above: the engine registered
     its own window keydown listener at load time, so a bubble-phase listener
     here would run second. */
  window.addEventListener(
    "keydown",
    (e) => {
      if (e.target.tagName === "INPUT") return; // worksheet inputs are the engine's

      if (e.key !== "Escape" && e.key.toLowerCase() !== "c") {
        armed = false;
        // Same type-over rule for the keyboard route.
        if (freshEntry && (DIGIT.test(e.key) || OPERATOR.test(e.key))) {
          startFreshEntry();
        } else if (e.key.startsWith("Arrow") || e.key === "Enter" || e.key === "=") {
          freshEntry = false;
        }
        return;
      }

      // Outside standard mode these keys navigate worksheets; leave them be.
      if (currentMode !== "standard") {
        armed = false;
        return;
      }
      if (intercept()) {
        e.stopPropagation();
        e.preventDefault();
      }
    },
    true
  );
})();
