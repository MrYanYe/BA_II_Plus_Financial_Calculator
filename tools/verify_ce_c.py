#!/usr/bin/env python3
"""
Verification -- verify_ce_c.py

Checks the offline-only two-stage CE|C against the behaviour of a real
BA II Plus: one press clears the current entry and keeps the pending operation,
a second consecutive press clears everything.

This is the one place the offline build deliberately does NOT match the live
site, so it cannot be covered by verify_parity.py -- that harness asserts the two
agree, and here they are meant to differ. Hence a separate file.

Six checks:

  1. entry      One press clears the entry, keeps the pending operator, and the
                pending operation still computes.
  2. all        A second consecutive press clears everything.
  3. pair       Any other key between the two presses breaks the pair, so the
                next press is CE again rather than C.
  4. untouched  CLR WORK (2ND + the same key) and the worksheet modes behave
                exactly as upstream.
  5. keyboard   Escape / c follows the same two-stage rule as the on-screen key.
  6. modes      Chn and AOS both behave.

Runs entirely against the local artifact; no network needed.

    python tools/verify_ce_c.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / "BAII_Plus_Financial_Calculator_Offline_2026.html"

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


def keys(page: Page, *specs: tuple[str, str]) -> None:
    for spec in specs:
        key(page, *spec)


def screen(page: Page) -> str:
    return page.evaluate("document.getElementById('screen').textContent.trim()")


def expr(page: Page) -> str:
    return page.evaluate("document.getElementById('displayExpr').textContent.trim()")


def check(label: str, got: str, want: str) -> None:
    if got == want:
        print(f"  PASS  {label}: {got!r}")
    else:
        failures.append(f"{label}: got {got!r}, expected {want!r}")
        print(f"  FAIL  {label}: got {got!r}, expected {want!r}")


def reset(page: Page) -> None:
    """Full clear via two presses, so the engine's own clear runs."""
    keys(page, ("a", "clearAll"), ("a", "clearAll"))
    page.wait_for_timeout(60)


