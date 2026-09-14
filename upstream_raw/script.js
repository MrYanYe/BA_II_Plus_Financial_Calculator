/* ── State ────────────────────────────────────────────── */
const MEM = Array(10).fill(0);          // STO/RCL registers 0-9
let expression   = "0";                  // current arithmetic expression string
let lastResult   = 0;                    // ANS
let pendingOp    = null;                 // 'sto' | 'rcl' | null
let is2nd        = false;                // 2ND key toggle
let isCpt        = false;                // CPT key toggle for TVM
let isBgn        = false;                // BGN mode toggle (true = BGN, false = END)
let cfEntries    = [{ cf: 0, freq: 1 }]; // CF worksheet entries

// Amortization Worksheet State
let currentMode      = "standard";           // "standard" | "amort" | "bgn" | "py"
let amortP1          = 1;
let amortP2          = 1;
let amortActiveIndex = 0;                    // 0: P1, 1: P2, 2: BAL, 3: PRN, 4: INT
const amortVariables = ["P1", "P2", "BAL", "PRN", "INT"];
let amortInputBuffer = "";

// P/Y and C/Y Settings State
let pyVal            = 1;                    // Payments per year (default = 1)
let cyVal            = 1;                    // Compounding periods per year (default = 1)
let pyActiveIndex    = 0;                    // 0: P/Y, 1: C/Y
let pyInputBuffer    = "";

// Format Settings State
let decVal           = 2;                    // Decimal places (default = 2, 0-8: fixed, 9: floating)
let formatInputBuffer = "";
let formatActiveIndex = 0;                    // 0: DEC, 1: DEG/RAD, 2: US/EUR, 3: Chn/AOS
let calcMode         = "Chn";                // "Chn" (Chain, default) | "AOS" (Algebraic Operating System)
let angleMode        = "DEG";                // "DEG" | "RAD"
let dateMode         = "US";                 // "US" | "EUR"

/* ── DOM Refs ─────────────────────────────────────────── */
const screenEl    = document.getElementById("screen");
const exprEl      = document.getElementById("displayExpr");
const statusLeft  = document.getElementById("statusLeft");
const statusRight = document.getElementById("statusRight");
const btn2nd      = document.getElementById("btn2nd");

const tvmPanel    = document.getElementById("tvmPanel");
const cfPanel     = document.getElementById("cfPanel");
const regOverlay  = document.getElementById("registerOverlay");
const regGrid     = document.getElementById("regGrid");
const regTitle    = document.getElementById("regTitle");

const tvmInputs = {
  n:   document.getElementById("tvmN"),
  iy:  document.getElementById("tvmIY"),
  pv:  document.getElementById("tvmPV"),
  pmt: document.getElementById("tvmPMT"),
  fv:  document.getElementById("tvmFV"),
  py:  document.getElementById("tvmPY"),
  cy:  document.getElementById("tvmCY"),
};

/* ── Display helpers ─────────────────────────────────── */
const fmt = (v) => {
  if (!Number.isFinite(v)) return "Error";
  if (Math.abs(v) >= 1e12 || (v !== 0 && Math.abs(v) < 1e-7))
    return v.toExponential(6);

  let s;
  if (decVal === 9) {
    s = v.toFixed(9).replace(/\.?0+$/, "");
  } else {
    s = v.toFixed(decVal);
  }

  const parts = s.split(".");
  parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  let res = parts.join(".");

  const zeroTarget = "0" + (decVal > 0 && decVal !== 9 ? "." + "0".repeat(decVal) : "");
  if (res === "-0" || res === "-" + zeroTarget) {
    res = zeroTarget;
  }
  return res;
};

const setScreen = (val) => { screenEl.textContent = val; };
const setExpr   = (val) => { exprEl.textContent = val; };
const setStatus = (left = "", right = "") => {
  let parts = left.split(" ").filter(p => p !== "");
  if (isBgn && !parts.includes("BGN")) {
    parts.unshift("BGN");
  }
  if (calcMode === "AOS" && !parts.includes("AOS")) {
    parts.unshift("AOS");
  }
  if (is2nd && !parts.includes("2ND")) {
    // Keep 2ND at the very front
    parts.unshift("2ND");
  }
  statusLeft.textContent  = parts.join(" ");
  statusRight.textContent = right;
};

const showError = (msg) => {
  setScreen("Error");
  setExpr(msg);
  setStatus("ERR");
  expression = "0";
};

/* ── 2ND key ────────────────────────────────────────── */
const toggle2nd = () => {
  is2nd = !is2nd;
  btn2nd.classList.toggle("active", is2nd);
  updateDisplay();
};

const consume2nd = () => {
  if (!is2nd) return false;
  is2nd = false;
  btn2nd.classList.remove("active");
  updateDisplay();
  return true;
};

/* ── Chain Mode Arithmetic Evaluator ─────────────────── */
const tokenizeFlat = (str) => {
  const tokens = [];
  let i = 0;
  const s = str.replace(/\s+/g, "");

  while (i < s.length) {
    const isUnaryMinus = s[i] === "-" && (tokens.length === 0 || typeof tokens[tokens.length - 1] === "string");

    if (isUnaryMinus || /^[0-9.]/.test(s[i])) {
      let numStr = "";
      if (isUnaryMinus) {
        numStr += "-";
        i++;
      }
      while (i < s.length && /^[0-9.eE]/.test(s[i])) {
        if ((s[i] === "e" || s[i] === "E") && (s[i + 1] === "+" || s[i + 1] === "-")) {
          numStr += s[i] + s[i + 1];
          i += 2;
        } else {
          numStr += s[i];
          i++;
        }
      }
      const val = parseFloat(numStr);
      if (!Number.isFinite(val)) throw new Error("Invalid number: " + numStr);
      tokens.push(val);
    } else if (s.slice(i, i + 2) === "**") {
      tokens.push("**");
      i += 2;
    } else if (["+", "-", "*", "/"].includes(s[i])) {
      tokens.push(s[i]);
      i++;
    } else {
      throw new Error("Unexpected character: " + s[i]);
    }
  }
  return tokens;
};

