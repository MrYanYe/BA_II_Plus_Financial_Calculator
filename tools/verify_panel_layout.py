#!/usr/bin/env python3
"""
Verification -- verify_panel_layout.py

Checks the offline-only worksheet placement: the panels sit outside the device --
to its left when there is room, below it when there is not -- the device never
moves, and two panels open at once stack rather than overlap.

Six checks:

  1. placed     Wide landscape: the dock is entirely to the left of the device
                and top-aligned with it. Narrow or portrait: the dock is entirely
                below the device. (STO/RCL no longer open the register overlay --
                they are keypad-driven offline -- so only TVM and CF are checked
                here; verify_sto_rcl.py covers the register keys.)
  2. still      Opening each panel leaves the device's bounding box unchanged.
  3. stacked    Two visible panels stack rather than overlap. Forced directly:
                the UI can no longer produce it, since STO/RCL stopped opening the
                register overlay, but the dock exists to guarantee it.
  4. reachable  Whenever a panel is off the bottom of the viewport, the document
                must actually scroll far enough to bring it into view.
  5. adjacent   A stacked panel sits right under the keypad, not under the
                device's empty tail. On a phone upstream stretches the device to
                min-height: 100vh and the keypad stops ~190px short of the bottom,
                so positioning from the device's box left the panel adrift of the
                calculator it belongs to.
  6. live       Resizing a loaded page across the threshold moves the dock
                between the two placements without a reload.

Runs entirely against the local artifact; no network needed.

    python tools/verify_panel_layout.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "BAII_Plus_Financial_Calculator_Offline_2026.html"
# Point it at another build to compare, e.g. an older artifact from git:
#     python tools/verify_panel_layout.py path/to/artifact.html
LOCAL = DEFAULT

# STO/RCL no longer open the register overlay -- they are keypad-driven offline,
# so that panel is unreachable by design. See verify_sto_rcl.py.
PANELS = [
    ("tvm", "tvmPanel"),
    ("cf", "cfPanel"),
]

# How far a stacked panel may sit below the keypad. The device's own bottom
# padding is 18px and the dock's gap is 20px, so a correctly placed panel is
# 38px down; the limit leaves slack for a different padding without allowing the
# ~190px of dead space this check exists to catch.
GAP_LIMIT = 60

WIDE = {"width": 1400, "height": 900}       # landscape, past the threshold
NARROW = {"width": 900, "height": 800}      # landscape, too narrow to fit
PORTRAIT = {"width": 1250, "height": 1400}  # wide enough, but portrait


def rect(page: Page, selector: str) -> dict[str, float] | None:
    return page.evaluate(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el || el.hidden) return null;
            const r = el.getBoundingClientRect();
            return {x: r.x, y: r.y, w: r.width, h: r.height,
                    right: r.right, bottom: r.bottom};
        }""",
        selector,
    )


def overlaps(a: dict, b: dict) -> bool:
    """True if the two boxes share any area. Touching edges do not count."""
    return not (
        a["right"] <= b["x"] + 0.5
        or b["right"] <= a["x"] + 0.5
        or a["bottom"] <= b["y"] + 0.5
        or b["bottom"] <= a["y"] + 0.5
    )


def press(page: Page, action: str) -> None:
    ok = page.evaluate(
        """(a) => {
            const el = document.querySelector(`button.key[data-action="${a}"]`);
            if (!el) return false;
            el.click();
            return true;
        }""",
        action,
    )
    if not ok:
        raise AssertionError(f"no key with data-action={action!r}")


def reset(page: Page) -> None:
    page.evaluate(
        """() => {
            for (const id of ['tvmPanel','cfPanel','registerOverlay'])
                document.getElementById(id).hidden = true;
        }"""
    )
    page.wait_for_timeout(80)


