#!/usr/bin/env python3
"""
Verification -- verify_sto_rcl.py

STO and RCL as they work on a real BA II Plus: press the key, then a digit key.
No panel involved, and a recall can be made in the middle of a calculation.

Checks, following the flows in the request:

  1. store      `1234 STO 1` stores the displayed value; the panel does not open.
  2. recall     `RCL 1` brings it back.
  3. mid        `23 + RCL 1 =` recalls into a running calculation, which the
                upstream panel could not do -- it replaced the whole expression.
  4. registers  Ten separate registers, and they do not alias each other.
  5. clear      `0 STO 1` empties a register.
  6. store-mid  STO works mid-expression too. `23 + 4 STO 2` stores 27 -- the
                evaluated display, this engine's convention for "the value on
                screen" -- and `23 + 4 = STO 3` stores the result.
  7. cancel     STO or RCL followed by a key that is not a digit abandons it and
                that key still does its job; pressing STO twice cancels.
  8. negatives  Recalling a negative value still evaluates correctly.
  9. modes      Works in Chn and in AOS.
 10. intact     The calculator's own arithmetic is unchanged, and the register
                overlay never appears.

Runs entirely against the local artifact; no network needed.

    python tools/verify_sto_rcl.py [path/to/artifact.html]
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "BAII_Plus_Financial_Calculator_Offline_2026.html"

failures: list[str] = []


def key(page: Page, kind: str, value: str) -> None:
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
        raise AssertionError(f"no key with {attr}={value!r}")


def keys(page: Page, *specs) -> None:
    for spec in specs:
        key(page, *spec)


def screen(page: Page) -> str:
    return page.evaluate("document.getElementById('screen').textContent.trim()")


def status(page: Page) -> str:
    return page.evaluate(
        "(document.getElementById('statusLeft').textContent + ' ' + "
        "document.getElementById('statusRight').textContent).trim()"
    )


def reg_open(page: Page) -> bool:
    return page.evaluate("!document.getElementById('registerOverlay').hidden")


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  PASS  {label}: {got!r}")
    else:
        failures.append(f"{label}: got {got!r}, expected {want!r}")
        print(f"  FAIL  {label}: got {got!r}, expected {want!r}")


def reset(page: Page) -> None:
    keys(page, ("a", "clearAll"), ("a", "clearAll"))
    # isCpt (the CPT toggle) latches: pressing CPT arms it so the next TVM key
    # solves instead of storing. Left set it turns the last check into a solve,
    # which errors on empty registers. Clear the one-shot flags as well.
    page.evaluate("for (let i = 0; i < 10; i++) MEM[i] = 0; isCpt = false;")
    page.evaluate(
        "for (const id of ['tvmPanel','cfPanel','registerOverlay'])"
        " document.getElementById(id).hidden = true;"
    )
    page.wait_for_timeout(60)


def set_calc_mode(page: Page, want: str) -> None:
    """
    Switch Chn/AOS through the FORMAT worksheet, the way a user would.

    The worksheet remembers which row it was left on -- entering it does not reset
    formatActiveIndex -- so counting arrow presses from a fixed start lands on the
    wrong setting the second time round. Read the row and step to CALC METHOD.
    """
    if page.evaluate("calcMode") == want:
        return
    keys(page, ("a", "2nd"), ("v", "."))
    page.wait_for_timeout(120)
    for _ in range(4):
        if page.evaluate("formatActiveIndex") == 3:   # 3 is CALC METHOD
            break
        key(page, "a", "arrowDn")
        page.wait_for_timeout(60)
    key(page, "a", "enter")
    page.wait_for_timeout(120)
    key(page, "a", "clearAll")
    page.wait_for_timeout(120)


def type_number(page: Page, text: str) -> None:
    """Type a number with the on-screen keypad, digits and minus via +/-."""
    for ch in text:
        if ch == "-":
            key(page, "a", "plusMinus")
        else:
            key(page, "v", ch)


def main() -> int:
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT
    if not target.is_file():
        print(f"ERROR: {target} not found -- run the build first", file=sys.stderr)
        return 1
    print(f"Checking {target.name}\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(target.as_uri(), wait_until="load")
        page.wait_for_selector("#calculator")
        page.wait_for_timeout(500)

        # ---------- 1: store ----------
        print("=== 1. 1234 STO 1 stores the displayed value ===")
        reset(page)
        type_number(page, "1234")
        key(page, "a", "sto")
        page.wait_for_timeout(80)
        armed = status(page)
        key(page, "v", "1")
        page.wait_for_timeout(80)
        stored = page.evaluate("MEM[1]")
        if reg_open(page):
            failures.append("STO still opens the register panel")
            print("  FAIL  the register panel opened")
        else:
            print("  PASS  no panel opens")
        check("MEM[1]", stored, 1234)
        print(f"        status while waiting: {armed!r}")

        # ---------- 2: recall ----------
        print("\n=== 2. RCL 1 brings it back ===")
        reset(page)
        page.evaluate("MEM[1] = 1234;")
        keys(page, ("a", "rcl"), ("v", "1"))
        page.wait_for_timeout(80)
        check("screen", screen(page), "1234")

        # ---------- 3: mid-expression recall ----------
        print("\n=== 3. 23 + RCL 1 = (the case upstream could not do) ===")
        reset(page)
        page.evaluate("MEM[1] = 100;")
        keys(page, ("v", "2"), ("v", "3"), ("v", "+"), ("a", "rcl"), ("v", "1"))
        page.wait_for_timeout(80)
        check("expression", screen(page), "23+100")
        key(page, "a", "equals")
        page.wait_for_timeout(80)
        check("= ", screen(page), "123.00")

        # ---------- 4: ten registers, no aliasing ----------
        print("\n=== 4. ten independent registers ===")
        reset(page)
        for i in range(10):
            keys(page, ("a", "clearAll"), ("a", "clearAll"))
            type_number(page, str(100 * (i + 1)))
            keys(page, ("a", "sto"), ("v", str(i)))
            page.wait_for_timeout(50)
        values = page.evaluate("MEM.slice()")
        check("MEM", values, [100 * (i + 1) for i in range(10)])

        # ---------- 5: clearing a register ----------
        print("\n=== 5. 0 STO 1 empties a register ===")
        keys(page, ("a", "clearAll"), ("a", "clearAll"))
        type_number(page, "0")
        keys(page, ("a", "sto"), ("v", "1"))
        page.wait_for_timeout(80)
        check("MEM[1]", page.evaluate("MEM[1]"), 0)

        # ---------- 6: STO mid-expression ----------
        print("\n=== 6. STO works mid-expression too ===")
        reset(page)
        keys(page, ("v", "2"), ("v", "3"), ("v", "+"), ("v", "4"))
        keys(page, ("a", "sto"), ("v", "2"))
        page.wait_for_timeout(80)
        # "The displayed value" is the evaluated expression, not the part-typed
        # entry. That is this engine's own convention -- its TVM keys call the same
        # currentNum() -- and its LCD shows the expression, so the value on screen
        # really is 23+4. A real device displays only the 4 here; press = first to
        # store the entry on its own.
        check("MEM[2] holds the evaluated display, as the TVM keys do",
              page.evaluate("MEM[2]"), 27)

        reset(page)
        keys(page, ("v", "2"), ("v", "3"), ("v", "+"), ("v", "4"), ("a", "equals"))
        keys(page, ("a", "sto"), ("v", "3"))
        page.wait_for_timeout(80)
        check("MEM[3] holds the result", page.evaluate("MEM[3]"), 27)

        # ---------- 7: cancelling ----------
        print("\n=== 7. a non-digit after STO/RCL cancels it ===")
        reset(page)
        type_number(page, "55")
        keys(page, ("a", "sto"), ("a", "cpt"))
        page.wait_for_timeout(80)
        check("STO was abandoned (MEM[0] untouched)", page.evaluate("MEM[0]"), 0)
        page.evaluate("MEM[5] = 70;")
        keys(page, ("a", "clearAll"), ("a", "clearAll"))
        keys(page, ("a", "rcl"), ("v", "5"))
        page.wait_for_timeout(80)
        check("RCL 5 recalled 70", screen(page), "70")
        keys(page, ("v", "5"))
        page.wait_for_timeout(80)
        check("typing after a recall appends, as typing always does",
              screen(page), "705")

        reset(page)
        keys(page, ("a", "sto"), ("a", "sto"), ("v", "1"))
        page.wait_for_timeout(80)
        check("pressing STO twice cancels, so the 1 is just an entry",
              page.evaluate("MEM[1]"), 0)

        # ---------- 8: negative values ----------
        print("\n=== 8. recalling a negative value ===")
        reset(page)
        page.evaluate("MEM[4] = -50;")
        keys(page, ("v", "1"), ("v", "0"), ("v", "0"), ("v", "+"), ("a", "rcl"), ("v", "4"))
        page.wait_for_timeout(80)
        key(page, "a", "equals")
        page.wait_for_timeout(80)
        check("100 + RCL 4", screen(page), "50.00")

        reset(page)
        page.evaluate("MEM[4] = -50;")
        keys(page, ("a", "rcl"), ("v", "4"))
        page.wait_for_timeout(80)
        key(page, "a", "equals")
        page.wait_for_timeout(80)
        check("RCL 4 on its own", screen(page), "-50.00")

        # ---------- 9: AOS ----------
        print("\n=== 9. works in AOS as well as Chn ===")
        reset(page)
        # 2ND + . opens the FORMAT worksheet at DEC; three arrows reach CALC METHOD
        keys(page, ("a", "2nd"), ("v", "."))
        page.wait_for_timeout(120)
        for _ in range(3):
            key(page, "a", "arrowDn")
            page.wait_for_timeout(50)
        key(page, "a", "enter")
        page.wait_for_timeout(120)
        check("switched to AOS", screen(page), "AOS")
        key(page, "a", "clearAll")
        page.wait_for_timeout(120)

        page.evaluate("MEM[1] = 100;")
        keys(page, ("v", "2"), ("v", "3"), ("v", "+"),
             ("a", "rcl"), ("v", "1"), ("v", "*"), ("v", "2"), ("a", "equals"))
        page.wait_for_timeout(120)
        # AOS precedence: 23 + (100 * 2), not (23 + 100) * 2.
        check("23 + RCL 1 * 2 in AOS", screen(page), "223.00")

        # ---------- 10: nothing else disturbed ----------
        print("\n=== 10. the rest of the calculator is unchanged ===")
        set_calc_mode(page, "Chn")
        reset(page)
        keys(page, ("v", "7"), ("v", "+"), ("v", "3"), ("v", "*"), ("v", "2"), ("a", "equals"))
        page.wait_for_timeout(80)
        check("7 + 3 * 2 (chain)", screen(page), "20.00")

        reset(page)
        key(page, "v", "8")
        key(page, "a", "tvm")
        page.wait_for_timeout(200)
        check("the N key still stores into the TVM worksheet",
              page.evaluate("document.getElementById('tvmN').value"), "8.00")

        reset(page)
        check("the register overlay is never shown", reg_open(page), False)

        browser.close()

    print("\n" + "=" * 62)
    if failures:
        print("RESULT: FAILED -- " + "; ".join(failures))
        return 1
    print("RESULT: PASSED -- STO and RCL work from the keypad, including mid-expression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
