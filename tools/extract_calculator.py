#!/usr/bin/env python3
"""
Step 2 of 3 -- extract_calculator.py

Turn the raw page mirror in upstream_raw/ into build/, the trimmed working set.

Two outputs:

  build/page.html   The page skeleton with ONLY the calculator widget kept.
                    Everything else -- navbar, the "other calculators" carousel,
                    the marketing sections, the footer, and the ad/analytics
                    tags -- is dropped. The widget markup itself is copied
                    byte-for-byte from upstream; nothing inside it is rewritten.

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


def extract_element(html: str, element_id: str) -> str | None:
    """
    Return the full outer HTML of <div id="..."> including its closing tag.

    Uses div-depth counting rather than a regex so nested divs come out intact.
    """
    start = re.search(rf'<div[^>]*\bid="{re.escape(element_id)}"', html)
    if not start:
        return None

    depth = 0
    for m in re.finditer(r"<(/?)div\b[^>]*>", html[start.start():]):
        depth += 1 if m.group(1) == "" else -1
        if depth == 0:
            return html[start.start():start.start() + m.end()]
    return None


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
    print(f"Extracted <div id={WIDGET_ID!r}>  ({styled(widget)} chars)")

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
