/* ─────────────────────────────────────────────────────────────────────────
   entry_behavior.js  --  a completed operation ends the entry
   ─────────────────────────────────────────────────────────────────────────
   On a real BA II Plus, anything that finishes a calculation also finishes the
   number being entered. Type `1 + 2 =` and the 3.00 on screen is a result:
   pressing `4` next starts a new number and shows 4. The same is true after
   STO, after RCL, and after any of the maths keys.

   The web version keeps the finished value in the entry buffer instead, so `4`
   extends the result to 34 and `82 STO 2` then `23` becomes 8223. This file
   restores the device's behaviour, and also adds the STO/RCL gestures the device
   has and the panel does not:

       1234 STO 1        stores 1234 in register 1     (digit, or click the panel)
       RCL 1             brings it back as the entry   (digit, or click the panel)
       23 + RCL 1 =      recalls into a running calculation
       RCL I/Y           recalls a TVM variable rather than overwriting it

   Both the panel and the keypad route through the same store()/recallRegister()
   here, so they cannot drift apart.

   WHAT COUNTS AS COMPLETED, and why the rule differs from CE|C's:

   - After STO, RCL or a result key, the value on screen is REAL. Pressing a digit
     replaces it (a new number), but pressing an operator must keep it -- it is
     the left operand. `3` then `+` has to give `3+`, not `0+`.
   - After CE|C, the value on screen is a placeholder zero that CE put there. That
     one IS dropped for an operator too, which is why ce_c_behavior.js strips on
     both. Getting this backwards is what made `RCL I/Y` then `+` show `0+`.

   How it reaches the engine: script.js declares its state with top-level
   let/const, which in classic scripts share the global lexical scope, so `MEM`,
   `expression`, `currentNum()`, `tvmVal()`, `openRegOverlay()` and the display
   helpers are all reachable from here. Keys are hooked in the capture phase,
   ahead of the engine's own bubble-phase listeners, and a press is suppressed
   only when this file has handled it.

   Note for anyone tempted to use it: `pendingOp` in script.js looks like it
   tracks the STO/RCL state and does not. It is assigned by openRegOverlay() and
   read nowhere. This file keeps its own `pending`, and mirrors it into `pendingOp`
   only so the engine's own cancel handler stays consistent.
   ───────────────────────────────────────────────────────────────────────── */

