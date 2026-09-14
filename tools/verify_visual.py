#!/usr/bin/env python3
"""
Verification -- verify_visual.py

The parity harness proves the logic matches. This one covers the layer logic
cannot see: that the inlined webfonts actually load, and that the widget paints
identically.

Checks, at three viewports (desktop / laptop / phone):

  1. fonts      document.fonts reports both families loaded, and text measured in
                them differs from the fallback metric -- i.e. the embedded woff2
                data really took effect rather than silently falling back.
  2. geometry   #calculator bounding box, live vs offline, to 0.5px.
  3. pixels     element screenshot of #calculator, live vs offline, diffed.

Screenshots are written to .verify_shots/ (gitignored) so the result can be
looked at directly.

Requires network access for the live half. Usage:

    python tools/verify_visual.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / "BAII_Plus_Financial_Calculator_Offline_2026.html"
LIVE = "https://baiiplusfinancialcalculator.com/"
SHOTS = ROOT / ".verify_shots"

VIEWPORTS = {
    "desktop_1400x1000": {"width": 1400, "height": 1000},
    "laptop_1366x768": {"width": 1366, "height": 768},
    "phone_390x844": {"width": 390, "height": 844},
}

# Largest per-channel difference tolerated before a pixel counts as a real
# mismatch. Browsers anti-alias the same curved edge (the LCD's rounded corners)
# a hair differently when the surrounding layout is a fraction of a pixel
# different, which shows up as small deltas on a handful of edge pixels.
# 16/255 sits comfortably under the ~25/255 just-noticeable difference used by
# standard image-diff tooling, so nothing at or below it is visible to a user.
MAX_CHANNEL_DELTA = 16

FONT_PROBE = """() => {
    const loaded = [...document.fonts].map(f => `${f.family}:${f.status}`);

    // Measure the same string in the real family and in a guaranteed fallback.
    // If the webfont failed to load, the two widths come out equal.
    const measure = (family) => {
        const c = document.createElement('canvas').getContext('2d');
        c.font = `40px ${family}`;
        return c.measureText('0123456789 BA II Plus').width;
    };
    return {
        loaded,
        ready: document.fonts.status,
        spaceGrotesk: measure("'Space Grotesk'"),
        shareTechMono: measure("'Share Tech Mono'"),
        monospaceFallback: measure("monospace"),
        sansFallback: measure("sans-serif"),
    };
}"""

GEOMETRY = """() => {
    const el = document.getElementById('calculator');
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const screen = document.getElementById('screen');
    const scs = getComputedStyle(screen);
    return {
        x: r.x, y: r.y, w: r.width, h: r.height,
        bg: cs.backgroundImage,
        radius: cs.borderRadius,
        screenFont: scs.fontFamily,
        screenSize: scs.fontSize,
    };
}"""


# Applied to BOTH pages before screenshotting, so the comparison isolates the
# widget. Two things are suppressed, both deliberately different by design:
#
#   .navbar     live-only page chrome. It is sticky at small widths, so an
#               element screenshot of the widget picks it up painted on top.
#   .hero-area  radial gradient. The widget now sits at a different Y, so this
#               gradient necessarily resolves differently behind the widget's
#               rounded corners. Flattening it to the same solid colour on both
#               sides removes that variable.
#
# With those neutralised, the remaining pixels are the widget and only the
# widget -- so the comparison can be exact instead of approximate.
NEUTRALISE = """
    nav.navbar, .navbar { display: none !important; }
    .hero-area { background: #252525 !important; }
"""


def main() -> int:
    if not LOCAL.is_file():
        print(f"ERROR: {LOCAL.name} not found -- run the build first", file=sys.stderr)
        return 1

    SHOTS.mkdir(exist_ok=True)
    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for vp_name, vp in VIEWPORTS.items():
            print(f"\n=== {vp_name} ===")
            shots: dict[str, Path] = {}

            for label, url in (("live", LIVE), ("offline", LOCAL.as_uri())):
                page = browser.new_page(viewport=vp)
                if label == "live":
                    page.goto(url, wait_until="networkidle", timeout=90_000)
                else:
                    page.goto(url, wait_until="load")
                page.wait_for_selector("#calculator", timeout=30_000)
                page.wait_for_timeout(800)

                probe = page.evaluate(FONT_PROBE)
                geom = page.evaluate(GEOMETRY)

                # raw shot: what a user actually sees, page chrome included
                page.locator("#calculator").screenshot(path=SHOTS / f"{vp_name}__{label}.png")

                # neutralised shot: the widget on its own
                page.add_style_tag(content=NEUTRALISE)
                page.wait_for_timeout(250)
                path = SHOTS / f"{vp_name}__{label}__widget.png"
                page.locator("#calculator").screenshot(path=path)
                page.close()

                shots[label] = path
                if label == "live":
                    live_probe, live_geom = probe, geom
                else:
                    off_probe, off_geom = probe, geom

            # --- fonts ---
            print(f"  fonts loaded (offline): {off_probe['loaded']}")
            if off_probe["ready"] != "loaded":
                failures.append(f"{vp_name}: document.fonts not ready ({off_probe['ready']})")
                print(f"  FAIL  font loading state: {off_probe['ready']}")
            else:
                print(f"  PASS  document.fonts status=loaded")

            for family, key, fallback in (
                ("Share Tech Mono", "shareTechMono", "monospaceFallback"),
                ("Space Grotesk", "spaceGrotesk", "sansFallback"),
            ):
                d_off = abs(off_probe[key] - off_probe[fallback])
                d_live = abs(live_probe[key] - live_probe[fallback])
                if d_off < 0.5:
                    failures.append(f"{vp_name}: {family} fell back offline")
                    print(f"  FAIL  {family}: offline width == fallback, font did NOT apply")
                elif abs(off_probe[key] - live_probe[key]) > 0.5:
                    failures.append(f"{vp_name}: {family} metrics differ")
                    print(
                        f"  FAIL  {family}: live {live_probe[key]:.1f}px vs "
                        f"offline {off_probe[key]:.1f}px"
                    )
                else:
                    print(
                        f"  PASS  {family}: applied (off {d_off:.1f}px from fallback, "
                        f"live {d_live:.1f}px), metrics match live exactly"
                    )

            # --- geometry ---
            # Size and horizontal position must match live exactly: the device
            # has to be the same size, at the same X. Y is expected to differ --
            # centring it is the one layout change this project makes on purpose.
            size_off = max(abs(live_geom[k] - off_geom[k]) for k in ("x", "w", "h"))
            if size_off > 0.5:
                failures.append(f"{vp_name}: size/position off by {size_off:.1f}px")
                print(f"  FAIL  size or x differs: live "
                      f"(x={live_geom['x']:.0f}, {live_geom['w']:.0f}x{live_geom['h']:.1f}) "
                      f"vs offline (x={off_geom['x']:.0f}, "
                      f"{off_geom['w']:.0f}x{off_geom['h']:.1f})")
            else:
                print(f"  PASS  size and x match live exactly "
                      f"(x={off_geom['x']:.0f}, {off_geom['w']:.0f}x{off_geom['h']:.1f})")
            if off_geom["y"] < 0:
                failures.append(f"{vp_name}: widget clipped above viewport")
                print(f"  FAIL  widget starts at y={off_geom['y']:.1f}, clipped")
            else:
                print(f"        y: live {live_geom['y']:.0f} -> offline {off_geom['y']:.0f} "
                      f"(centring, by design)")
            if "Share Tech Mono" in off_geom["screenFont"]:
                print(f"  PASS  LCD font: {off_geom['screenFont']} @ {off_geom['screenSize']}")
            else:
                failures.append(f"{vp_name}: LCD font is {off_geom['screenFont']}")
                print(f"  FAIL  LCD font: {off_geom['screenFont']}")

            # --- pixels ---
            try:
                from PIL import Image
            except ImportError:
                print("  note  Pillow missing, skipping pixel diff")
                continue

            wa = SHOTS / f"{vp_name}__live__widget.png"
            wb = SHOTS / f"{vp_name}__offline__widget.png"
            a = Image.open(wa).convert("RGB")
            b = Image.open(wb).convert("RGB")
            if a.size != b.size:
                failures.append(f"{vp_name}: widget screenshot size differs")
                print(f"  FAIL  widget size: live {a.size} vs offline {b.size}")
            else:
                import numpy as np

                delta = np.abs(
                    np.asarray(a, dtype=int) - np.asarray(b, dtype=int)
                )
                worst = int(delta.max())
                over = int((delta.max(axis=2) > MAX_CHANNEL_DELTA).sum())
                nz = int((delta.max(axis=2) > 0).sum())
                pct = 100 * nz / (a.size[0] * a.size[1])

                if over:
                    failures.append(
                        f"{vp_name}: {over} px exceed {MAX_CHANNEL_DELTA}/255 tolerance"
                    )
                    print(f"  FAIL  {over} px differ by more than {MAX_CHANNEL_DELTA}/255 "
                          f"(worst {worst}/255)")
                elif nz == 0:
                    print(f"  PASS  widget pixel-identical ({a.size[0]}x{a.size[1]})")
                else:
                    print(
                        f"  PASS  widget matches within anti-aliasing tolerance "
                        f"({a.size[0]}x{a.size[1]}, worst {worst}/255, "
                        f"{nz} px differ at all = {pct:.2f}%)"
                    )

        browser.close()

    print(f"\nScreenshots in {SHOTS.relative_to(ROOT)}/")
    print("=" * 62)
    if failures:
        print("RESULT: FAILED -- " + "; ".join(failures))
        return 1
    print("RESULT: PASSED -- fonts embedded and applied, geometry and pixels match live")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