def main() -> int:
    global LOCAL
    if len(sys.argv) > 1:
        LOCAL = Path(sys.argv[1]).resolve()
    if not LOCAL.is_file():
        print(f"ERROR: {LOCAL.name} not found -- run the build first", file=sys.stderr)
        return 1

    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # ---------- 1 + 2: wide landscape -> dock left, device still ----------
        page = browser.new_page(viewport=WIDE)
        page.goto(LOCAL.as_uri(), wait_until="load")
        page.wait_for_selector("#calculator")
        page.wait_for_timeout(500)

        print(f"=== wide landscape {WIDE['width']}x{WIDE['height']} ===")
        for action, panel_id in PANELS:
            reset(page)
            before = rect(page, "#calculator")
            press(page, action)
            page.wait_for_timeout(300)
            after = rect(page, "#calculator")
            panel = rect(page, f"#{panel_id}")

            if panel is None:
                failures.append(f"{panel_id} did not open from key {action!r}")
                print(f"  FAIL  #{panel_id} did not open")
                continue

            moved = max(abs(before[k] - after[k]) for k in ("x", "y", "w", "h"))
            if moved > 0.01:
                failures.append(f"device moved {moved:.2f}px opening #{panel_id}")
                print(f"  FAIL  device moved {moved:.2f}px when #{panel_id} opened")

            gap = after["x"] - panel["right"]
            top_delta = abs(panel["y"] - after["y"])
            if panel["right"] <= after["x"] + 0.5 and top_delta <= 2:
                print(f"  PASS  #{panel_id}: left of device (gap {gap:.0f}px, "
                      f"top-aligned within {top_delta:.1f}px), device unmoved")
            else:
                failures.append(f"#{panel_id} not left of the device (gap {gap:.1f}px)")
                print(f"  FAIL  #{panel_id} gap {gap:.1f}px, top delta {top_delta:.1f}px")

        # ---------- 3: two visible panels must stack, not overlap ----------
        # The UI can no longer produce this: STO and RCL stopped opening the
        # register overlay, and openTVM()/openCF() already hide each other, so at
        # most one panel is ever reachable. The dock exists to make overlap
        # impossible regardless, so prove the property directly by forcing two
        # visible rather than relying on a key sequence that no longer exists.
        print("\n=== two panels visible at once stack (forced) ===")
        reset(page)
        press(page, "tvm")
        page.wait_for_timeout(250)
        page.evaluate("document.getElementById('registerOverlay').hidden = false;")
        page.wait_for_timeout(200)

        tvm = rect(page, "#tvmPanel")
        reg = rect(page, "#registerOverlay")
        calc = rect(page, "#calculator")

        if tvm is None or reg is None:
            failures.append("could not get two panels visible")
            print(f"  FAIL  tvm={'open' if tvm else 'hidden'}, "
                  f"register={'open' if reg else 'hidden'}")
        elif overlaps(tvm, reg):
            failures.append("TVM and register panels overlap")
            print(f"  FAIL  panels overlap: tvm {tvm['y']:.0f}-{tvm['bottom']:.0f}, "
                  f"register {reg['y']:.0f}-{reg['bottom']:.0f}")
        else:
            print(f"  PASS  both open and stacked "
                  f"(tvm y {tvm['y']:.0f}-{tvm['bottom']:.0f}, "
                  f"register y {reg['y']:.0f}-{reg['bottom']:.0f})")
            if reg["y"] < tvm["y"]:
                failures.append("stacking order differs from upstream")
                print("  FAIL  register is above TVM; upstream stacks it below")
            else:
                print("  PASS  in DOM order, as upstream")
            if overlaps(calc, tvm) or overlaps(calc, reg):
                failures.append("a panel overlaps the device")
                print("  FAIL  a panel overlaps the device")
            else:
                print("  PASS  neither panel overlaps the device")

        # ---------- 4: off-screen panels must be scrollable to ----------
        print("\n=== reachability on a short viewport ===")
        page.set_viewport_size({"width": 900, "height": 600})
        page.wait_for_timeout(300)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(150)
        pan = rect(page, "#tvmPanel")
        scrollable = page.evaluate(
            "() => document.documentElement.scrollHeight - window.innerHeight"
        )
        if pan is None:
            failures.append("the TVM panel closed unexpectedly")
            print("  FAIL  panel closed")
        elif pan["bottom"] <= 600:
            print(f"  PASS  panel already visible (bottom {pan['bottom']:.0f})")
        elif scrollable + 1 >= pan["bottom"] - 600:
            print(f"  PASS  panel is {pan['bottom'] - 600:.0f}px below the fold, "
                  f"page scrolls {scrollable:.0f}px -- reachable")
        else:
            failures.append("panel is off-screen and unreachable")
            print(f"  FAIL  panel bottom {pan['bottom']:.0f} but page only scrolls "
                  f"{scrollable:.0f}px -- unreachable")
        page.close()

        # ---------- 5: placement on narrow / portrait ----------
        for label, vp in (("narrow landscape", NARROW), ("portrait", PORTRAIT)):
            page = browser.new_page(viewport=vp)
            page.goto(LOCAL.as_uri(), wait_until="load")
            page.wait_for_selector("#calculator")
            page.wait_for_timeout(400)
            reset(page)
            press(page, "tvm")
            page.wait_for_timeout(300)

            calc = rect(page, "#calculator")
            panel = rect(page, "#tvmPanel")
            print(f"\n=== {label} {vp['width']}x{vp['height']} ===")
            if panel is None:
                failures.append(f"{label}: panel did not open")
                print("  FAIL  panel did not open")
            elif panel["right"] <= calc["x"] + 0.5:
                failures.append(f"{label}: panel went beside the device, expected below")
                print("  FAIL  panel is beside the device, expected below it")
            elif panel["y"] >= calc["bottom"] - 0.5:
                print(f"  PASS  below the whole device "
                      f"(device ends {calc['bottom']:.0f}, panel starts {panel['y']:.0f})")
            else:
                failures.append(f"{label}: panel is inside the device, not below it")
                print(f"  FAIL  panel starts at y={panel['y']:.0f}, device spans "
                      f"{calc['y']:.0f}-{calc['bottom']:.0f} -- still inside the device")
            page.close()

        # ---------- 5: stacked panels must sit right under the keypad ----------
        # The device's box and its visible content are not the same thing: on a
        # phone the box is stretched to the viewport, so anything measured from
        # the box lands below ~190px of dead space. Measure from the keypad.
        print("\n=== a stacked panel is adjacent to the keypad ===")
        for label, vp in (("phone", {"width": 390, "height": 844}),
                          ("small phone", {"width": 320, "height": 568}),
                          ("narrow window", NARROW)):
            page = browser.new_page(viewport=vp)
            page.goto(LOCAL.as_uri(), wait_until="load")
            page.wait_for_selector("#calculator")
            page.wait_for_timeout(400)
            reset(page)
            press(page, "tvm")
            page.wait_for_timeout(350)
            bounds = page.evaluate(
                """() => {
                    const b = (s) => document.querySelector(s).getBoundingClientRect();
                    return {keypad: b('#keypad').bottom, panel: b('#tvmPanel').top};
                }"""
            )
            gap = bounds["panel"] - bounds["keypad"]
            if gap > GAP_LIMIT:
                failures.append(f"{label}: panel {gap:.0f}px below the keypad")
                print(f"  FAIL  {label}: {gap:.0f}px of dead space between keypad and panel")
            else:
                print(f"  PASS  {label}: panel {gap:.0f}px below the keypad")
            page.close()

        # ---------- 6: live resize across the threshold ----------
        page = browser.new_page(viewport=NARROW)
        page.goto(LOCAL.as_uri(), wait_until="load")
        page.wait_for_selector("#calculator")
        page.wait_for_timeout(400)
        press(page, "tvm")
        page.wait_for_timeout(300)

        print("\n=== live resize, no reload ===")
        seq = []
        for vp in (NARROW, WIDE, NARROW, PORTRAIT, WIDE):
            page.set_viewport_size(vp)
            page.wait_for_timeout(300)
            calc = rect(page, "#calculator")
            panel = rect(page, "#tvmPanel")
            if panel["right"] <= calc["x"] + 0.5:
                mode = "left"
            elif panel["y"] >= calc["bottom"] - 0.5:
                mode = "below"
            else:
                mode = "INSIDE"
            seq.append((f"{vp['width']}x{vp['height']}", mode))

        for size, mode in seq:
            print(f"  {size:>10}  -> {mode}")

        expected = ["below", "left", "below", "below", "left"]
        got = [m for _, m in seq]
        if got == expected:
            print("  PASS  placement switches live on resize, both directions")
        else:
            failures.append(f"live resize: got {got}, expected {expected}")
            print(f"  FAIL  expected {expected}")

        browser.close()

    print("\n" + "=" * 62)
    if failures:
        print("RESULT: FAILED -- " + "; ".join(failures))
        return 1
    print("RESULT: PASSED -- panels outside the device, stacked not overlapping, "
          "device pinned, live on resize")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
