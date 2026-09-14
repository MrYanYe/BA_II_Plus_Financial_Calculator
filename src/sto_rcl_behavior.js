/* ─────────────────────────────────────────────────────────────────────────
   sto_rcl_behavior.js  --  STO and RCL on the keypad, as on the real device
   ─────────────────────────────────────────────────────────────────────────
   Upstream, the STO and RCL keys open a panel of register buttons and that is the
   only way to reach a register. The real BA II Plus has no such panel: you press
   STO or RCL and then a digit key, which means a recall can be made in the middle
   of a calculation rather than only at the start of one.

       1234  STO  1        stores the displayed value in register 1
       RCL   1             brings it back as the current entry
       23 + RCL 1 =        ...including mid-expression, the case upstream cannot do

   This file implements that. The register panel is left in the markup but is no
   longer opened by these keys, so the widget's structure is untouched and the
   parity check against the live site still holds.

   How it works: script.js declares its state with top-level let/const, which in
   classic scripts share the global lexical scope, so `MEM`, `expression`,
   `currentNum()` and the display helpers are all reachable from here. The keys
   are hooked in the capture phase on #keypad, ahead of the engine's own
   bubble-phase listener, and the press is suppressed only when this file has
   handled it.

   Two things worth knowing if you change this:

   - `pendingOp` in script.js looks like it tracks this state and does not. It is
     assigned by openRegOverlay() and read nowhere; the panel's buttons use a
     closure instead. This file keeps its own `pending` rather than relying on it.

   - Status text is set AFTER updateDisplay(), never before. In standard mode
     updateDisplay() ends with setStatus(indicators, "BA II PLUS"), so anything
     written earlier is immediately overwritten -- the same trap that made the
     engine's own "Cleared" message invisible.
   ───────────────────────────────────────────────────────────────────────── */

(() => {
  "use strict";

  const keypad = document.getElementById("keypad");
  const DIGIT = /^[0-9]$/;

  /* "sto" or "rcl" while a register is being chosen, null otherwise. */
  let pending = null;

  /* Re-render, then label the status bar. Order matters -- see the note above. */
  const setPendingStatus = (label, right) => {
    updateDisplay();
    setStatus(label, right);
  };

  const arm = (mode) => {
    pending = mode;
    setPendingStatus(mode === "sto" ? "STO" : "RCL", "BA II PLUS");
  };

  const disarm = () => {
    if (!pending) return;
    pending = null;
    updateDisplay();
  };

  /* STO n -- keep the value the display is showing, exactly as currentNum()
     resolves it (an evaluated expression, not the raw text). */
  const store = (i) => {
    const value = currentNum();
    MEM[i] = Number.isFinite(value) ? value : 0;
    pending = null;
    setExpr("Stored to register " + i);
    setPendingStatus("", "STO " + i + " ✓");
  };

  /* RCL n -- bring the register back as the current ENTRY, so it can sit in the
     middle of a running calculation. That means dropping whatever entry is being
     typed and appending the recalled number to what came before it:
        "23+"   -> "23+100"
        "23+5"  -> "23+100"     (the part-typed 5 is replaced, as on the device)
        "0"     -> "100"        (nothing pending, so it is just the entry)
     A negative value is wrapped in brackets, which both evaluators accept; the
     chain evaluator would otherwise read "23+-100" as a subtraction of a
     negative literal and the AOS evaluator would need it anyway. */
  const recall = (i) => {
    const value = MEM[i];
    let k = expression.length;
    while (k > 0 && /[\d.]/.test(expression[k - 1])) k--;
    const head = expression.slice(0, k);

    const literal = head !== "" && value < 0 ? "(" + value + ")" : String(value);
    expression = head + literal;
    if (expression === "") expression = "0";

    pending = null;
    setScreen(expression);
    setExpr("Recalled register " + i);
    setPendingStatus("", "RCL " + i);
  };

  /* True when this press belongs to a pending STO/RCL and has been handled. */
  const complete = (digit) => {
    if (pending === "sto") store(digit);
    else recall(digit);
    return true;
  };

  keypad.addEventListener(
    "click",
    (e) => {
      const btn = e.target.closest("button.key");
      if (!btn) return;
      const { action, value: val } = btn.dataset;

      if (pending) {
        // A digit completes whichever of STO / RCL is waiting.
        if (val !== undefined && DIGIT.test(val)) {
          e.stopPropagation();
          e.preventDefault();
          complete(Number(val));
          return;
        }
        // Pressing STO or RCL again abandons it rather than stacking a second one.
        if (action === "sto" || action === "rcl") {
          e.stopPropagation();
          e.preventDefault();
          disarm();
          updateDisplay();
          setStatus("", "BA II PLUS");
          return;
        }
        // Anything else cancels, and then does its own job as normal.
        disarm();
        return;
      }

      // Only the standard calculator has registers to work with; inside a
      // worksheet these keys mean nothing and the engine keeps its behaviour.
      if (currentMode !== "standard") return;
      if (action !== "sto" && action !== "rcl") return;

      e.stopPropagation();
      e.preventDefault();
      consume2nd();
      arm(action);
    },
    true
  );

  /* The keyboard route, so digits typed on a physical keyboard complete the same
     gesture. Without this the on-screen key would wait for a click while the
     digit arrived as normal input, which reads as the key not working. */
  window.addEventListener(
    "keydown",
    (e) => {
      if (e.target.tagName === "INPUT") return;
      if (!pending) return;

      if (DIGIT.test(e.key)) {
        e.stopPropagation();
        e.preventDefault();
        complete(Number(e.key));
        return;
      }
      if (e.key === "Escape") {
        e.stopPropagation();
        e.preventDefault();
        disarm();
        updateDisplay();
        setStatus("", "BA II PLUS");
        return;
      }
      // Any other key cancels and then behaves normally.
      disarm();
    },
    true
  );
})();
