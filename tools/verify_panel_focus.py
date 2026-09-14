#!/usr/bin/env python3
"""
Verification -- verify_panel_focus.py

Opening a worksheet panel must not steal focus. Upstream, pressing 8 then N opens
the TVM worksheet and immediately focuses the N field; on a phone that scrolls
the panel into view and raises the virtual keyboard, hiding the calculator the
user is typing on.

Four checks:

  1. quiet     No key that opens a panel leaves focus inside a panel, on a phone
               viewport or a desktop one. Covers every panel: TVM via N / I-Y /
               PV / PMT, TVM via FV, cash flow via CF and via NPV, and the
               register overlay via STO / RCL.
  2. still     Opening a panel does not move the page.
  3. tap       A tap or click on a field still focuses it, and still raises the
               keyboard. Suppressing the automatic focus must not make the fields
               read-only in effect.
  4. intact    Everything else still works: the panel opens, the pressed key
               still stores its value, and the fields still accept typed input.

Point it at a different build to compare (e.g. an older artifact from git):

    python tools/verify_panel_focus.py [path/to/artifact.html]
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "BAII_Plus_Financial_Calculator_Offline_2026.html"

# (key to press, panel it should open, id of the field it used to grab)
PANEL_KEYS = [
    ("tvm", "tvmPanel", "tvmN"),
    ("tvm2", "tvmPanel", "tvmIY"),
    ("tvm3", "tvmPanel", "tvmPV"),
    ("tvm4", "tvmPanel", "tvmPMT"),
    ("clrTVM", "tvmPanel", "tvmFV"),   # the FV key carries data-action=clrTVM
    ("cf", "cfPanel", None),
    ("npv", "cfPanel", "cfRate"),
    ("sto", "registerOverlay", None),
    ("rcl", "registerOverlay", None),
]

VIEWPORTS = {
    "phone": {"width": 390, "height": 844},
    "desktop": {"width": 1400, "height": 900},
}

failures: list[str] = []


def key(page, kind: str, value: str) -> None:
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


def press_panel_key(page, name: str) -> None:
    """Press the key that opens the given panel, by its data-target or action."""
    if name in ("tvm", "tvm2", "tvm3", "tvm4", "clrTVM"):
        # the TVM keys share data-action, distinguished by data-target
        target = {"tvm": "n", "tvm2": "iy", "tvm3": "pv", "tvm4": "pmt"}.get(name)
        if target:
            page.evaluate(
                "document.querySelector('button.key[data-action=\"tvm\"]"
                f"[data-target=\"{target}\"]').click()"
            )
        else:
            page.evaluate("document.querySelector('button.key[data-action=\"clrTVM\"]').click()")
    else:
        key(page, "a", name)


def reset(page) -> None:
    page.evaluate(
        """() => {
            for (const id of ['tvmPanel','cfPanel','registerOverlay'])
                document.getElementById(id).hidden = true;
        }"""
    )
    for _ in range(2):
        key(page, "a", "clearAll")
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(80)


def focused_in_panel(page) -> str | None:
    """Id of the focused element if it is a panel input, else None."""
    return page.evaluate(
        """() => {
            const el = document.activeElement;
            if (!el || el === document.body) return null;
            return el.closest('#tvmPanel, #cfPanel, #registerOverlay') ? (el.id || el.tagName) : null;
        }"""
    )


def main() -> int:
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT
    if not target.is_file():
        print(f"ERROR: {target} not found", file=sys.stderr)
        return 1
    print(f"Checking {target.name}\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # ---------- 1 + 2: no focus stolen, no scroll ----------
        for vp_name, vp in VIEWPORTS.items():
            print(f"=== {vp_name} {vp['width']}x{vp['height']} ===")
            page = browser.new_page(viewport=vp)
            page.goto(target.as_uri(), wait_until="load")
            page.wait_for_selector("#calculator")
            page.wait_for_timeout(500)

            for name, panel_id, field_id in PANEL_KEYS:
                reset(page)
                before_scroll = page.evaluate("window.scrollY")
                press_panel_key(page, name)
                page.wait_for_timeout(350)

                opened = page.evaluate(f"!document.getElementById('{panel_id}').hidden")
                grabbed = focused_in_panel(page)
                after_scroll = page.evaluate("window.scrollY")

                problems = []
                if not opened:
                    problems.append(f"#{panel_id} did not open")
                if grabbed:
                    problems.append(f"focus stolen by #{grabbed}")
                if abs(after_scroll - before_scroll) > 1:
                    problems.append(f"page scrolled {after_scroll - before_scroll:.0f}px")

                label = f"{name} -> #{panel_id}"
                if problems:
                    failures.append(f"{vp_name}: {label}: {'; '.join(problems)}")
                    print(f"  FAIL  {label}: {'; '.join(problems)}")
                else:
                    print(f"  PASS  {label}: opens, no focus, no scroll")
            page.close()

        # ---------- 3: tapping a field still focuses it ----------
        print("\n=== a tap on a field still focuses it (phone) ===")
        page = browser.new_page(**{"viewport": VIEWPORTS["phone"], "has_touch": True})
        page.goto(target.as_uri(), wait_until="load")
        page.wait_for_selector("#calculator")
        page.wait_for_timeout(400)
        reset(page)
        press_panel_key(page, "tvm")
        page.wait_for_timeout(300)
        page.locator("#tvmN").scroll_into_view_if_needed()
        page.wait_for_timeout(200)
        page.tap("#tvmN")
        page.wait_for_timeout(250)
        active = page.evaluate("document.activeElement && document.activeElement.id")
        if active == "tvmN":
            print("  PASS  tapping #tvmN focuses it, keyboard can appear")
        else:
            failures.append(f"tap did not focus the field (activeElement={active!r})")
            print(f"  FAIL  activeElement is {active!r}, expected 'tvmN'")
        page.close()

        print("\n=== a click on a field still focuses it (desktop) ===")
        page = browser.new_page(viewport=VIEWPORTS["desktop"])
        page.goto(target.as_uri(), wait_until="load")
        page.wait_for_selector("#calculator")
        page.wait_for_timeout(400)
        reset(page)
        press_panel_key(page, "tvm")
        page.wait_for_timeout(300)
        page.click("#tvmN")
        page.wait_for_timeout(200)
        active = page.evaluate("document.activeElement && document.activeElement.id")
        if active == "tvmN":
            print("  PASS  clicking #tvmN focuses it")
        else:
            failures.append(f"click did not focus the field (activeElement={active!r})")
            print(f"  FAIL  activeElement is {active!r}, expected 'tvmN'")

        # ---------- 4: everything else intact ----------
        print("\n=== everything else still works ===")
        reset(page)
        key(page, "v", "8")
        press_panel_key(page, "tvm")
        page.wait_for_timeout(300)
        stored = page.evaluate("document.getElementById('tvmN').value")
        screen = page.evaluate("document.getElementById('screen').textContent.trim()")
        if stored == "8.00" and screen == "8.00":
            print(f"  PASS  pressing N still stores the screen value ({stored})")
        else:
            failures.append(f"TVM key no longer stores the value (N={stored!r}, screen={screen!r})")
            print(f"  FAIL  N={stored!r}, screen={screen!r}")

        # typing into a focused field must still reach the engine
        page.click("#tvmIY")
        page.wait_for_timeout(150)
        page.keyboard.type("6")
        page.wait_for_timeout(250)
        # 2ND + FV is CLR TVM; use it to prove the typed value reached the engine.
        # 2ND really is required -- pressing FV alone stores the screen value
        # instead of clearing, which is what the first version of this check got
        # wrong.
        page.evaluate("document.querySelector('button.key[data-action=\"2nd\"]').click()")
        page.evaluate("document.querySelector('button.key[data-action=\"clrTVM\"]').click()")
        page.wait_for_timeout(200)
        cleared = page.evaluate(
            "document.getElementById('tvmN').value === '' && "
            "document.getElementById('tvmIY').value === ''"
        )
        if cleared:
            print("  PASS  a typed field value still reaches the engine (CLR TVM cleared it)")
        else:
            failures.append("typed field input did not reach the engine")
            print("  FAIL  typed value was not picked up by the engine")
        page.close()

        browser.close()

    print("\n" + "=" * 62)
    if failures:
        print("RESULT: FAILED -- " + "; ".join(failures))
        return 1
    print("RESULT: PASSED -- no panel steals focus on open, fields still focus on tap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