const evaluateChainFlat = (tokens) => {
  if (tokens.length === 0) return 0;
  let acc = tokens[0];
  for (let i = 1; i < tokens.length; i += 2) {
    const op = tokens[i];
    const nextVal = tokens[i + 1];
    if (nextVal === undefined || typeof nextVal !== "number") break;
    if (op === "+") acc = acc + nextVal;
    else if (op === "-") acc = acc - nextVal;
    else if (op === "*") acc = acc * nextVal;
    else if (op === "/") {
      if (nextVal === 0) throw new Error("Divide by 0");
      acc = acc / nextVal;
    } else if (op === "**") acc = Math.pow(acc, nextVal);
  }
  return acc;
};

const evaluateChain = (expr) => {
  let s = String(expr).trim();
  if (!s) return 0;
  s = s.replace(/[+\-*/]+$/, "");
  if (!s) return 0;

  const parenRegex = /\(([^()]+)\)/;
  let match;
  let safetyLimit = 50;
  while ((match = parenRegex.exec(s)) && safetyLimit-- > 0) {
    const innerVal = evaluateChain(match[1]);
    s = s.slice(0, match.index) + String(innerVal) + s.slice(match.index + match[0].length);
  }

  const tokens = tokenizeFlat(s);
  return evaluateChainFlat(tokens);
};

/* ── Basic expression builder ───────────────────────── */
const appendValue = (v) => {
  pendingOp = null;

  // In Chn mode, when user enters a binary operator, collapse preceding chain if complete
  if (calcMode === "Chn" && ["+", "-", "*", "/", "**"].includes(v)) {
    const openParens = (expression.match(/\(/g) || []).length;
    const closeParens = (expression.match(/\)/g) || []).length;

    if (/[+\-*/]$/.test(expression) && expression !== "-") {
      expression = expression.replace(/[+\-*/]+$/, v);
      setScreen(expression);
      setExpr("");
      return;
    }

    if (openParens === closeParens && /[0-9.)]$/.test(expression)) {
      try {
        const intermediate = evaluateChain(expression);
        if (Number.isFinite(intermediate)) {
          expression = String(intermediate) + v;
          setScreen(expression);
          setExpr("");
          return;
        }
      } catch {
        // fallback
      }
    }
  }

  // Replace duplicate trailing operators in any mode
  if (["+", "*", "/"].includes(v) && /[+\-*/]$/.test(expression) && expression !== "-") {
    expression = expression.replace(/[+\-*/]+$/, v);
    setScreen(expression);
    setExpr("");
    return;
  }

  if (expression === "0" && v !== ".") expression = v;
  else expression += v;
  setScreen(expression);
  setExpr("");
};

const safeEval = () => {
  try {
    let result;
    if (calcMode === "Chn") {
      result = evaluateChain(expression);
    } else {
      // eslint-disable-next-line no-new-func
      result = Function('"use strict"; return (' + expression + ")")();
    }
    if (!Number.isFinite(result)) throw new Error("Bad expression");
    lastResult = result;
    expression = String(result);
    setScreen(fmt(result));
    setExpr("");
    setStatus("");
  } catch {
    showError("Bad expression");
  }
};

/* ── STO / RCL ───────────────────────────────────────── */
const buildRegGrid = (mode) => {
  regGrid.innerHTML = "";
  MEM.forEach((val, i) => {
    const btn = document.createElement("button");
    btn.className = "reg-btn";
    btn.innerHTML = `<span class="reg-btn-num">${i}</span><span class="reg-btn-val">${fmt(val)}</span>`;
    btn.addEventListener("click", () => {
      if (mode === "sto") {
        const num = parseFloat(expression);
        MEM[i] = Number.isFinite(num) ? num : 0;
        setStatus(``, `STO ${i} ✓`);
        setExpr(`Stored to register ${i}`);
      } else {
        expression = String(MEM[i]);
        setScreen(fmt(MEM[i]));
        setExpr(`Recalled register ${i}`);
        setStatus("", `RCL ${i}`);
      }
      regOverlay.hidden = true;
      pendingOp = null;
    });
    regGrid.appendChild(btn);
  });
};

const openRegOverlay = (mode) => {
  pendingOp = mode;
  regTitle.textContent = mode === "sto"
    ? "STO — select register (0–9)"
    : "RCL — select register (0–9)";
  buildRegGrid(mode);
  regOverlay.hidden = false;
};

document.getElementById("regCancel").addEventListener("click", () => {
  regOverlay.hidden = true;
  pendingOp = null;
});

