#!/usr/bin/env python3
"""
Step 2 of 3 -- extract_calculator.py

Turn the raw page mirror in upstream_raw/ into build/, the trimmed working set.

Two outputs:

  build/page.html   The page skeleton with ONLY the calculator widget kept.
                    Everything else -- navbar, the "other calculators" carousel,
                    the marketing sections, the footer, and the ad/analytics
                    tags -- is dropped. The widget markup is copied byte-for-byte
                    from upstream with exactly one addition: a .panel-dock div
                    wrapped around the three worksheet panels so they stack
                    instead of overlapping. See wrap_panels() for why.

  build/fonts.css   The Google Fonts @font-face sheet, rewritten to point at the
                    local woff2 copies in build/fonts/ instead of fonts.gstatic.com.

Everything under build/ is generated and disposable -- wipe it and re-run. The
only hand-authored file this project has is src/offline_overrides.css, which
lives outside build/ for exactly that reason. build/page.html links it by
relative path, so the page still opens directly in a browser for development:

    build/page.html   the assembled page
    src/              hand-written source (offline_overrides.css)
    upstream_raw/     the mirror everything above is generated from

build_single_file.py then collapses the four stylesheets and scripts into one
self-contained document.

A coverage check runs at the end: every element id that script.js looks up must
exist in the extracted markup, or the build fails loudly.

Usage:
    python tools/extract_calculator.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "upstream_raw"
SRC = ROOT / "src"    # hand-authored only: offline_overrides.css
BUILD = ROOT / "build"  # generated; disposable, regenerated from RAW by this script

# The widget we keep. Everything outside this element is discarded.
WIDGET_ID = "calculator"


def styled(text: str) -> str:
    """Format a byte/char count for the log lines."""
    return f"{len(text):,}"


def element_span(html: str, element_id: str) -> tuple[int, int] | None:
    """
    Return the (start, end) span of the element carrying the given id.

    Matches whatever tag the element uses and counts depth of that same tag, so
    nested elements of the same type come out intact. Regex alone would stop at
    the first inner closing tag.
    """
    m = re.search(rf'<(\w+)[^>]*\bid="{re.escape(element_id)}"', html)
    if not m:
        return None
    tag = m.group(1)

    depth = 0
    for mm in re.finditer(rf"<(/?){tag}\b[^>]*>", html[m.start():]):
        depth += 1 if mm.group(1) == "" else -1
        if depth == 0:
            return (m.start(), m.start() + mm.end())
    return None


def extract_element(html: str, element_id: str) -> str | None:
    """Return the full outer HTML of the element with the given id."""
    span = element_span(html, element_id)
    return html[span[0]:span[1]] if span else None


def wrap_panels(widget: str) -> str:
    """
    Wrap the three worksheet panels in a single .panel-dock container.

    This is the ONLY structural change made to the widget, and it exists for one
    reason: the panels do not reliably hide each other. openTVM() and openCF()
    each hide the other two, but openRegOverlay() hides nothing -- so pressing
    N and then STO leaves the TVM worksheet and the register overlay open at the
    same time. Upstream that is harmless, because both sit in normal flow and
    simply stack vertically inside the device. Once they are positioned, two
    open panels would land in the same place and overlap.

    Giving them a shared column means they stack, in DOM order, exactly as they
    do upstream -- without touching script.js, which continues to show and hide
    the same panels in the same circumstances.

    The panels are contiguous siblings between the display and the keypad, which
    is asserted below so a future upstream reshuffle fails loudly instead of
    silently wrapping the wrong run of markup.
    """
    first = element_span(widget, "tvmPanel")
    last = element_span(widget, "registerOverlay")
    if not first or not last:
        raise SystemExit("ERROR: could not locate the worksheet panels to wrap")

    between = widget[first[0]:last[1]]
    # Only the three known panels may sit in the wrapped run.
    for other in ("tvmPanel", "cfPanel", "registerOverlay"):
        if f'id="{other}"' not in between:
            raise SystemExit(f"ERROR: #{other} is not inside the panel run")
    for stray in ("id=\"display\"", "id=\"keypad\""):
        if stray in between:
            raise SystemExit(f"ERROR: {stray} found inside the panel run -- layout changed")

    return (
        widget[:first[0]]
        + '<div class="panel-dock">\n'
        + between
        + "\n</div>"
        + widget[last[1]:]
    )


def build_fonts_css() -> str:
    """
    Rewrite Google's @font-face sheet to reference local woff2 files, collapsing
    the per-weight duplicates.

    Google answers a `wght@400;500;600;700` request with one @font-face block per
    subset *per weight* -- 13 blocks pointing at only 4 files. Space Grotesk is a
    variable font (fvar axis wght=300-700), so all four of its weight blocks per
    subset are the same binary. Inlining them as served would embed each file
    four times over (~205KB of base64 instead of ~61KB).

    So blocks are grouped by (family, style, src, unicode-range, font-display) and
    merged into a single face spanning `font-weight: <min> <max>`. That is the
    correct declaration for a variable font and behaves identically: a request
    for a weight in range resolves to the same instance, and a request above 700
    clamps to 700 either way -- exactly as it does on the live site.

    The local filenames mirror fetch_upstream.py's naming scheme.
    """
    sheet = (RAW / "google-fonts.css").read_text(encoding="utf-8")

    blocks = re.findall(r"@font-face\s*\{(.*?)\}", sheet, re.S)
    if not blocks:
        raise SystemExit("ERROR: no @font-face blocks in upstream_raw/google-fonts.css")

    def field(body: str, prop: str) -> str:
        m = re.search(rf"{prop}:\s*([^;]+);", body)
        if not m:
            raise SystemExit(f"ERROR: @font-face block has no {prop}")
        return m.group(1).strip()

    groups: dict[tuple[str, ...], dict] = {}
    order: list[tuple[str, ...]] = []

    for body in blocks:
        url = re.search(r"url\(([^)]+)\)", body).group(1)
        key = (
            field(body, "font-family"),
            field(body, "font-style"),
            url,
            field(body, "unicode-range"),
            field(body, "font-display"),
        )
        if key not in groups:
            groups[key] = {"weights": [], "body": body}
            order.append(key)
        groups[key]["weights"].append(field(body, "font-weight"))

    # Unused-font guard: warn if a merged face references a file we did not fetch.
    out: list[str] = []
    for key in order:
        family, style, url, urange, display = key
        name = url.rsplit("/", 1)[-1]
        slug = "space-grotesk" if "spacegrotesk" in url else "share-tech-mono"
        local = f"{slug}__{name}"
        if not (RAW / "fonts" / local).is_file():
            raise SystemExit(f"ERROR: font file missing for {url} -- run fetch_upstream.py first")

        weights = groups[key]["weights"]
        w_min, w_max = min(weights), max(weights)
        weight_decl = w_min if w_min == w_max else f"{w_min} {w_max}"

        out.append(
            "@font-face {\n"
            f"  font-family: {family};\n"
            f"  font-style: {style};\n"
            f"  font-weight: {weight_decl};\n"
            f"  font-display: {display};\n"
            f"  src: url(fonts/{local}) format('woff2');\n"
            f"  unicode-range: {urange};\n"
            "}"
        )

    header = (
        "/* Webfont faces for the offline calculator.\n"
        " * Generated by tools/extract_calculator.py from upstream_raw/google-fonts.css.\n"
        " * url() targets rewritten from fonts.gstatic.com to local files.\n"
        " * Subsetting (unicode-range) is exactly as Google served it.\n"
        " * Per-weight blocks for the same file are merged into one variable-font\n"
        " * face with a weight range -- see build_fonts_css() for why.\n"
        " * Do not hand-edit -- re-run the extractor instead.\n"
        " */\n\n"
    )
    return header + "\n\n".join(out) + "\n"


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
<title>BA II Plus Financial Calculator &mdash; Offline</title>

<!-- Webfont faces, self-hosted. Inlined at build time. -->
<link rel="stylesheet" href="fonts.css" />
<!-- Upstream stylesheet, copied verbatim from baiiplusfinancialcalculator.com -->
<link rel="stylesheet" href="styles.css" />
<!-- Centring + any other offline-only adjustments. See the file for rationale. -->
<link rel="stylesheet" href="../src/offline_overrides.css" />
</head>
<body>

<!--
  Only the calculator widget survives from the original page. The upstream
  wrappers .app-shell > .hero-area are kept because the stylesheet uses them for
  the dark radial background and the centring; everything that used to follow
  the widget (carousel, marketing sections, footer) has been removed.
-->
<div class="app-shell">
  <section class="hero-area" id="hero">

{calculator}

  </section>
</div>

<!-- Upstream calculator logic, copied verbatim. Fully client-side: no fetch,
     no XHR, no storage, no imports. Inlined at build time. -->
<script src="script.js"></script>
</body>
</html>
"""