def main() -> int:
    if not LOCAL.is_file():
        print(f"ERROR: {LOCAL.name} not found -- run the build first", file=sys.stderr)
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(LOCAL.as_uri(), wait_until="load")
        page.wait_for_selector("#calculator")
        page.wait_for_timeout(400)

        # ---------- 1: one press = CE ----------
        print("=== 1. one press clears the entry, keeps the operation ===")
        reset(page)
        keys(page, ("v", "1"), ("v", "2"), ("v", "+"), ("v", "5"))
        check("12+5 then", screen(page), "12+5")
        key(page, "a", "clearAll")
        page.wait_for_timeout(80)
        check("after 1 press", screen(page), "12+0")
        keys(page, ("a", "equals"))
        page.wait_for_timeout(80)
        check("= (12+0)", screen(page), "12.00")

        # ---------- 1b: typing after a CE replaces the 0 ----------
        # Not cosmetic: in AOS a leftover "0" makes the next entry "04", and
        # strict-mode evaluation rejects that as a legacy octal literal.
        print("\n=== 1b. typing after CE replaces the placeholder 0 ===")
        reset(page)
        keys(page, ("v", "1"), ("v", "2"), ("v", "+"), ("v", "5"))
        key(page, "a", "clearAll")
        page.wait_for_timeout(80)
        keys(page, ("v", "7"))
        page.wait_for_timeout(80)
        check("CE then 7", screen(page), "12+7")
        keys(page, ("a", "equals"))
        page.wait_for_timeout(80)
        check("= (12+7)", screen(page), "19.00")

        reset(page)
        keys(page, ("v", "1"), ("v", "2"), ("v", "+"), ("v", "5"))
        key(page, "a", "clearAll")
        page.wait_for_timeout(80)
        keys(page, ("v", "."), ("v", "5"))
        page.wait_for_timeout(80)
        check("CE then .5", screen(page), "12+0.5")

        # ---------- 2: second press = C ----------
        print("\n=== 2. second consecutive press clears everything ===")
        reset(page)
        keys(page, ("v", "1"), ("v", "2"), ("v", "+"), ("v", "5"))
        keys(page, ("a", "clearAll"), ("a", "clearAll"))
        page.wait_for_timeout(80)
        check("after 2 presses", screen(page), "0.00")
        keys(page, ("a", "equals"))
        page.wait_for_timeout(80)
        check("= after C", screen(page), "0.00")

        # ---------- 3: an intervening key breaks the pair ----------
        print("\n=== 3. another key between presses breaks the pair ===")
        reset(page)
        keys(page, ("v", "1"), ("v", "2"), ("v", "+"), ("v", "5"))
        key(page, "a", "clearAll")          # CE
        page.wait_for_timeout(60)
        key(page, "a", "backspace")         # intervening key
        page.wait_for_timeout(60)
        key(page, "a", "clearAll")          # must be CE again, not C
        page.wait_for_timeout(80)
        got = screen(page)
        if got == "0.00":
            failures.append("pair not broken: 3rd press behaved as C")
            print(f"  FAIL  third press cleared everything ({got!r})")
        else:
            print(f"  PASS  third press was CE again, not C (screen {got!r})")

        # ---------- 4: CLR WORK and worksheet modes untouched ----------
        print("\n=== 4. CLR WORK and worksheet modes behave as upstream ===")
        reset(page)
        keys(page, ("v", "8"), ("a", "tvm"))
        page.wait_for_timeout(150)
        pg_tvm_before = page.evaluate("document.getElementById('tvmN').value")
        # 2ND + CE|C is CLR WORK: must NOT be intercepted as CE
        keys(page, ("a", "2nd"), ("a", "clearAll"))
        page.wait_for_timeout(150)
        pg_tvm_after = page.evaluate("document.getElementById('tvmN').value")
        if pg_tvm_before == pg_tvm_after:
            print(f"  PASS  CLR WORK left TVM N at {pg_tvm_after!r} (as upstream)")
        else:
            failures.append("CLR WORK altered TVM state")
            print(f"  FAIL  TVM N {pg_tvm_before!r} -> {pg_tvm_after!r}")

        page.evaluate('for (const id of ["tvmPanel","cfPanel","registerOverlay"])'
                      ' document.getElementById(id).hidden = true;')
        page.wait_for_timeout(80)

        # ---------- 5: keyboard ----------
        print("\n=== 5. keyboard Escape / c follows the same rule ===")
        reset(page)
        keys(page, ("v", "1"), ("v", "2"), ("v", "+"), ("v", "5"))
        page.keyboard.press("Escape")
        page.wait_for_timeout(80)
        check("after 1st Escape", screen(page), "12+0")
        page.keyboard.press("Escape")
        page.wait_for_timeout(80)
        check("after 2nd Escape", screen(page), "0.00")

        reset(page)
        keys(page, ("v", "7"), ("v", "+"), ("v", "3"))
        page.keyboard.press("c")
        page.wait_for_timeout(80)
        check("after 1st c", screen(page), "7+0")

        # ---------- 6: AOS mode ----------
        # Reached the way a user reaches it: 2ND + . opens the FORMAT worksheet at
        # DEC, three arrow-downs land on CALC METHOD, ENTER toggles Chn <-> AOS.
        print("\n=== 6. works in AOS as well as Chn ===")
        page.evaluate("""() => {
            for (const id of ['tvmPanel','cfPanel','registerOverlay'])
                document.getElementById(id).hidden = true;
        }""")
        reset(page)
        keys(page, ("a", "2nd"), ("v", "."))
        page.wait_for_timeout(120)
        for _ in range(3):
            key(page, "a", "arrowDn")
            page.wait_for_timeout(60)
        key(page, "a", "enter")
        page.wait_for_timeout(120)
        mode_shown = screen(page)
        if mode_shown == "AOS":
            print(f"  PASS  switched to AOS via the FORMAT worksheet")
        else:
            failures.append(f"could not reach AOS (screen showed {mode_shown!r})")
            print(f"  FAIL  expected AOS on screen, got {mode_shown!r}")
        key(page, "a", "clearAll")   # leave the worksheet
        page.wait_for_timeout(120)

        keys(page, ("v", "2"), ("v", "+"), ("v", "3"), ("v", "*"), ("v", "4"))
        check("AOS expression", screen(page), "2+3*4")
        key(page, "a", "clearAll")
        page.wait_for_timeout(80)
        check("AOS entry cleared", screen(page), "2+3*0")
        keys(page, ("v", "4"), ("a", "equals"))
        page.wait_for_timeout(80)
        check("= (2+3*4)", screen(page), "14.00")

        # the pair works in AOS too: 14.00 -> CE -> C
        key(page, "a", "clearAll")
        page.wait_for_timeout(80)
        check("AOS 1st press = CE", screen(page), "0")
        key(page, "a", "clearAll")
        page.wait_for_timeout(80)
        check("AOS 2nd press = C", screen(page), "0.00")

        browser.close()

    print("\n" + "=" * 62)
    if failures:
        print("RESULT: FAILED -- " + "; ".join(failures))
        return 1
    print("RESULT: PASSED -- CE clears the entry, C clears everything, "
          "worksheet and 2ND paths untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