/* ── TVM solver ──────────────────────────────────────── */
const tvmVal = (id) => {
  const raw = tvmInputs[id].value.trim().replace(/,/g, "");
  if (raw === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
};

const bisect = (fn, lo, hi, itr = 150, tol = 1e-9) => {
  let fLo = fn(lo), fHi = fn(hi);
  if (fLo === 0) return lo;
  if (fHi === 0) return hi;
  for (let e = 0; e < 15 && fLo * fHi > 0; e++) {
    lo = Math.max(-0.9999, lo - 1);
    hi += 1;
    fLo = fn(lo); fHi = fn(hi);
  }
  if (fLo * fHi > 0) return null;
  for (let i = 0; i < itr; i++) {
    const mid = (lo + hi) / 2;
    const fMid = fn(mid);
    if (Math.abs(fMid) < tol) return mid;
    fLo * fMid < 0 ? (hi = mid, fHi = fMid) : (lo = mid, fLo = fMid);
  }
  return (lo + hi) / 2;
};

/* ── P/Y & C/Y Rate Conversion Helpers ───────────────── */
const getRatePerPeriod = (iy) => {
  const nominalRate = iy / 100;
  if (Math.abs(pyVal - cyVal) < 1e-12) {
    return nominalRate / pyVal;
  } else {
    return Math.pow(1 + nominalRate / cyVal, cyVal / pyVal) - 1;
  }
};

const getIyFromRate = (r) => {
  if (Math.abs(pyVal - cyVal) < 1e-12) {
    return r * pyVal * 100;
  } else {
    return cyVal * 100 * (Math.pow(1 + r, pyVal / cyVal) - 1);
  }
};

/* ── Amortization worksheet calculations & UI updates ── */
const calculateAmort = () => {
  const parseVal = (str) => {
    if (!str) return 0;
    return parseFloat(str.replace(/,/g, "")) || 0;
  };

  const pvVal = parseVal(tvmInputs.pv.value);
  let pmtVal = parseVal(tvmInputs.pmt.value);
  const iyVal = parseVal(tvmInputs.iy.value);
  const nVal = parseVal(tvmInputs.n.value);
  const fvVal = parseVal(tvmInputs.fv.value);

  const r = getRatePerPeriod(iyVal);
  const p1 = Math.max(1, Math.round(amortP1));
  const p2 = Math.max(p1, Math.round(amortP2));

  // Use unrounded exact PMT if pmtVal aligns with TVM inputs
  if (nVal > 0 && Math.abs(pvVal) > 0 && Math.abs(r) >= 0) {
    const pmtExact = Math.abs(r) < 1e-12
      ? -(pvVal + fvVal) / nVal
      : -((pvVal * Math.pow(1 + r, nVal) + fvVal) * r) / ((Math.pow(1 + r, nVal) - 1) * (isBgn ? (1 + r) : 1));
    if (Number.isFinite(pmtExact) && (pmtVal === 0 || Math.abs(pmtVal - Math.round(pmtExact * 100) / 100) < 0.05)) {
      pmtVal = pmtExact;
    }
  }

  let balance = pvVal;
  let accumulatedPrn = 0;
  let accumulatedInt = 0;

  for (let t = 1; t <= p2; t++) {
    let interest, principal;
    if (isBgn) {
      interest = - ((balance + pmtVal) * r);
      principal = pmtVal - interest;
      balance = balance + pmtVal + interest;
    } else {
      interest = - (balance * r);
      principal = pmtVal - interest;
      balance = balance + principal;
    }

    if (t >= p1) {
      accumulatedPrn += principal;
      accumulatedInt += interest;
    }
  }

  const round2 = (val) => Math.round(val * 100) / 100;

  return {
    bal: round2(balance),
    prn: round2(accumulatedPrn),
    int: round2(accumulatedInt)
  };
};

const updateDisplay = () => {
  let sLeftParts = [];
  if (is2nd) sLeftParts.push("2ND");
  if (isBgn) sLeftParts.push("BGN");
  if (calcMode === "AOS") sLeftParts.push("AOS");

  // Keep worksheet P/Y and C/Y inputs in sync
  if (tvmInputs.py) tvmInputs.py.value = fmt(pyVal);
  if (tvmInputs.cy) tvmInputs.cy.value = fmt(cyVal);

  if (currentMode === "amort") {
    sLeftParts.push("AMORT");
    setStatus(sLeftParts.join(" "), "");

    const varName = amortVariables[amortActiveIndex];
    setExpr(varName + " =");

    if (varName === "P1") {
      setScreen(amortInputBuffer !== "" ? amortInputBuffer : amortP1.toFixed(2));
    } else if (varName === "P2") {
      setScreen(amortInputBuffer !== "" ? amortInputBuffer : amortP2.toFixed(2));
    } else {
      const res = calculateAmort();
      if (varName === "BAL") setScreen(fmt(res.bal));
      else if (varName === "PRN") setScreen(fmt(res.prn));
      else if (varName === "INT") setScreen(fmt(res.int));
    }
  } else if (currentMode === "bgn") {
    setStatus(sLeftParts.join(" "), "");
    setExpr("SETTING");
    setScreen(isBgn ? "BGN" : "END");
  } else if (currentMode === "py") {
    setStatus(sLeftParts.join(" "), "");
    const varName = pyActiveIndex === 0 ? "P/Y" : "C/Y";
    setExpr(varName + " =");
    const currentVal = pyActiveIndex === 0 ? pyVal : cyVal;
    setScreen(pyInputBuffer !== "" ? pyInputBuffer : currentVal.toFixed(2));
  } else if (currentMode === "format") {
    setStatus(sLeftParts.join(" "), "FORMAT");
    if (formatActiveIndex === 0) {
      setExpr("DEC =");
      setScreen(formatInputBuffer !== "" ? formatInputBuffer : decVal.toString());
    } else if (formatActiveIndex === 1) {
      setExpr("DEG / RAD =");
      setScreen(angleMode);
    } else if (formatActiveIndex === 2) {
      setExpr("FORMAT =");
      setScreen(dateMode);
    } else if (formatActiveIndex === 3) {
      setExpr("CALC METHOD =");
      setScreen(calcMode);
    }
  } else {
    setStatus(sLeftParts.join(" "), "BA II PLUS");
  }
};

const tvmEq = (n, r, pv, pmt, fv) => {
  if (Math.abs(r) < 1e-12) return pv + pmt * n + fv;
  const g = Math.pow(1 + r, n);
  const pmtTerm = isBgn ? pmt * (1 + r) : pmt;
  return pv * g + pmtTerm * ((g - 1) / r) + fv;
};

const solveTVM = (target) => {
  const vals = {
    n:   tvmVal("n"),
    iy:  tvmVal("iy"),
    pv:  tvmVal("pv"),
    pmt: tvmVal("pmt"),
    fv:  tvmVal("fv"),
  };
  const missing = Object.keys(vals).filter((k) => vals[k] === null);
  if (missing.length > 1 || (missing.length === 1 && missing[0] !== target)) {
    showError("Fill 4 TVM fields first");
    return;
  }

  try {
    let result;
    const { n, iy, pv, pmt, fv } = vals;
    const r = iy !== null ? getRatePerPeriod(iy) : 0;
    const pmtTerm = isBgn ? pmt * (1 + r) : pmt;

    if (target === "fv") {
      result = Math.abs(r) < 1e-12
        ? -(pv + pmtTerm * n)
        : -(pv * Math.pow(1 + r, n) + pmtTerm * ((Math.pow(1 + r, n) - 1) / r));
      tvmInputs.fv.value = fmt(result);
    } else if (target === "pv") {
      result = Math.abs(r) < 1e-12
        ? -(pmtTerm * n + fv)
        : -((pmtTerm * ((Math.pow(1 + r, n) - 1) / r) + fv) / Math.pow(1 + r, n));
      tvmInputs.pv.value = fmt(result);
    } else if (target === "pmt") {
      result = Math.abs(r) < 1e-12
        ? -(pv + fv) / n
        : -((pv * Math.pow(1 + r, n) + fv) * r) / ((Math.pow(1 + r, n) - 1) * (isBgn ? (1 + r) : 1));
      tvmInputs.pmt.value = fmt(result);
    } else if (target === "n") {
      result = bisect((periods) => tvmEq(periods, r, pv, pmt, fv), 1e-9, 1000);
      if (result === null || !Number.isFinite(result)) throw new Error("no solution");
      tvmInputs.n.value = fmt(result);
    } else if (target === "iy") {
      const rSol = bisect((rate) => tvmEq(n, rate, pv, pmt, fv), -0.9999, 100);
      if (rSol === null || !Number.isFinite(rSol)) throw new Error("no solution");
      result = getIyFromRate(rSol);
      tvmInputs.iy.value = fmt(result);
    }

    setScreen(fmt(result));
    setExpr(target.toUpperCase() + " =");
    setStatus("TVM", `CPT ${target.toUpperCase()} ✓`);
    expression = "0";
  } catch {
    showError("TVM: no solution — check signs");
  }
};

/* ── TVM worksheet buttons ───────────────────────────── */
document.querySelectorAll("[data-tvm]").forEach((btn) => {
  btn.addEventListener("click", () => solveTVM(btn.dataset.tvm));
});

document.getElementById("tvmClose").addEventListener("click", () => {
  tvmPanel.hidden = true;
  setStatus("", "");
});

// Sync worksheet inputs to variables
if (tvmInputs.py) {
  tvmInputs.py.addEventListener("input", () => {
    const v = parseFloat(tvmInputs.py.value.replace(/,/g, ""));
    if (Number.isFinite(v) && v > 0) pyVal = v;
  });
}
if (tvmInputs.cy) {
  tvmInputs.cy.addEventListener("input", () => {
    const v = parseFloat(tvmInputs.cy.value.replace(/,/g, ""));
    if (Number.isFinite(v) && v > 0) cyVal = v;
  });
}

/* ── CF worksheet ────────────────────────────────────── */
const renderCFList = () => {
  const list = document.getElementById("cfList");
  list.innerHTML = "";
  cfEntries.forEach((entry, i) => {
    const row = document.createElement("div");
    row.className = "cf-row";

    const label = document.createElement("span");
    label.className = "cf-row-label";
    label.textContent = i === 0 ? "CF0" : `CF${i}`;

    const cfInput = document.createElement("input");
    cfInput.type = "number";
    cfInput.inputMode = "decimal";
    cfInput.placeholder = i === 0 ? "e.g. -1000" : "0";
    cfInput.value = entry.cf || "";
    cfInput.setAttribute("aria-label", `Cash flow ${i}`);
    cfInput.addEventListener("input", () => { cfEntries[i].cf = Number(cfInput.value) || 0; });

    const freqWrap = document.createElement("div");
    freqWrap.className = "cf-row-freq";
    if (i > 0) {
      const freqLabel = document.createElement("label");
      freqLabel.textContent = "×";
      freqLabel.style.color = "#607080";
      const freqInput = document.createElement("input");
      freqInput.type = "number";
      freqInput.inputMode = "decimal";
      freqInput.placeholder = "1";
      freqInput.min = "1";
      freqInput.value = entry.freq || 1;
      freqInput.setAttribute("aria-label", `Frequency for CF ${i}`);
      freqInput.addEventListener("input", () => { cfEntries[i].freq = Math.max(1, parseInt(freqInput.value) || 1); });
      freqWrap.appendChild(freqLabel);
      freqWrap.appendChild(freqInput);
    }

    if (i > 0) {
      const removeBtn = document.createElement("button");
      removeBtn.className = "cf-remove";
      removeBtn.textContent = "✕";
      removeBtn.setAttribute("aria-label", `Remove CF ${i}`);
      removeBtn.addEventListener("click", () => {
        cfEntries.splice(i, 1);
        renderCFList();
      });
      row.append(label, cfInput, freqWrap, removeBtn);
    } else {
      row.append(label, cfInput, freqWrap);
    }
    list.appendChild(row);
  });
};

document.getElementById("btnAddCF").addEventListener("click", () => {
  cfEntries.push({ cf: 0, freq: 1 });
  renderCFList();
});

document.getElementById("cfClose").addEventListener("click", () => {
  cfPanel.hidden = true;
  setStatus("", "");
});

/* ── NPV calculation ─────────────────────────────────── */
const calcNPV = (rate, entries) => {
  let npv = entries[0].cf;
  let t = 0;
  for (let i = 1; i < entries.length; i++) {
    const freq = entries[i].freq || 1;
    for (let j = 0; j < freq; j++) {
      t++;
      npv += entries[i].cf / Math.pow(1 + rate, t);
    }
  }
  return npv;
};

document.getElementById("btnCptNPV").addEventListener("click", () => {
  const rateRaw = document.getElementById("cfRate").value.trim();
  if (!rateRaw) { showError("Enter I/Y % first"); return; }
  const rate = Number(rateRaw) / 100;
  if (!Number.isFinite(rate)) { showError("Invalid I/Y"); return; }
  try {
    const npv = calcNPV(rate, cfEntries);
    lastResult = npv;
    expression = String(npv);
    setScreen(fmt(npv));
    setExpr("NPV =");
    setStatus("CF", "NPV ✓");
  } catch { showError("NPV calculation failed"); }
});

document.getElementById("btnCptIRR").addEventListener("click", () => {
  try {
    const fn = (r) => calcNPV(r, cfEntries);
    const irr = bisect(fn, -0.9999, 10);
    if (irr === null || !Number.isFinite(irr)) throw new Error("no IRR");
    const irrPct = irr * 100;
    lastResult = irrPct;
    expression = String(irrPct);
    setScreen(fmt(irrPct));
    setExpr("IRR (%) =");
    setStatus("CF", "IRR ✓");
  } catch { showError("No IRR found — check CF signs"); }
});

/* ── Panel toggling helpers ─────────────────────────── */
const openTVM = () => {
  cfPanel.hidden = true;
  regOverlay.hidden = true;
  tvmPanel.hidden = false;
  setStatus("TVM", "");
};

const openCF = () => {
  tvmPanel.hidden = true;
  regOverlay.hidden = true;
  cfPanel.hidden = false;
  renderCFList();
  setStatus("CF", "");
};

/* ── Math helpers ────────────────────────────────────── */
const currentNum = () => {
  try {
    if (calcMode === "Chn") {
      return evaluateChain(expression);
    }
    // eslint-disable-next-line no-new-func
    return Function('"use strict"; return (' + expression + ")")();
  } catch { return parseFloat(expression) || 0; }
};

const applyMath = (fn) => {
  try {
    const x = currentNum();
    const result = fn(x);
    if (!Number.isFinite(result)) throw new Error("domain error");
    lastResult = result;
    expression = String(result);
    setScreen(fmt(result));
    setExpr("");
  } catch (e) { showError(e.message || "Math error"); }
};

/* ── Keypad click handler ────────────────────────────── */
document.getElementById("keypad").addEventListener("click", (e) => {
  const btn = e.target.closest("button.key");
  if (!btn) return;

  const val    = btn.dataset.value;
  const action = btn.dataset.action;

  // Intercept keys when in BGN mode
  if (currentMode === "bgn") {
    if (action === "2nd") {
      toggle2nd();
      return;
    }
    if (action === "enter" || action === "assign" || action === "equals") {
      consume2nd();
      isBgn = !isBgn;
      updateDisplay();
      return;
    }
    if (action === "clearAll") {
      consume2nd();
      currentMode = "standard";
      expression = "0";
      setScreen(fmt(0));
      setExpr("");
      updateDisplay();
      return;
    }
    if (action === "cpt" && is2nd) {
      consume2nd();
      currentMode = "standard";
      expression = "0";
      setScreen(fmt(0));
      setExpr("");
      updateDisplay();
      return;
    }
    // For any other key, automatically exit BGN mode to standard, then proceed
    consume2nd();
    currentMode = "standard";
    expression = "0";
    setScreen(fmt(0));
    setExpr("");
    updateDisplay();
  }

  // Intercept keys when in P/Y & C/Y mode
  if (currentMode === "py") {
    if (action === "2nd") {
      toggle2nd();
      return;
    }
    if (action === "enter" || action === "assign" || action === "equals") {
      consume2nd();
      if (pyInputBuffer !== "") {
        const v = parseFloat(pyInputBuffer);
        if (Number.isFinite(v) && v > 0) {
          if (pyActiveIndex === 0) {
            pyVal = v;
            cyVal = v; // C/Y follows P/Y
          } else {
            cyVal = v;
          }
        }
        pyInputBuffer = "";
      }
      updateDisplay();
      return;
    }
    if (action === "clearAll") {
      consume2nd();
      if (pyInputBuffer !== "") {
        pyInputBuffer = "";
        updateDisplay();
      } else {
        currentMode = "standard";
        setScreen(fmt(currentNum()));
        setExpr("");
        updateDisplay();
      }
      return;
    }
    if (action === "cpt" && is2nd) {
      consume2nd();
      currentMode = "standard";
      setScreen(fmt(currentNum()));
      setExpr("");
      updateDisplay();
      return;
    }
    if (action === "arrowUp" || action === "arrowDn") {
      consume2nd();
      pyActiveIndex = (pyActiveIndex + 1) % 2;
      pyInputBuffer = "";
      updateDisplay();
      return;
    }
    // For any other key, if it's not a digit (digit is handled by intercept digits/dot below),
    // we exit py mode to standard, then let the key be processed.
    if (val === undefined || !/^[0-9.]$/.test(val)) {
      consume2nd();
      currentMode = "standard";
      setScreen(fmt(currentNum()));
      setExpr("");
      updateDisplay();
      // DO NOT return, so it falls through to execute the key!
    }
  }

  // Intercept keys when in FORMAT mode
  if (currentMode === "format") {
    if (action === "2nd") {
      toggle2nd();
      return;
    }
    if (action === "arrowUp") {
      consume2nd();
      formatActiveIndex = (formatActiveIndex + 3) % 4;
      formatInputBuffer = "";
      updateDisplay();
      return;
    }
    if (action === "arrowDn") {
      consume2nd();
      formatActiveIndex = (formatActiveIndex + 1) % 4;
      formatInputBuffer = "";
      updateDisplay();
      return;
    }
    if (action === "enter" || action === "assign" || action === "equals") {
      consume2nd();
      if (formatActiveIndex === 0) {
        if (formatInputBuffer !== "") {
          const v = parseInt(formatInputBuffer, 10);
          if (Number.isFinite(v) && v >= 0 && v <= 9) {
            decVal = v;
          }
          formatInputBuffer = "";
        }
      } else if (formatActiveIndex === 1) {
        angleMode = (angleMode === "DEG" ? "RAD" : "DEG");
      } else if (formatActiveIndex === 2) {
        dateMode = (dateMode === "US" ? "EUR" : "US");
      } else if (formatActiveIndex === 3) {
        calcMode = (calcMode === "Chn" ? "AOS" : "Chn");
      }
      updateDisplay();
      return;
    }
    if (action === "clearAll") {
      consume2nd();
      if (formatInputBuffer !== "") {
        formatInputBuffer = "";
        updateDisplay();
      } else {
        currentMode = "standard";
        setScreen(fmt(currentNum()));
        setExpr("");
        updateDisplay();
      }
      return;
    }
    if (action === "cpt" && is2nd) {
      consume2nd();
      currentMode = "standard";
      setScreen(fmt(currentNum()));
      setExpr("");
      updateDisplay();
      return;
    }
    // If user enters a digit in DEC mode
    if (formatActiveIndex === 0 && val !== undefined && /^[0-9]$/.test(val)) {
      consume2nd();
      formatInputBuffer = val;
      updateDisplay();
      return;
    }
    // For any other key, exit format mode to standard, then proceed
    if (val === undefined || !/^[0-9]$/.test(val)) {
      consume2nd();
      currentMode = "standard";
      setScreen(fmt(currentNum()));
      setExpr("");
      updateDisplay();
      // DO NOT return, so it falls through to execute the key!
    }
  }

  // Intercept digits/dot/signs when not in standard mode
  if (currentMode !== "standard") {
    // If it's a digit or dot
    if (val !== undefined && /^[0-9.]$/.test(val)) {
      consume2nd();
      if (currentMode === "amort" && (amortActiveIndex === 0 || amortActiveIndex === 1)) {
        amortInputBuffer += val;
        updateDisplay();
      } else if (currentMode === "py") {
        pyInputBuffer += val;
        updateDisplay();
      } else if (currentMode === "format") {
        if (formatActiveIndex === 0 && /^[0-9]$/.test(val)) {
          formatInputBuffer = val;
          updateDisplay();
        }
      }
      return;
    }
  }

  // ── 2ND + . (FORMAT) ─────────────────────────────
  if (val === ".") {
    const is2ndActive = consume2nd();
    if (is2ndActive) {
      currentMode = "format";
      formatInputBuffer = "";
      updateDisplay();
      return;
    }
  }

  // ── Digit / operator tap in Standard Mode ─────────
  if (val !== undefined) {
    consume2nd();
    appendValue(val);
    return;
  }

  if (!action) return;

  // ── 2ND ─────────────────────────────────────────
  if (action === "2nd") { toggle2nd(); return; }

  // ── Memory: STO ─────────────────────────────────
  if (action === "sto") {
    consume2nd();
    openRegOverlay("sto");
    return;
  }

  // ── Memory: RCL ─────────────────────────────────
  if (action === "rcl") {
    consume2nd();
    openRegOverlay("rcl");
    return;
  }

  // ── TVM / AMORT / P/Y ─────────────────────────────
  if (action === "tvm") {
    const is2ndActive = consume2nd();
    const target = btn.dataset.target;

    if (is2ndActive) {
      if (target === "pv") {
        // AMORT Worksheet
        currentMode = "amort";
        amortActiveIndex = 0;
        amortInputBuffer = "";
        tvmPanel.hidden = true;
        cfPanel.hidden = true;
        updateDisplay();
        return;
      }
      if (target === "pmt") {
        // BGN Worksheet
        currentMode = "bgn";
        updateDisplay();
        return;
      }
      if (target === "iy") {
        // P/Y Worksheet
        currentMode = "py";
        pyActiveIndex = 0;
        pyInputBuffer = "";
        updateDisplay();
        return;
      }
    }

    if (isCpt) {
      isCpt = false;
      setStatus("", "");
      solveTVM(target);
      return;
    }

    // Normal TVM key click: store current screen value into the TVM variable
    const valToStore = currentNum();
    if (target && tvmInputs[target]) {
      tvmInputs[target].value = fmt(valToStore);
    }

    currentMode = "standard";
    openTVM();
    if (target && tvmInputs[target]) tvmInputs[target].focus();
    
    setExpr(target.toUpperCase() + " =");
    setScreen(fmt(valToStore));
    expression = "0";
    return;
  }

  // ── CLR TVM / FV ─────────────────────────────────
  if (action === "clrTVM") {
    const is2ndActive = consume2nd();
    if (is2ndActive) {
      // 2ND + FV → CLR TVM
      ["n", "iy", "pv", "pmt", "fv"].forEach((k) => {
        if (tvmInputs[k]) tvmInputs[k].value = "";
      });
      openTVM();
      setStatus("TVM", "Cleared");
    } else {
      if (isCpt) {
        isCpt = false;
        setStatus("", "");
        solveTVM("fv");
        return;
      }

      // Regular FV click: store current screen value and focus FV
      const valToStore = currentNum();
      if (tvmInputs.fv) {
        tvmInputs.fv.value = fmt(valToStore);
      }
      currentMode = "standard";
      openTVM();
      if (tvmInputs.fv) tvmInputs.fv.focus();
      
      setExpr("FV =");
      setScreen(fmt(valToStore));
      expression = "0";
    }
    return;
  }

  // ── Arrow Keys Navigation ────────────────────────
  if (action === "arrowUp") {
    consume2nd();
    if (currentMode === "amort") {
      amortActiveIndex = (amortActiveIndex + 4) % 5;
      amortInputBuffer = "";
      updateDisplay();
    }
    return;
  }

  if (action === "arrowDn") {
    consume2nd();
    if (currentMode === "amort") {
      amortActiveIndex = (amortActiveIndex + 1) % 5;
      amortInputBuffer = "";
      updateDisplay();
    }
    return;
  }

  // ── CF Worksheet ─────────────────────────────────
  if (action === "cf") {
    consume2nd();
    openCF();
    return;
  }

  // ── NPV/IRR quick CPT ───────────────────────────
  if (action === "npv") {
    consume2nd();
    openCF();
    document.getElementById("cfRate").focus();
    return;
  }

  if (action === "irr") {
    consume2nd();
    openCF();
    document.getElementById("btnCptIRR").click();
    return;
  }

  // ── Equals / evaluate / save inputs ──────────────
  if (action === "equals") {
    if (consume2nd()) {
      if (currentMode === "standard") {
        // ANS
        expression = String(lastResult);
        setScreen(fmt(lastResult));
        setExpr("ANS");
      }
    } else {
      if (currentMode === "amort" && (amortActiveIndex === 0 || amortActiveIndex === 1)) {
        if (amortInputBuffer !== "") {
          const v = parseFloat(amortInputBuffer);
          if (Number.isFinite(v)) {
            if (amortActiveIndex === 0) amortP1 = v;
            else amortP2 = v;
          }
          amortInputBuffer = "";
        }
        updateDisplay();
      } else if (currentMode === "standard") {
        safeEval();
      }
    }
    return;
  }

  // ── ENTER / assign ──
  if (action === "enter" || action === "assign") {
    consume2nd();
    if (currentMode === "amort" && (amortActiveIndex === 0 || amortActiveIndex === 1)) {
      if (amortInputBuffer !== "") {
        const v = parseFloat(amortInputBuffer);
        if (Number.isFinite(v)) {
          if (amortActiveIndex === 0) amortP1 = v;
          else amortP2 = v;
        }
        amortInputBuffer = "";
      }
      updateDisplay();
    } else if (currentMode === "standard") {
      safeEval();
    }
    return;
  }

  // ── CPT / 2ND+CPT (QUIT) ──────────────────────────
  if (action === "cpt") {
    if (consume2nd()) {
      // 2ND + CPT → QUIT: close panels, reset display to 0, return to standard
      currentMode = "standard";
      expression = "0";
      lastResult  = 0;
      isCpt = false;
      tvmPanel.hidden   = true;
      cfPanel.hidden    = true;
      regOverlay.hidden = true;
      setScreen(fmt(0));
      setExpr("");
      setStatus("", "QUIT");
      updateDisplay();
    } else {
      if (currentMode === "standard") {
        isCpt = true;
        setStatus("CPT", "");
      }
    }
    return;
  }

  // ── Backspace / DEL ──────────────────────────────
  if (action === "backspace") {
    consume2nd();
    if (currentMode === "amort" && (amortActiveIndex === 0 || amortActiveIndex === 1)) {
      if (amortInputBuffer.length > 0) {
        amortInputBuffer = amortInputBuffer.slice(0, -1);
        updateDisplay();
      }
      return;
    }
    expression = expression.length > 1 ? expression.slice(0, -1) : "0";
    setScreen(expression);
    return;
  }

  // ── Clear all / CLR WORK ─────────────────────────
  if (action === "clearAll") {
    const is2ndActive = consume2nd();
    if (is2ndActive) {
      // 2ND + CE/C → CLR WORK
      if (currentMode === "amort") {
        amortP1 = 1;
        amortP2 = 1;
        amortActiveIndex = 0;
        amortInputBuffer = "";
        updateDisplay();
      } else {
        // Clear standard values
        is2nd = false;
        btn2nd.classList.remove("active");
        expression = "0";
        pendingOp  = null;
        regOverlay.hidden = true;
        setScreen(fmt(0));
        setExpr("");
        setStatus("", "Cleared");
        updateDisplay();
      }
      return;
    }

    // Normal CE/C click
    if (currentMode === "amort" && (amortActiveIndex === 0 || amortActiveIndex === 1)) {
      amortInputBuffer = "";
      updateDisplay();
      return;
    }

    is2nd = false;
    btn2nd.classList.remove("active");
    expression = "0";
    pendingOp  = null;
    regOverlay.hidden = true;
    setScreen(fmt(0));
    setExpr("");
    setStatus("", "Cleared");
    updateDisplay();
    return;
  }

  // ── Plus/Minus sign flip ─────────────────────────
  if (action === "plusMinus") {
    const is2ndActive = consume2nd();
    if (is2ndActive) {
      // 2ND + +/- is RESET
      decVal = 2;
      calcMode = "Chn";
      angleMode = "DEG";
      dateMode = "US";
      expression = "0";
      setScreen(fmt(0));
      setExpr("");
      setStatus("", "RESET");
      updateDisplay();
      return;
    }
    if (currentMode === "amort" && (amortActiveIndex === 0 || amortActiveIndex === 1)) {
      if (amortInputBuffer.startsWith("-")) {
        amortInputBuffer = amortInputBuffer.slice(1);
      } else {
        amortInputBuffer = "-" + amortInputBuffer;
      }
      updateDisplay();
      return;
    }
    expression = expression.startsWith("-") ? expression.slice(1) : "-" + expression;
    setScreen(expression);
    return;
  }

  // ── Parens ───────────────────────────────────────
  if (action === "openParen")  { consume2nd(); appendValue("("); return; }
  if (action === "closeParen") { consume2nd(); appendValue(")"); return; }

  // ── Math functions ───────────────────────────────
  if (action === "percent") {
    consume2nd();
    applyMath((x) => x / 100);
    setExpr("÷ 100");
    return;
  }

  if (action === "sqrt") {
    consume2nd();
    applyMath((x) => {
      if (x < 0) throw new Error("√ of negative");
      return Math.sqrt(x);
    });
    setExpr("√x");
    return;
  }

  if (action === "square") {
    consume2nd();
    applyMath((x) => x * x);
    setExpr("x²");
    return;
  }

  if (action === "reciprocal") {
    consume2nd();
    applyMath((x) => {
      if (x === 0) throw new Error("Divide by 0");
      return 1 / x;
    });
    setExpr("1/x");
    return;
  }

  if (action === "ln") {
    if (consume2nd()) {
      // eˣ
      applyMath((x) => Math.exp(x));
      setExpr("eˣ");
    } else {
      applyMath((x) => {
        if (x <= 0) throw new Error("LN domain error");
        return Math.log(x);
      });
      setExpr("LN");
    }
    return;
  }

  if (action === "inv") {
    consume2nd();
    applyMath((x) => 1 / x);
    setExpr("INV");
    return;
  }

  if (action === "power") {
    // Enters y^x mode: append ** operator so user types exponent
    consume2nd();
    if (!expression.endsWith("**")) {
      expression += "**";
    }
    setScreen(expression);
    return;
  }

  // ── Unimplemented keys – show label ─────────────
  consume2nd();
});

/* ── Keyboard support ────────────────────────────────── */
window.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") return; // don't intercept worksheet inputs

  // Intercept for BGN, AMORT, and P/Y worksheet modes
  if (currentMode !== "standard") {
    if (currentMode === "bgn") {
      if (e.key === "Enter" || e.key === "=") {
        e.preventDefault();
        isBgn = !isBgn;
        updateDisplay();
        return;
      }
      if (e.key === "Escape" || e.key.toLowerCase() === "c") {
        currentMode = "standard";
        expression = "0";
        setScreen("0");
        setExpr("");
        updateDisplay();
        return;
      }
      currentMode = "standard";
      expression = "0";
      setScreen("0");
      setExpr("");
      updateDisplay();
      // Let it fall through to standard key handling
    }

    if (currentMode === "py") {
      if (e.key === "Enter" || e.key === "=") {
        e.preventDefault();
        if (pyInputBuffer !== "") {
          const v = parseFloat(pyInputBuffer);
          if (Number.isFinite(v) && v > 0) {
            if (pyActiveIndex === 0) {
              pyVal = v;
              cyVal = v; // C/Y follows P/Y
            } else {
              cyVal = v;
            }
          }
          pyInputBuffer = "";
        }
        updateDisplay();
        return;
      }
      if (e.key === "Escape" || e.key.toLowerCase() === "c") {
        if (pyInputBuffer !== "") {
          pyInputBuffer = "";
          updateDisplay();
        } else {
          currentMode = "standard";
          setScreen(fmt(currentNum()));
          setExpr("");
          updateDisplay();
        }
        return;
      }
      if (e.key === "Backspace") {
        if (pyInputBuffer.length > 0) {
          pyInputBuffer = pyInputBuffer.slice(0, -1);
          updateDisplay();
        }
        return;
      }
      if (e.key === "ArrowUp" || e.key === "ArrowDown") {
        e.preventDefault();
        pyActiveIndex = (pyActiveIndex + 1) % 2;
        pyInputBuffer = "";
        updateDisplay();
        return;
      }
      if (/^[0-9.]$/.test(e.key)) {
        pyInputBuffer += e.key;
        updateDisplay();
        return;
      }
      // Any other key in py mode: exit to standard, then fall through
      currentMode = "standard";
      setScreen(fmt(currentNum()));
      setExpr("");
      updateDisplay();
    }

    if (currentMode === "format") {
      if (e.key === "Enter" || e.key === "=") {
        e.preventDefault();
        if (formatActiveIndex === 0) {
          if (formatInputBuffer !== "") {
            const v = parseInt(formatInputBuffer, 10);
            if (Number.isFinite(v) && v >= 0 && v <= 9) {
              decVal = v;
            }
            formatInputBuffer = "";
          }
        } else if (formatActiveIndex === 1) {
          angleMode = (angleMode === "DEG" ? "RAD" : "DEG");
        } else if (formatActiveIndex === 2) {
          dateMode = (dateMode === "US" ? "EUR" : "US");
        } else if (formatActiveIndex === 3) {
          calcMode = (calcMode === "Chn" ? "AOS" : "Chn");
        }
        updateDisplay();
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        formatActiveIndex = (formatActiveIndex + 3) % 4;
        formatInputBuffer = "";
        updateDisplay();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        formatActiveIndex = (formatActiveIndex + 1) % 4;
        formatInputBuffer = "";
        updateDisplay();
        return;
      }
      if (e.key === "Escape" || e.key.toLowerCase() === "c") {
        if (formatInputBuffer !== "") {
          formatInputBuffer = "";
          updateDisplay();
        } else {
          currentMode = "standard";
          setScreen(fmt(currentNum()));
          setExpr("");
          updateDisplay();
        }
        return;
      }
      if (e.key === "Backspace") {
        if (formatInputBuffer.length > 0) {
          formatInputBuffer = "";
          updateDisplay();
        }
        return;
      }
      if (/^[0-9]$/.test(e.key) && formatActiveIndex === 0) {
        formatInputBuffer = e.key;
        updateDisplay();
        return;
      }
      // Any other key in format mode: exit to standard, then fall through
      currentMode = "standard";
      setScreen(fmt(currentNum()));
      setExpr("");
      updateDisplay();
    }

    if (/^[0-9.]$/.test(e.key)) {
      if (currentMode === "amort" && (amortActiveIndex === 0 || amortActiveIndex === 1)) {
        amortInputBuffer += e.key;
        updateDisplay();
      }
      return;
    }
    if (e.key === "Enter" || e.key === "=") {
      e.preventDefault();
      if (currentMode === "amort" && (amortActiveIndex === 0 || amortActiveIndex === 1)) {
        if (amortInputBuffer !== "") {
          const v = parseFloat(amortInputBuffer);
          if (Number.isFinite(v)) {
            if (amortActiveIndex === 0) amortP1 = v;
            else amortP2 = v;
          }
          amortInputBuffer = "";
        }
        updateDisplay();
      }
      return;
    }
    if (e.key === "Backspace") {
      if (currentMode === "amort" && (amortActiveIndex === 0 || amortActiveIndex === 1)) {
        if (amortInputBuffer.length > 0) {
          amortInputBuffer = amortInputBuffer.slice(0, -1);
          updateDisplay();
        }
      }
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (currentMode === "amort") {
        amortActiveIndex = (amortActiveIndex + 4) % 5;
        amortInputBuffer = "";
        updateDisplay();
      }
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (currentMode === "amort") {
        amortActiveIndex = (amortActiveIndex + 1) % 5;
        amortInputBuffer = "";
        updateDisplay();
      }
      return;
    }
    if (e.key === "Escape" || e.key.toLowerCase() === "c") {
      if (currentMode === "amort" && (amortActiveIndex === 0 || amortActiveIndex === 1)) {
        amortInputBuffer = "";
        updateDisplay();
      }
      return;
    }
    return; // Ignore other keys in worksheet mode
  }

  // Standard Mode Keyboard support
  if (/^[0-9]$/.test(e.key) || ["+", "-", "/", ".", "(", ")"].includes(e.key)) {
    appendValue(e.key);
    return;
  }
  if (e.key === "*") { appendValue("*"); return; }
  if (e.key === "Enter" || e.key === "=") { e.preventDefault(); safeEval(); return; }
  if (e.key === "Backspace") {
    expression = expression.length > 1 ? expression.slice(0, -1) : "0";
    setScreen(expression);
    return;
  }
  if (e.key === "Escape" || e.key.toLowerCase() === "c") {
    expression = "0"; setScreen(fmt(0)); setExpr(""); setStatus("");
  }
});

