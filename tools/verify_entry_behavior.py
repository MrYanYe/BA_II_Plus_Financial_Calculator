#!/usr/bin/env python3
"""
Verification -- verify_entry_behavior.py

Two things a real BA II Plus does that the web version does not, both about when
an entry ends:

  * STO and RCL work from the keypad -- press the key, then a digit -- while the
    original register panel is still there and still works.
  * A finished calculation finishes the entry. After `1 + 2 =` the 3.00 is a
    result, so pressing `4` starts a new number rather than extending it to 34.
    Operators go the other way: they keep the value, because it is the left
    operand.

Checks, following the flows in the request:

  1. both       STO opens the register panel and a digit completes it; the panel
                route does the same thing, so the two cannot drift apart.
  2. fresh      STO is a completed operation: the next digit starts a new entry,
                so `82 STO 2` then `2` `3` shows 23 and not 8223.
  2b. result    The same after `=`, `√`, `x²`, `1/x` and `%`: a digit starts a new
                calculation, but an operator keeps the result. Reported as
                `1 + 2 =` then `4` showing 34 instead of 4.
  2c. operator  A recall followed by an operator keeps the recalled value. It used
                to be dropped, so `RCL N` then `+` showed `0+`.
  3. recall     `RCL 1` brings a value back, formatted.
  4. mid        `23 + RCL 1 =` recalls into a running calculation, which the
                panel alone could never do -- it replaced the whole expression.
  5. tvm        `RCL I/Y` recalls the TVM variable instead of overwriting it with
                the display, which is what upstream does. Covers all five.
  5b. display   STO stores the number on screen, which is not always what the
                engine's entry buffer holds: after a TVM key it stores into that
                variable and resets `expression` to "0" while the LCD keeps showing
                the value, so `8 N` then `STO 1` must save 8 and not 0. A CPT solve
                leaves the same shape.
  6. registers  Ten separate registers, and they do not alias each other.
  7. clear      `0 STO 1` empties a register.
  8. store-mid  STO mid-expression stores the evaluated display, as the TVM keys
                do, and `23 + 4 = STO 3` stores the result.
  9. cancel     A key that is not a digit abandons the pending STO/RCL and then
                does its own job; pressing STO twice cancels.
 10. negatives  Recalling a negative value still evaluates correctly.
 11. modes      Works in Chn and in AOS.
 12. intact     The calculator's own arithmetic is unchanged.

Runs entirely against the local artifact; no network needed.

    python tools/verify_entry_behavior.py [path/to/artifact.html]
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "BAII_Plus_Financial_Calculator_Offline_2026.html"

failures: list[str] = []

# data-target of each TVM key, and the input it writes to.
TVM_KEYS = [("n", "tvmN"), ("iy", "tvmIY"), ("pv", "tvmPV"), ("pmt", "tvmPMT")]


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


def tvm_key(page: Page, target: str) -> None:
    """The N / I-Y / PV / PMT keys. FV carries data-action=clrTVM instead."""
    if target == "fv":
        page.evaluate("document.querySelector('button.key[data-action=\"clrTVM\"]').click()")
    else:
        page.evaluate(
            f"document.querySelector('button.key[data-action=\"tvm\"]"
            f"[data-target=\"{target}\"]').click()"
        )


def click_reg(page: Page, i: int) -> None:
    """Click register i in the panel, the way the panel's users do."""
    page.evaluate(
        f"[...document.querySelectorAll('#regGrid .reg-btn')][{i}].click()"
    )


def screen(page: Page) -> str:
    return page.evaluate("document.getElementById('screen').textContent.trim()")


def status(page: Page) -> str:
    return page.evaluate(
        "(document.getElementById('statusLeft').textContent + ' ' + "
        "document.getElementById('statusRight').textContent).trim()"
    )


def reg_open(page: Page) -> bool:
    return page.evaluate("!document.getElementById('registerOverlay').hidden")


def tvm_input(page: Page, i: str) -> str:
    return page.evaluate(f"document.getElementById('{i}').value")


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  PASS  {label}: {got!r}")
    else:
        failures.append(f"{label}: got {got!r}, expected {want!r}")
        print(f"  FAIL  {label}: got {got!r}, expected {want!r}")


def hide_panels(page: Page) -> None:
    page.evaluate(
        "for (const id of ['tvmPanel','cfPanel','registerOverlay'])"
        " document.getElementById(id).hidden = true;"
    )


