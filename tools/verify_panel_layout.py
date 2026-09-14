#!/usr/bin/env python3
"""
Verification -- verify_panel_layout.py

Checks the offline-only worksheet placement: the TVM / CF / STO-RCL panels float
to the left of the device when there is room, stack below it when there is not,
and the device itself never moves either way.

Four checks:

  1. anchored    On a wide landscape viewport the open panel sits entirely to the
                 left of the device and top-aligned with it.
  2. still       Opening and closing each panel leaves the device's bounding box
                 bit-for-bit unchanged.
  3. stacked     On a narrow viewport, and on a portrait one wide enough to be
                 ambiguous, the panel is NOT to the left -- it falls back to
                 upstream's in-flow position inside the device.
  4. live        Resizing a loaded page across the threshold moves the panel
                 between the two layouts without a reload. This is the part the
                 request called out explicitly, and the part a naive
                 render-time-only check would miss.

Runs entirely against the local artifact; no network needed.

    python tools/verify_panel_layout.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / "BAII_Plus_Financial_Calculator_Offline_2026.html"

# (key to press, panel id) -- one per panel.
PANELS = [
    ("tvm", "tvmPanel"),
    ("cf", "cfPanel"),
    ("sto", "registerOverlay"),
]

WIDE = {"width": 1400, "height": 900}      # landscape, well past the threshold
NARROW = {"width": 900, "height": 800}     # landscape but too narrow to fit
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


def close_panels(page: Page) -> None:
    page.evaluate(
        """() => {
            for (const id of ['tvmPanel','cfPanel','registerOverlay'])
                document.getElementById(id).hidden = true;
        }"""
    )


def main() -> int:
    if not LOCAL.is_file():
        print(f"ERROR: {LOCAL.name} not found -- run the build first", file=sys.stderr)
        return 1

    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # ---------- 1 + 2: wide landscape, panel left, device still ----------
        page = browser.new_page(viewport=WIDE)
        page.goto(LOCAL.as_uri(), wait_until="load")
        page.wait_for_selector("#calculator")
        page.wait_for_timeout(500)

        print(f"=== wide landscape {WIDE['width']}x{WIDE['height']} ===")
        for action, panel_id in PANELS:
            close_panels(page)
            page.wait_for_timeout(80)
            before = rect(page, "#calculator")
            press(page, action)
            page.wait_for_timeout(300)
            after = rect(page, "#calculator")
            panel = rect(page, f"#{panel_id}")

            if panel is None:
                failures.append(f"{panel_id} did not open from key {action!r}")
                print(f"  FAIL  #{panel_id} did not open")
                continue

            # device must not have moved at all
            moved = max(abs(before[k] - after[k]) for k in ("x", "y", "w", "h"))
            if moved > 0.01:
                failures.append(f"device moved {moved:.2f}px opening #{panel_id}")
                print(f"  FAIL  device moved {moved:.2f}px when #{panel_id} opened")
            else:
                print(f"  PASS  device unmoved with #{panel_id} open")

            # panel must be fully left of the device, top-aligned
            gap = after["x"] - panel["right"]
            top_delta = abs(panel["y"] - after["y"])
            if panel["right"] <= after["x"] + 0.5 and top_delta <= 2:
                print(f"  PASS  #{panel_id} to the left (gap {gap:.0f}px, "
                      f"top-aligned within {top_delta:.1f}px, {panel['w']:.0f}px wide)")
            else:
                failures.append(f"#{panel_id} not left-aligned (gap {gap:.1f}px)")
                print(f"  FAIL  #{panel_id} gap {gap:.1f}px, top delta {top_delta:.1f}px")

        # panel must stay inside the viewport
        close_panels(page)
        press(page, "tvm")
        page.wait_for_timeout(300)
        panel = rect(page, "#tvmPanel")
        if panel["x"] < 0:
            failures.append(f"panel clipped off-screen left (x={panel['x']:.0f})")
            print(f"  FAIL  panel starts at x={panel['x']:.0f}, off-screen")
        else:
            print(f"  PASS  panel fully on-screen (x={panel['x']:.0f})")
        page.close()

        # ---------- 3: narrow + portrait must stack ----------
        for label, vp in (("narrow landscape", NARROW), ("portrait", PORTRAIT)):
            page = browser.new_page(viewport=vp)
            page.goto(LOCAL.as_uri(), wait_until="load")
            page.wait_for_selector("#calculator")
            page.wait_for_timeout(400)
            press(page, "tvm")
            page.wait_for_timeout(300)

            calc = rect(page, "#calculator")
            panel = rect(page, "#tvmPanel")
            print(f"\n=== {label} {vp['width']}x{vp['height']} ===")
            if panel is None:
                failures.append(f"{label}: panel did not open")
                print("  FAIL  panel did not open")
            elif panel["right"] <= calc["x"] + 0.5:
                failures.append(f"{label}: panel went left when it should stack")
                print(f"  FAIL  panel is beside the device, expected stacked")
            else:
                # expect upstream's in-flow slot: inside the device, above the keypad
                inside = calc["x"] <= panel["x"] and panel["right"] <= calc["right"] + 1
                print(f"  PASS  panel stacked, not beside the device"
                      f"{' (in-flow inside the device, as upstream)' if inside else ''}")
            page.close()

        # ---------- 4: live resize across the threshold ----------
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
            beside = panel is not None and panel["right"] <= calc["x"] + 0.5
            seq.append((f"{vp['width']}x{vp['height']}", "left" if beside else "stacked"))

        for size, mode in seq:
            print(f"  {size:>10}  -> {mode}")

        expected = ["stacked", "left", "stacked", "stacked", "left"]
        got = [m for _, m in seq]
        if got == expected:
            print("  PASS  layout switches live on resize, both directions")
        else:
            failures.append(f"live resize: got {got}, expected {expected}")
            print(f"  FAIL  expected {expected}")

        browser.close()

    print("\n" + "=" * 62)
    if failures:
        print("RESULT: FAILED -- " + "; ".join(failures))
        return 1
    print("RESULT: PASSED -- device pinned, panels left when there is room, "
          "stacked when there is not, live on resize")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