/* ── Boot ────────────────────────────────────────────── */
setScreen(fmt(0));
setExpr("");
setStatus("", "BA II PLUS");
updateDisplay();

/* ── Other Calculators Slider Arrows ─────────────────── */
document.addEventListener("DOMContentLoaded", () => {
  const track = document.getElementById("calcSliderTrack");
  const prevBtn = document.getElementById("calcSliderPrev");
  const nextBtn = document.getElementById("calcSliderNext");

  if (track && prevBtn && nextBtn) {
    const getScrollStep = () => {
      const card = track.querySelector(".calc-card");
      if (!card) return 288;
      const style = window.getComputedStyle(track);
      const gap = parseFloat(style.gap) || 24;
      return card.offsetWidth + gap;
    };

    nextBtn.addEventListener("click", () => {
      const step = getScrollStep();
      if (track.scrollLeft + track.clientWidth >= track.scrollWidth - 15) {
        track.scrollTo({ left: 0, behavior: "smooth" });
      } else {
        track.scrollBy({ left: step, behavior: "smooth" });
      }
    });

    prevBtn.addEventListener("click", () => {
      const step = getScrollStep();
      if (track.scrollLeft <= 15) {
        track.scrollTo({ left: track.scrollWidth, behavior: "smooth" });
      } else {
        track.scrollBy({ left: -step, behavior: "smooth" });
      }
    });
  }
});