def main() -> int:
    index = (RAW / "index.html").read_text(encoding="utf-8")

    widget = extract_element(index, WIDGET_ID)
    if not widget:
        print(f"ERROR: <div id={WIDGET_ID!r}> not found in upstream_raw/index.html", file=sys.stderr)
        return 1
    before = len(widget)
    widget = wrap_panels(widget)
    print(f"Extracted <div id={WIDGET_ID!r}>  ({styled(widget)} chars, "
          f"+{len(widget) - before} for the panel dock)")

    # --- coverage check ---------------------------------------------------
    # script.js resolves its UI through getElementById. If the trim dropped any
    # node it needs, the calculator would silently break at runtime, so verify
    # the mapping here instead.
    script = (RAW / "script.js").read_text(encoding="utf-8")
    needed = sorted(set(re.findall(r'getElementById\("([^"]+)"\)', script)))
    missing = [i for i in needed if f'id="{i}"' not in widget]
    # The carousel is legitimately absent: script.js guards those lookups.
    carousel = {"calcSliderTrack", "calcSliderPrev", "calcSliderNext"}

    print(f"script.js needs {len(needed)} element ids")
    if missing:
        unexpected = [i for i in missing if i not in carousel]
        for i in sorted(carousel & set(missing)):
            print(f"  ok     #{i} absent (carousel, null-guarded in script.js)")
        if unexpected:
            print(f"ERROR: extracted widget is missing required ids: {unexpected}", file=sys.stderr)
            return 1
        print("  all required ids present")

    # --- write ------------------------------------------------------------
    BUILD.mkdir(parents=True, exist_ok=True)

    # styles.css and script.js are carried over untouched -- they are the same
    # bytes the live site serves. Re-copied on every run so build/ can never
    # drift away from the mirror.
    for name in ("styles.css", "script.js"):
        data = (RAW / name).read_bytes()
        (BUILD / name).write_bytes(data)
        print(f"  copy   build/{name}  ({len(data):,} bytes, verbatim)")

    # Fonts are copied across so build/page.html is a complete, self-sufficient
    # page that can be opened directly in a browser for development.
    font_src = RAW / "fonts"
    (BUILD / "fonts").mkdir(parents=True, exist_ok=True)
    copied = 0
    for font in sorted(font_src.glob("*.woff2")):
        (BUILD / "fonts" / font.name).write_bytes(font.read_bytes())
        copied += font.stat().st_size
    print(f"  copy   build/fonts/*.woff2  ({copied:,} bytes, {len(list(font_src.glob('*.woff2')))} files)")

    # newline="\n" throughout: the write_text default translates \n to
    # os.linesep, which would give these files CRLF on Windows and LF on Linux
    # and make the build output depend on the host platform.
    page = PAGE_TEMPLATE.replace("{calculator}", widget)
    (BUILD / "page.html").write_text(page, encoding="utf-8", newline="\n")
    print(f"  write  build/page.html  ({styled(page)} chars)")

    fonts_css = build_fonts_css()
    (BUILD / "fonts.css").write_text(fonts_css, encoding="utf-8", newline="\n")
    print(f"  write  build/fonts.css  ({styled(fonts_css)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