(() => {
  "use strict";

  const keypad = document.getElementById("keypad");
  const overlay = document.getElementById("registerOverlay");

  const DIGIT = /^[0-9]$/;
  /* What begins a new entry rather than continuing one. */
  const STARTS_ENTRY = /^[0-9.]$/;
  const OPERATOR = /^[+\-*/]$/;

  /* Keys that leave a finished number on screen. All of them end with
     `expression = String(result)` and `setScreen(fmt(result))`. */
  const RESULT_ACTIONS = new Set([
    "equals", "percent", "sqrt", "square", "reciprocal", "ln",
  ]);

  /* "sto" or "rcl" while a register is being chosen, null otherwise. */
  let pending = null;

  /* True once an operation has finished: the next digit starts a new entry
     instead of extending the value on screen. */
  let fresh = false;

  /* Re-render, then label the status bar. Order matters -- updateDisplay() ends
     with setStatus(indicators, "BA II PLUS"), so anything written first is lost. */
  const setStatusText = (left, right) => {
    updateDisplay();
    setStatus(left, right);
  };

  const closePanel = () => {
    overlay.hidden = true;
    pendingOp = null;   // keep the engine's flag in step with ours
  };

  const arm = (mode) => {
    pending = mode;
    openRegOverlay(mode);            // the engine builds the grid and shows it
    setStatusText(mode === "sto" ? "STO" : "RCL", "BA II PLUS");
  };

  /* Abandon a pending STO/RCL. The caller decides whether the key that caused it
     should still do its own job. */
  const disarm = () => {
    if (!pending) return;
    pending = null;
    closePanel();
    updateDisplay();
  };

  /* Drop the entry being typed, keeping everything before it -- the last
     operator included. Returns the prefix, which is "" when nothing precedes
     the entry. */
  const dropEntry = () => {
    let k = expression.length;
    while (k > 0 && /[\d.]/.test(expression[k - 1])) k--;
    return expression.slice(0, k);
  };

  /* A digit arrived while the entry was finished: make it start a new number.

     The prefix is kept, so `23+` with a recall pending becomes `23+7` and not
     `7`. When nothing precedes it the entry is set to "0", which the engine's
     own appendValue() treats as "replace me" -- giving `7` rather than `07`. */
  const beginNewEntry = () => {
    expression = dropEntry() || "0";
    fresh = false;
  };

  /* An operator arrived while the entry was finished: the value stays, because it
     is the left operand. Just stop treating the entry as finished. */
  const keepEntry = () => {
    fresh = false;
  };

  /* STO n -- keep the value the display resolves to, exactly as currentNum()
     reads it (an evaluated expression, not the raw text). */
  const store = (i) => {
    const value = currentNum();
    MEM[i] = Number.isFinite(value) ? value : 0;
    pending = null;
    closePanel();
    expression = String(MEM[i]);
    setScreen(fmt(MEM[i]));
    setExpr("Stored to register " + i);
    setStatusText("", "STO " + i + " ✓");
    fresh = true;
  };

  /* RCL <value> -- bring it back as the current ENTRY so it can sit in the middle
     of a running calculation. */
  const recallValue = (value, label) => {
    const head = dropEntry();
    const literal = head !== "" && value < 0 ? "(" + value + ")" : String(value);
    expression = head + literal || "0";
    pending = null;
    closePanel();
    /* A standalone recall is a finished value, so it renders formatted and the
       next digit starts fresh. Recalling into a part-built expression is not:
       the entry is live and typing extends it, as typing always does. */
    setScreen(head === "" ? fmt(value) : expression);
    setExpr("Recalled " + label);
    setStatusText("", "RCL " + label);
    fresh = head === "";
  };

  const recallRegister = (i) => recallValue(MEM[i], "register " + i);

  /* The TVM variable a key refers to, or null for any other key. The FV key
     carries data-action="clrTVM" and only clears when 2ND is held. */
  const tvmIdOf = (btn) => {
    if (btn.dataset.action === "tvm") return btn.dataset.target;   // n iy pv pmt
    if (btn.dataset.action === "clrTVM") return "fv";
    return null;
  };

  /* A digit completes whichever of STO / RCL is waiting. */
  const completeWithDigit = (d) => {
    if (pending === "sto") store(d);
    else recallRegister(d);
  };

  /* ── the keypad ─────────────────────────────────────────────────────── */
  keypad.addEventListener(
    "click",
    (e) => {
      const btn = e.target.closest("button.key");
      if (!btn) return;
      const { action, value: val } = btn.dataset;

      if (pending) {
        if (val !== undefined && DIGIT.test(val)) {
          e.stopPropagation();
          e.preventDefault();
          completeWithDigit(Number(val));
          return;
        }
        /* RCL + a TVM key recalls that variable. Left to the engine it would
           STORE the display into it, wiping the value the user asked for. */
        if (pending === "rcl") {
          const id = tvmIdOf(btn);
          if (id) {
            e.stopPropagation();
            e.preventDefault();
            recallValue(tvmVal(id), id.toUpperCase());
            return;
          }
        }
        if (action === "sto" || action === "rcl") {
          e.stopPropagation();
          e.preventDefault();
          disarm();
          setStatusText("", "BA II PLUS");
          return;
        }
        /* Anything else cancels, and then does its own job as normal -- which is
           what makes STO + a TVM key store into that variable, as on the device. */
        disarm();
        return;
      }

      /* The entry is finished. Runs before the engine appends, which is why this
         is the capture phase. */
      if (fresh) {
        if (val !== undefined && STARTS_ENTRY.test(val)) beginNewEntry();
        else keepEntry();
      }

      /* A result key leaves a finished number on screen, so whatever is typed
         next starts a new entry. Armed here, before the engine evaluates. */
      if (RESULT_ACTIONS.has(action)) {
        fresh = true;
        return;
      }
      /* CPT followed by a TVM key solves, which also leaves a result. */
      if (isCpt && tvmIdOf(btn)) {
        fresh = true;
        return;
      }

      /* Only the standard calculator has registers to work with; inside a
         worksheet these keys mean nothing and the engine keeps its behaviour. */
      if (currentMode !== "standard") return;
      if (action !== "sto" && action !== "rcl") return;

      e.stopPropagation();
      e.preventDefault();
      consume2nd();
      arm(action);
    },
    true
  );

  /* ── the register panel ─────────────────────────────────────────────── */
  /* It lives outside #keypad, so it needs its own listener. Both routes go
     through store()/recallRegister() so the panel and the keypad cannot diverge. */
  overlay.addEventListener(
    "click",
    (e) => {
      const regBtn = e.target.closest(".reg-btn");
      if (regBtn && pending) {
        const num = regBtn.querySelector(".reg-btn-num");
        const i = num ? parseInt(num.textContent, 10) : NaN;
        if (Number.isInteger(i)) {
          e.stopPropagation();
          e.preventDefault();
          completeWithDigit(i);
          return;
        }
      }
      if (e.target.closest("#regCancel")) {
        pending = null;
        setStatusText("", "BA II PLUS");   // the engine hides the panel itself
      }
    },
    true
  );

  /* ── the keyboard ───────────────────────────────────────────────────── */
  window.addEventListener(
    "keydown",
    (e) => {
      if (e.target.tagName === "INPUT") return;

      if (!pending) {
        if (fresh) {
          if (STARTS_ENTRY.test(e.key)) beginNewEntry();
          else keepEntry();
        }
        /* Enter and = evaluate, so they leave a result behind them. */
        if (e.key === "Enter" || e.key === "=") fresh = true;
        return;
      }

      if (DIGIT.test(e.key)) {
        e.stopPropagation();
        e.preventDefault();
        completeWithDigit(Number(e.key));
        return;
      }
      if (e.key === "Escape") {
        e.stopPropagation();
        e.preventDefault();
        disarm();
        setStatusText("", "BA II PLUS");
        return;
      }
      disarm();   // any other key cancels and then behaves normally
    },
    true
  );
})();
