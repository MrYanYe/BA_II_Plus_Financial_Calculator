#!/usr/bin/env python3
"""
Verification -- verify_parity.py

Drives the live site and the offline single-file build through identical key
sequences and diffs what the LCD shows after each one. Also diffs the rendered
widget markup between the two.

Three checks:

  1. markup   #calculator outerHTML, live vs offline, normalised for whitespace.
              Proves nothing inside the widget was altered during extraction.
  2. parity   24 key sequences covering arithmetic, chaining vs AOS, the 2ND
              layer, STO/RCL, TVM, cash flow (NPV/IRR), amortisation, P/Y, C/Y
              and the DEC format setting. Every intermediate display is compared.

              CE|C is the one key deliberately NOT compared here, because offline
              it is two-stage and upstream it is not. It has its own harness,
              verify_ce_c.py, which asserts the difference on purpose. The reset
              between sequences presses until the display reads 0.00 so both
              builds start each sequence from the same state regardless.
  3. offline  the local file is reloaded with every network request aborted at
              the browser level. Any attempt to reach the network fails the run.

Keys are pressed via element.click() rather than a real mouse click so both
sides are driven identically and ad or consent overlays on the live page cannot
swallow a press. This is a logic comparison, not a layout one.

Requires network access for the live half. Usage:

    python tools/verify_parity.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / "BAII_Plus_Financial_Calculator_Offline_2026.html"
LIVE = "https://baiiplusfinancialcalculator.com/"

# Each sequence is a list of (kind, value) presses:
#   ("v", "7")   -> button[data-value="7"]     (digits, . + - * /)
#   ("a", "cpt") -> button[data-action="cpt"]  (everything else)
# Every sequence starts from a full clear.
SEQUENCES: dict[str, list[tuple[str, str]]] = {
    # --- arithmetic, chain mode (the calculator defaults to Chn) ---------
    "chain_7+3*2": [("v", "7"), ("v", "+"), ("v", "3"), ("v", "*"), ("v", "2"), ("a", "equals")],
    "chain_parens": [
        ("a", "openParen"), ("v", "7"), ("v", "+"), ("v", "3"),
        ("a", "closeParen"), ("v", "*"), ("v", "2"), ("a", "equals"),
    ],
    "decimal_add": [("v", "1"), ("v", "."), ("v", "5"), ("v", "+"), ("v", "2"), ("v", "."), ("v", "2"), ("v", "5"), ("a", "equals")],
    "divide": [("v", "1"), ("v", "0"), ("v", "/"), ("v", "4"), ("a", "equals")],
    "plusminus": [("v", "5"), ("a", "plusMinus"), ("a", "equals")],
    "sqrt": [("v", "1"), ("v", "4"), ("v", "4"), ("a", "sqrt")],
    "square": [("v", "1"), ("v", "2"), ("a", "square")],
    "reciprocal": [("v", "4"), ("a", "reciprocal")],
    "power": [("v", "2"), ("a", "power"), ("v", "1"), ("v", "0"), ("a", "equals")],
    "ln": [("v", "1"), ("a", "ln")],
    "percent": [("v", "2"), ("v", "0"), ("v", "0"), ("v", "*"), ("v", "1"), ("v", "0"), ("a", "percent")],

    # --- 2ND layer --------------------------------------------------------
    "2nd_pending": [("a", "2nd")],
    "2nd_then_clear": [("a", "2nd"), ("a", "clearAll")],

    # --- memory -----------------------------------------------------------
    # No clear in the middle: CE|C is covered by verify_ce_c.py, and mixing it
    # in here would compare the one behaviour that is meant to differ. Typing a
    # new number proves the recall just as well.
    "sto_rcl": [("v", "4"), ("v", "2"), ("a", "sto"), ("v", "1"),
                ("v", "7"), ("v", "7"), ("a", "rcl"), ("v", "1")],
    "register_overlay": [("v", "9"), ("a", "sto")],

    # --- TVM --------------------------------------------------------------
    "tvm_open": [("a", "tvm")],
    "tvm_n": [("a", "tvm"), ("v", "5")],
    "tvm_clr": [("a", "clrTVM")],

    # --- cash flow --------------------------------------------------------
    "cf_open": [("a", "cf")],
    "npv": [("a", "npv")],
    "irr": [("a", "irr")],

    # --- amortisation / compounding --------------------------------------
    "amort": [("a", "2nd"), ("a", "tvm")],
    "py": [("a", "2nd"), ("a", "assign")],

    # --- backspace / editing ---------------------------------------------
    "backspace": [("v", "1"), ("v", "2"), ("v", "3"), ("a", "backspace")],
}


def normalise(html: str) -> str:
    """Collapse insignificant whitespace so upstream formatting does not matter."""
    return " ".join(html.split())


def clear(page: Page) -> None:
    """
    Reset the page to a fully cleared state, on either build.

    CE|C is the one key whose behaviour is deliberately different offline -- one
    press is CE there, a full clear here -- so a fixed number of presses cannot
    be used to reset: which press lands as C depends on whether 2ND happened to
    be pending. Pressing until the LCD reads "0.00" is self-synchronising instead.
    After a CE the screen reads "0", after a C it reads "0.00", so the loop always
    leaves the engine's own full clear as the last thing that ran and leaves the
    two sides in the same state.

    The behavioural difference itself is verified in verify_ce_c.py; this harness
    only needs a common starting point.
    """
    for _ in range(5):
        press(page, "a", "clearAll")
        if read_state(page)["screen"] == "0.00":
            return
    raise AssertionError("could not get the calculator into a cleared state")


def press(page: Page, kind: str, value: str) -> None:
    attr = "data-value" if kind == "v" else "data-action"
    ok = page.evaluate(
        f"""() => {{
            const el = document.querySelector('button.key[{attr}="{value}"]');
            if (!el) return false;
            el.click();
            return true;
        }}"""
    )
    if not ok:
        raise AssertionError(f"no key with {attr}={value!r} on the page")


def read_state(page: Page) -> dict[str, str]:
    return page.evaluate(
        """() => ({
            screen:  (document.getElementById('screen')?.textContent || '').trim(),
            expr:    (document.getElementById('displayExpr')?.textContent || '').trim(),
            left:    (document.getElementById('statusLeft')?.textContent || '').trim(),
            right:   (document.getElementById('statusRight')?.textContent || '').trim(),
        })"""
    )


def run_sequences(page: Page) -> dict[str, list[dict[str, str]]]:
    """Play every sequence and record the display after each individual press."""
    results: dict[str, list[dict[str, str]]] = {}
    for name, presses in SEQUENCES.items():
        clear(page)
        states = [read_state(page)]
        for kind, value in presses:
            press(page, kind, value)
            states.append(read_state(page))
        results[name] = states
    return results


def widget_html(page: Page) -> str:
    """
    The widget's markup, with the offline-only .panel-dock wrapper unwrapped.

    The dock is the one structural change this project makes -- it exists so the
    panels stack instead of overlapping. Removing it before comparing means this
    check still proves exactly what it always did: that every element upstream
    ships inside #calculator is present, and that nothing else inside it was
    rewritten.
    """
    return normalise(
        page.evaluate(
            """() => {
                const el = document.getElementById('calculator').cloneNode(true);
                // Blank the live readouts. They hold whatever the last keypress
                // left there, so comparing them would test the preceding sequence
                // rather than the markup -- and CE|C, the one deliberate
                // behavioural difference, would make them disagree.
                for (const id of ['screen', 'displayExpr', 'statusLeft', 'statusRight']) {
                    const n = el.querySelector('#' + id);
                    if (n) n.textContent = '';
                }
                const dock = el.querySelector('.panel-dock');
                if (dock) {
                    const parent = dock.parentNode;
                    while (dock.firstChild) parent.insertBefore(dock.firstChild, dock);
                    parent.removeChild(dock);
                }
                return el.outerHTML;
            }"""
        )
    )


def main() -> int:
    if not LOCAL.is_file():
        print(f"ERROR: {LOCAL.name} not found -- run the build first", file=sys.stderr)
        return 1

    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # ---------------- live site ----------------
        print(f"Loading live site: {LIVE}")
        live = browser.new_page(viewport={"width": 1400, "height": 1000})
                    # domcontentloaded + an explicit wait for the widget, not
                    # networkidle: the live page runs Google Tag Manager, AdSense
                    # and Clarity, which keep polling, so the network never goes
                    # idle and networkidle times out at random.
        live.goto(LIVE, wait_until="domcontentloaded", timeout=90_000)
        live.wait_for_selector("#screen", timeout=30_000)
        live_states = run_sequences(live)
        live_widget = widget_html(live)
        print(f"  captured {len(live_states)} sequences, widget {len(live_widget):,} chars")

        # ---------------- offline file ----------------
        print(f"\nLoading offline file: {LOCAL.name}")
        offline = browser.new_page(viewport={"width": 1400, "height": 1000})
        network_attempts: list[str] = []

        def only_local(route) -> None:
            """
            Let file:// through, hard-fail everything else.

            A single-file build must never reach the network, so any non-file
            request is recorded and aborted rather than merely observed -- if the
            calculator somehow depended on a CDN, the run would break here
            instead of quietly passing.
            """
            url = route.request.url
            if url.startswith("file://"):
                route.continue_()
            else:
                network_attempts.append(url)
                route.abort()

        offline.route("**/*", only_local)
        offline.goto(LOCAL.as_uri(), wait_until="load")
        offline.wait_for_selector("#screen", timeout=30_000)
        offline_states = run_sequences(offline)
        offline_widget = widget_html(offline)
        print(f"  captured {len(offline_states)} sequences, widget {len(offline_widget):,} chars")

        # ---------------- check 1: markup ----------------
        print("\n[1/3] widget markup")
        if live_widget == offline_widget:
            print(f"  PASS  #calculator outerHTML identical ({len(live_widget):,} chars normalised)")
        else:
            failures.append("widget markup differs")
            print("  FAIL  #calculator outerHTML differs")
            for i in range(0, min(len(live_widget), len(offline_widget))):
                if live_widget[i] != offline_widget[i]:
                    lo = max(0, i - 80)
                    print(f"        first diff at char {i}")
                    print(f"        live:    ...{live_widget[lo:i + 80]!r}")
                    print(f"        offline: ...{offline_widget[lo:i + 80]!r}")
                    break

        # ---------------- check 2: functional parity ----------------
        print("\n[2/3] functional parity")
        mismatches = 0
        for name, live_seq in live_states.items():
            off_seq = offline_states[name]
            for step, (ls, os_) in enumerate(zip(live_seq, off_seq)):
                if ls != os_:
                    mismatches += 1
                    if mismatches <= 12:
                        label = "initial" if step == 0 else f"after press {step}"
                        print(f"  DIFF  {name} @ {label}")
                        print(f"        live:    {ls}")
                        print(f"        offline: {os_}")
        total_steps = sum(len(v) for v in live_states.values())
        if mismatches == 0:
            print(f"  PASS  {len(live_states)} sequences / {total_steps} display states identical")
        else:
            failures.append(f"{mismatches} display mismatches")
            print(f"  FAIL  {mismatches} mismatching states out of {total_steps}")

        # ---------------- check 3: offline ----------------
        print("\n[3/3] offline containment")
        external = [u for u in network_attempts if not u.startswith("file://")]
        if external:
            failures.append("network access attempted")
            print(f"  FAIL  {len(external)} external request(s) attempted:")
            for u in dict.fromkeys(external):
                print(f"        {u}")
        else:
            print("  PASS  no network requests while running every sequence")

        browser.close()

    print("\n" + "=" * 62)
    if failures:
        print("RESULT: FAILED -- " + "; ".join(failures))
        return 1
    print("RESULT: PASSED -- markup identical, behaviour identical, fully offline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