def reset(page: Page) -> None:
    keys(page, ("a", "clearAll"), ("a", "clearAll"))
    # isCpt (the CPT toggle) latches: pressing CPT arms it so the next TVM key
    # solves instead of storing. Left set it turns a later TVM check into a solve,
    # which errors on empty registers. Clear the one-shot flags as well.
    page.evaluate("for (let i = 0; i < 10; i++) MEM[i] = 0; isCpt = false;")
    hide_panels(page)
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
    """Type a number with the on-screen keypad, minus via +/-."""
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

        # ---------- 1: the panel is still there, and the keypad works ----------
        print("=== 1. STO opens the panel, and a digit completes it ===")
        reset(page)
        type_number(page, "1234")
        key(page, "a", "sto")
        page.wait_for_timeout(150)
        check("panel opened", reg_open(page), True)
        check("the panel offers ten registers",
              page.evaluate("document.querySelectorAll('#regGrid .reg-btn').length"), 10)
        armed = status(page)
        key(page, "v", "1")
        page.wait_for_timeout(150)
        check("MEM[1] after pressing 1", page.evaluate("MEM[1]"), 1234)
        check("panel closed again", reg_open(page), False)
        print(f"        status while waiting: {armed!r}")

        print("\n--- and the panel route does the same ---")
        reset(page)
        type_number(page, "4321")
        keys(page, ("a", "sto"))
        page.wait_for_timeout(150)
        click_reg(page, 3)
        page.wait_for_timeout(150)
        check("MEM[3] after clicking register 3", page.evaluate("MEM[3]"), 4321)
        check("panel closed again", reg_open(page), False)

        # ---------- 2: a completed STO starts a fresh entry ----------
        print("\n=== 2. after STO the next digit starts a new entry ===")
        reset(page)
        keys(page, ("v", "8"), ("v", "2"), ("a", "sto"), ("v", "2"))
        page.wait_for_timeout(150)
        check("screen after STO 2", screen(page), "82.00")
        check("MEM[2]", page.evaluate("MEM[2]"), 82)
        keys(page, ("v", "2"), ("v", "3"))
        page.wait_for_timeout(150)
        check("then 2 3", screen(page), "23")

        # ---------- 3: recall ----------
        print("\n=== 3. RCL 1 brings it back ===")
        reset(page)
        page.evaluate("MEM[1] = 1234;")
        keys(page, ("a", "rcl"), ("v", "1"))
        page.wait_for_timeout(150)
        check("screen", screen(page), "1,234.00")

        # ---------- 4: mid-expression recall ----------
        print("\n=== 4. 23 + RCL 1 = (the case the panel could not do) ===")
        reset(page)
        page.evaluate("MEM[1] = 100;")
        keys(page, ("v", "2"), ("v", "3"), ("v", "+"), ("a", "rcl"), ("v", "1"))
        page.wait_for_timeout(150)
        check("expression", screen(page), "23+100")
        key(page, "a", "equals")
        page.wait_for_timeout(150)
        check("=", screen(page), "123.00")

        # ---------- 5: RCL + a TVM key recalls, not overwrites ----------
        print("\n=== 5. RCL + a TVM key recalls the variable ===")
        for target, input_id in TVM_KEYS + [("fv", "tvmFV")]:
            reset(page)
            # put a value in through the keyboard, then leave the worksheet
            type_number(page, "6")
            tvm_key(page, target)
            page.wait_for_timeout(200)
            page.evaluate("document.getElementById('tvmClose').click()")
            hide_panels(page)
            page.wait_for_timeout(120)
            stored = tvm_input(page, input_id)

            reset(page)
            keys(page, ("v", "2"), ("v", "3"), ("v", "+"), ("a", "rcl"))
            page.wait_for_timeout(120)
            tvm_key(page, target)
            page.wait_for_timeout(200)
            got = screen(page)
            after = tvm_input(page, input_id)
            hide_panels(page)
            if got == "23+6" and after == stored and stored != "":
                print(f"  PASS  RCL {target.upper()}: recalled into 23+6, variable untouched")
            else:
                failures.append(
                    f"RCL {target.upper()}: screen {got!r} (want '23+6'), "
                    f"variable {stored!r} -> {after!r}"
                )
                print(f"  FAIL  RCL {target.upper()}: screen {got!r}, "
                      f"variable {stored!r} -> {after!r}")

        # ---------- 2b: a result key also finishes the entry ----------
        print("\n=== 2b. after a result the next digit starts a new calculation ===")
        for action, setup, shown in (
            ("equals", (("v", "1"), ("v", "+"), ("v", "2")), "3.00"),
            ("sqrt", (("v", "9"),), "3.00"),
            ("square", (("v", "3"),), "9.00"),
            ("reciprocal", (("v", "4"),), "0.25"),
            ("percent", (("v", "5"), ("v", "0")), "0.50"),
        ):
            reset(page)
            keys(page, *setup)
            key(page, "a", action)
            page.wait_for_timeout(150)
            if screen(page) != shown:
                failures.append(f"{action} produced {screen(page)!r}, expected {shown!r}")
                print(f"  FAIL  {action} produced {screen(page)!r}, expected {shown!r}")
                continue
            key(page, "v", "7")
            page.wait_for_timeout(150)
            got = screen(page)
            if got == "7":
                print(f"  PASS  after {action}: a digit starts a new entry")
            else:
                failures.append(f"after {action}: typing 7 gave {got!r}, expected '7'")
                print(f"  FAIL  after {action}: typing 7 gave {got!r}")

        print("\n--- but an operator keeps the result ---")
        reset(page)
        keys(page, ("v", "1"), ("v", "+"), ("v", "2"), ("a", "equals"))
        page.wait_for_timeout(150)
        keys(page, ("v", "*"), ("v", "3"), ("a", "equals"))
        page.wait_for_timeout(150)
        check("1+2= then *3=", screen(page), "9.00")

        # ---------- 2c: an operator after a recall keeps the value ----------
        print("\n=== 2c. an operator after a recall keeps the recalled value ===")
        reset(page)
        page.evaluate("MEM[1] = 5;")
        keys(page, ("a", "rcl"), ("v", "1"))
        page.wait_for_timeout(150)
        keys(page, ("v", "+"))
        page.wait_for_timeout(150)
        check("RCL 1 then +", screen(page), "5+")
        keys(page, ("v", "6"), ("a", "equals"))
        page.wait_for_timeout(150)
        check("then 6 =", screen(page), "11.00")

        # ---------- 5b: STO stores what the DISPLAY shows ----------
        print("\n=== 5b. STO stores the displayed value ===")
        reset(page)
        key(page, "v", "8")
        tvm_key(page, "n")
        page.wait_for_timeout(250)
        page.evaluate("document.getElementById('tvmClose').click()")
        hide_panels(page)
        page.wait_for_timeout(150)
        # The engine writes 8 into N and resets `expression` to "0"; the LCD shows
        # 8.00. Reading the expression would save 0.
        keys(page, ("a", "sto"), ("v", "1"))
        page.wait_for_timeout(200)
        check("8 then N then STO 1", page.evaluate("MEM[1]"), 8)

        reset(page)
        # N=10, I/Y=5, PV=-1000, PMT=0, then CPT FV
        type_number(page, "10")
        tvm_key(page, "n")
        page.wait_for_timeout(150)
        type_number(page, "5")
        tvm_key(page, "iy")
        page.wait_for_timeout(150)
        type_number(page, "1000")
        key(page, "a", "plusMinus")
        tvm_key(page, "pv")
        page.wait_for_timeout(150)
        type_number(page, "0")
        tvm_key(page, "pmt")
        page.wait_for_timeout(150)
        key(page, "a", "cpt")
        page.wait_for_timeout(100)
        tvm_key(page, "fv")
        page.wait_for_timeout(300)
        fv_shown = page.evaluate("document.getElementById('tvmFV').value")
        keys(page, ("a", "sto"), ("v", "1"))
        page.wait_for_timeout(200)
        stored_fv = page.evaluate("MEM[1]")
        if fv_shown and abs(stored_fv - float(fv_shown.replace(",", ""))) < 0.005:
            print(f"  PASS  CPT FV then STO 1 stored {stored_fv} (display showed {fv_shown})")
        else:
            failures.append(f"CPT FV then STO 1 stored {stored_fv}, display showed {fv_shown!r}")
            print(f"  FAIL  CPT FV then STO 1 stored {stored_fv}, display showed {fv_shown!r}")

        print("\n--- and the unchanged cases still hold ---")
        reset(page)
        type_number(page, "23")
        keys(page, ("v", "+"), ("v", "4"))
        keys(page, ("a", "sto"), ("v", "2"))
        page.wait_for_timeout(200)
        # "23+4" is not a plain number, so the expression is evaluated: 27.
        check("23+4 STO 2 (unevaluated display)", page.evaluate("MEM[2]"), 27)

        # ---------- 6: ten registers, no aliasing ----------
        print("\n=== 6. ten independent registers ===")
        reset(page)
        for i in range(10):
            keys(page, ("a", "clearAll"), ("a", "clearAll"))
            type_number(page, str(100 * (i + 1)))
            keys(page, ("a", "sto"), ("v", str(i)))
            page.wait_for_timeout(50)
        check("MEM", page.evaluate("MEM.slice()"), [100 * (i + 1) for i in range(10)])

        # ---------- 7: clearing a register ----------
        print("\n=== 7. 0 STO 1 empties a register ===")
        keys(page, ("a", "clearAll"), ("a", "clearAll"))
        type_number(page, "0")
        keys(page, ("a", "sto"), ("v", "1"))
        page.wait_for_timeout(120)
        check("MEM[1]", page.evaluate("MEM[1]"), 0)

        # ---------- 8: STO mid-expression ----------
        print("\n=== 8. STO works mid-expression too ===")
        reset(page)
        keys(page, ("v", "2"), ("v", "3"), ("v", "+"), ("v", "4"))
        keys(page, ("a", "sto"), ("v", "2"))
        page.wait_for_timeout(150)
        # "The displayed value" is the evaluated expression, not the part-typed
        # entry. That is this engine's own convention -- its TVM keys call the same
        # currentNum() -- and its LCD shows the expression, so the value on screen
        # really is 23+4. A real device displays only the 4 here.
        check("MEM[2] holds the evaluated display, as the TVM keys do",
              page.evaluate("MEM[2]"), 27)

        reset(page)
        keys(page, ("v", "2"), ("v", "3"), ("v", "+"), ("v", "4"), ("a", "equals"))
        keys(page, ("a", "sto"), ("v", "3"))
        page.wait_for_timeout(150)
        check("MEM[3] holds the result", page.evaluate("MEM[3]"), 27)

        # ---------- 9: cancelling ----------
        print("\n=== 9. a non-digit after STO/RCL cancels it ===")
        reset(page)
        type_number(page, "55")
        keys(page, ("a", "sto"), ("a", "cpt"))
        page.wait_for_timeout(150)
        check("STO was abandoned (MEM[0] untouched)", page.evaluate("MEM[0]"), 0)

        reset(page)
        page.evaluate("MEM[5] = 70;")
        keys(page, ("a", "rcl"), ("v", "5"))
        page.wait_for_timeout(150)
        check("RCL 5 recalled 70", screen(page), "70.00")
        keys(page, ("v", "5"))
        page.wait_for_timeout(150)
        check("a completed RCL starts a fresh entry too", screen(page), "5")

        reset(page)
        keys(page, ("a", "sto"), ("a", "sto"), ("v", "1"))
        page.wait_for_timeout(150)
        check("pressing STO twice cancels, so the 1 is just an entry",
              page.evaluate("MEM[1]"), 0)

        # ---------- 10: negative values ----------
        print("\n=== 10. recalling a negative value ===")
        reset(page)
        page.evaluate("MEM[4] = -50;")
        keys(page, ("v", "1"), ("v", "0"), ("v", "0"), ("v", "+"), ("a", "rcl"), ("v", "4"))
        page.wait_for_timeout(150)
        key(page, "a", "equals")
        page.wait_for_timeout(150)
        check("100 + RCL 4", screen(page), "50.00")

        reset(page)
        page.evaluate("MEM[4] = -50;")
        keys(page, ("a", "rcl"), ("v", "4"))
        page.wait_for_timeout(150)
        key(page, "a", "equals")
        page.wait_for_timeout(150)
        check("RCL 4 on its own", screen(page), "-50.00")

        # ---------- 11: AOS ----------
        print("\n=== 11. works in AOS as well as Chn ===")
        set_calc_mode(page, "AOS")
        reset(page)
        page.evaluate("MEM[1] = 100;")
        keys(page, ("v", "2"), ("v", "3"), ("v", "+"),
             ("a", "rcl"), ("v", "1"), ("v", "*"), ("v", "2"), ("a", "equals"))
        page.wait_for_timeout(150)
        # AOS precedence: 23 + (100 * 2), not (23 + 100) * 2.
        check("23 + RCL 1 * 2 in AOS", screen(page), "223.00")

        # ---------- 12: nothing else disturbed ----------
        print("\n=== 12. the rest of the calculator is unchanged ===")
        set_calc_mode(page, "Chn")
        reset(page)
        keys(page, ("v", "7"), ("v", "+"), ("v", "3"), ("v", "*"), ("v", "2"), ("a", "equals"))
        page.wait_for_timeout(150)
        check("7 + 3 * 2 (chain)", screen(page), "20.00")

        reset(page)
        key(page, "v", "8")
        tvm_key(page, "n")
        page.wait_for_timeout(200)
        check("the N key still stores into the TVM worksheet", tvm_input(page, "tvmN"), "8.00")

        browser.close()

    print("\n" + "=" * 62)
    if failures:
        print("RESULT: FAILED -- " + "; ".join(failures))
        return 1
    print("RESULT: PASSED -- STO/RCL from keypad and panel, TVM recall, "
          "and a finished calculation starts a new entry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
