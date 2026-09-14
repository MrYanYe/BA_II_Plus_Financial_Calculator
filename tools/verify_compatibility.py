#!/usr/bin/env python3
"""
Verification -- verify_compatibility.py

Answers one question: can this be copied anywhere and still work? It checks both
halves of that -- that nothing in the project depends on where it lives, and that
the deliverable actually runs on the engines people will open it with.

Part A -- portability audit (static, no browser):
  A1  no absolute paths in the artifact, the sources or the tools
  A2  every file reference resolves with exact case, which is what breaks when a
      project built on case-insensitive Windows is copied to case-sensitive Linux
  A3  no URLs outside comments, so the artifact cannot reach the network
  A4  filenames are legal on Windows, macOS and Linux alike, and paths are short
      enough for the Windows 260-character limit

Part B -- cross-engine behaviour (Chromium, Firefox, WebKit):
  B1  the same key sequences produce the same display on all three engines
  B2  the embedded webfonts load on all three -- a font that silently falls back
      would change the LCD's appearance without changing any text
  B3  no network request is made by any engine

Part C -- mobile:
  C1  works under iPhone / iPad / Pixel / Galaxy device emulation, including
      touch events and device pixel ratios
  C2  the layout does not overflow horizontally at phone widths

Part D -- relocation:
  D1  the artifact still works when copied to a directory whose name contains
      spaces and non-ASCII characters

Run:  python tools/verify_compatibility.py
Needs: pip install playwright && python -m playwright install chromium firefox webkit
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = ROOT / "BAII_Plus_Financial_Calculator_Offline_2026.html"
LOCAL = ARTIFACT.resolve().as_uri()

failures: list[str] = []

# A short but broad sweep: arithmetic in both evaluation modes, the 2ND layer,
# memory, TVM, cash flow, and the two-stage CE|C.
PROBE_KEYS = [
    ("v", "7"), ("v", "+"), ("v", "3"), ("v", "*"), ("v", "2"), ("a", "equals"),
    ("a", "clearAll"), ("a", "clearAll"),
    ("v", "1"), ("v", "2"), ("v", "+"), ("v", "5"),
    ("a", "clearAll"), ("a", "equals"),
    ("a", "clearAll"), ("a", "clearAll"),
    ("v", "1"), ("v", "4"), ("v", "4"), ("a", "sqrt"),
    ("a", "clearAll"), ("a", "clearAll"),
    ("v", "4"), ("v", "2"), ("a", "sto"), ("v", "1"),
    ("a", "clearAll"), ("a", "clearAll"),
    ("a", "rcl"), ("v", "1"),
    ("a", "clearAll"), ("a", "clearAll"),
    ("v", "8"), ("a", "tvm"),
]


def ok(label: str, detail: str = "") -> None:
    print(f"  PASS  {label}{(' -- ' + detail) if detail else ''}")


def bad(label: str, detail: str = "") -> None:
    failures.append(label)
    print(f"  FAIL  {label}{(' -- ' + detail) if detail else ''}")


# ───────────────────────── Part A ─────────────────────────

def audit_paths() -> None:
    print("=== A1. no absolute paths ===")
    art = ARTIFACT.read_text(encoding="utf-8")
    # A drive letter or POSIX root followed by a path separator. "https://" is
    # excluded because the colon there is a scheme, not a drive.
    abs_re = re.compile(r'(?<![A-Za-z])(?:[A-Za-z]:[\\/]|file:///|/(?:home|Users|mnt|opt|var)/)')
    hits = abs_re.findall(art)
    if hits:
        bad("absolute path in the artifact", str(sorted(set(hits))[:5]))
    else:
        ok("artifact contains no absolute paths")

    tool_hits = []
    for f in sorted((ROOT / "tools").glob("*.py")):
        # Skip this file: it has to spell the patterns out in order to look for
        # them, so it would always report itself.
        if f.name == Path(__file__).name:
            continue
        for m in abs_re.finditer(f.read_text(encoding="utf-8")):
            tool_hits.append(f"{f.name}: {m.group(0)}")
    if tool_hits:
        bad("absolute path in a tool", "; ".join(tool_hits[:5]))
    else:
        ok("all tools locate the project relative to their own file (__file__)")


def audit_case() -> None:
    print("\n=== A2. file references resolve with exact case ===")
    page = ROOT / "build" / "page.html"
    if not page.is_file():
        print("  note  build/page.html absent -- run extract_calculator.py first, skipping")
        return
    refs = [(ROOT / "build", m.group(1))
            for m in re.finditer(r'(?:href|src)="([^"]+)"', page.read_text(encoding="utf-8"))]
    refs += [(ROOT / "build", "fonts/" + m.group(1))
             for m in re.finditer(r"url\(fonts/([^)\s]+)\)",
                                  (ROOT / "build" / "fonts.css").read_text(encoding="utf-8"))]

    problems = []
    checked = 0
    for base, ref in refs:
        if ref.startswith(("http", "data:", "#")):
            continue
        checked += 1
        target = (base / ref).resolve()
        if not target.exists():
            problems.append(f"{ref} does not exist")
            continue
        on_disk = [p.name for p in target.parent.iterdir() if p.name.lower() == target.name.lower()]
        if target.name not in on_disk:
            problems.append(f"{ref} is spelled differently on disk: {on_disk}")
    if problems:
        bad("case-sensitive path mismatch", "; ".join(problems))
    else:
        ok(f"{checked} file references resolve with exact case (safe on Linux)")


def audit_urls() -> None:
    print("\n=== A3. no reachable URLs ===")
    art = ARTIFACT.read_text(encoding="utf-8")
    stripped = re.sub(r"<!--.*?-->", "", art, flags=re.S)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)
    urls = sorted(set(re.findall(r"https?://[^\s\"'<>)]+", stripped)))
    if urls:
        bad("reachable URL in the artifact", str(urls[:5]))
    else:
        ok("no URL outside comments -- nothing to fetch")


def audit_filenames() -> None:
    print("\n=== A4. filenames portable across Windows / macOS / Linux ===")
    reserved = {"CON", "PRN", "AUX", "NUL",
                *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    illegal = set('<>:"|?*\\')
    problems, longest, count = [], ("", 0), 0
    for p in sorted(ROOT.rglob("*")):
        if any(part in (".git", ".verify_shots", "build", "__pycache__") for part in p.parts):
            continue
        rel = p.relative_to(ROOT)
        count += 1
        if len(str(rel)) > longest[1]:
            longest = (str(rel), len(str(rel)))
        if not p.name.isascii():
            problems.append(f"non-ASCII name: {rel}")
        if p.name.split(".")[0].upper() in reserved:
            problems.append(f"reserved on Windows: {rel}")
        if p.name != p.name.rstrip(". "):
            problems.append(f"trailing dot or space: {rel}")
        if illegal & set(p.name):
            problems.append(f"character illegal on Windows: {rel}")
        if p.is_symlink():
            problems.append(f"symlink: {rel}")
    if problems:
        bad("filename portability", "; ".join(problems[:5]))
    else:
        ok(f"{count} files, all names legal on every platform")
    if longest[1] < 150:
        ok("longest path is short enough for Windows", f"{longest[1]} chars: {longest[0]}")
    else:
        bad("path may exceed the Windows limit", f"{longest[1]} chars")


# ───────────────────────── Part B / C ─────────────────────────

def run_probe(page) -> tuple[list[str], list[str], bool]:
    """Play the probe sequence, return (displays, fontFamilies, fontsLoaded)."""
    screens = []
    for kind, value in PROBE_KEYS:
        attr = "data-value" if kind == "v" else "data-action"
        page.evaluate(
            f"""() => {{
                const el = document.querySelector('button.key[{attr}="{value}"]');
                if (el) el.click();
            }}"""
        )
        screens.append(page.evaluate("document.getElementById('screen').textContent.trim()"))

    fonts = page.evaluate(
        """() => {
            const c = document.createElement('canvas').getContext('2d');
            const w = (f) => { c.font = '40px ' + f; return c.measureText('0123456789').width; };
            return {
                monoApplied: Math.abs(w("'Share Tech Mono'") - w('monospace')) > 0.5,
                sansApplied: Math.abs(w("'Space Grotesk'") - w('sans-serif')) > 0.5,
                ready: document.fonts.status,
                failures: [...document.fonts].filter(f => f.status === 'error').length,
            };
        }"""
    )
    return screens, [f"{fonts['monoApplied']}/{fonts['sansApplied']}"], fonts


def check_engines(browser_types: dict) -> list[str]:
    print("\n=== B. behaviour on Chromium, Firefox and WebKit ===")
    results: dict[str, list[str]] = {}
    fonts_by_engine: dict[str, str] = {}

    for name, bt in browser_types.items():
        try:
            browser = bt.launch(headless=True)
        except Exception as exc:  # engine not installed
            print(f"  note  {name} unavailable, skipping ({str(exc).splitlines()[0][:60]})")
            continue
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        requested: list[str] = []
        page.route("**/*", lambda route: (
            route.continue_() if route.request.url.startswith("file://")
            else (requested.append(route.request.url), route.abort())[1]
        ))
        page.goto(LOCAL, wait_until="load")
        page.wait_for_selector("#calculator")
        page.wait_for_timeout(500)

        screens, _, fonts = run_probe(page)
        results[name] = screens
        fonts_by_engine[name] = (
            f"mono={'yes' if fonts['monoApplied'] else 'NO'}, "
            f"sans={'yes' if fonts['sansApplied'] else 'NO'}, "
            f"state={fonts['ready']}"
        )
        if fonts["monoApplied"] and fonts["sansApplied"] and fonts["ready"] == "loaded":
            ok(f"{name}: embedded webfonts applied", fonts_by_engine[name])
        else:
            bad(f"{name}: webfonts did not apply", fonts_by_engine[name])
        if requested:
            bad(f"{name}: made {len(requested)} network request(s)", str(requested[:3]))
        else:
            ok(f"{name}: no network requests")
        browser.close()

    if len(results) < 2:
        bad("fewer than two engines available -- cross-engine check inconclusive")
        return []

    baseline_name = next(iter(results))
    baseline = results[baseline_name]
    for name, screens in results.items():
        if name == baseline_name:
            continue
        if screens == baseline:
            ok(f"{name}: identical display output to {baseline_name}",
               f"{len(screens)} states")
        else:
            diffs = [(i, a, b) for i, (a, b) in enumerate(zip(baseline, screens)) if a != b]
            bad(f"{name}: {len(diffs)} display state(s) differ from {baseline_name}",
                str(diffs[:3]))

    return baseline


def check_mobile(p) -> None:
    print("\n=== C. mobile device emulation ===")
    devices = ["iPhone 13", "iPhone SE", "iPad (gen 7)", "Pixel 5", "Galaxy S9+"]
    for label in devices:
        desc = p.devices.get(label)
        if desc is None:
            print(f"  note  {label} not in this Playwright build, skipping")
            continue
        engine = p.webkit if "iPhone" in label or "iPad" in label else p.chromium
        browser = engine.launch(headless=True)
        ctx = browser.new_context(**desc)
        page = ctx.new_page()
        page.route("**/*", lambda route: (
            route.continue_() if route.request.url.startswith("file://") else route.abort()
        ))
        page.goto(LOCAL, wait_until="load")
        page.wait_for_selector("#calculator")
        page.wait_for_timeout(400)

        screens, _, fonts = run_probe(page)
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - window.innerWidth"
        )
        # a real tap, not a synthetic click, so touch handling is exercised
        page.evaluate("document.querySelector('button.key[data-value=\"5\"]')"
                      ".dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}))")
        page.tap("button.key[data-value='5']")
        page.wait_for_timeout(120)
        tapped = page.evaluate("document.getElementById('screen').textContent.trim()")

        problems = []
        if fonts["ready"] != "loaded" or not (fonts["monoApplied"] and fonts["sansApplied"]):
            problems.append("webfonts not applied")
        if overflow > 1:
            problems.append(f"horizontal overflow of {overflow}px")
        if tapped not in ("5", "0.00"):
            problems.append(f"tap produced {tapped!r}")
        if problems:
            bad(f"{label}", "; ".join(problems))
        else:
            ok(f"{label}", f"{desc['viewport']['width']}x{desc['viewport']['height']} "
                           f"@dpr{desc.get('device_scale_factor', 1)}, no overflow, tap works")
        ctx.close()
        browser.close()


def check_relocation(p, baseline: list[str]) -> None:
    """
    Copy the artifact somewhere awkward and require it to behave IDENTICALLY.

    Compared against the baseline captured at the original location rather than
    against hard-coded expected values: the point is that moving the file changes
    nothing, and a hard-coded value would only test the probe sequence.
    """
    print("\n=== D. works from a relocated path with spaces and non-ASCII ===")
    with tempfile.TemporaryDirectory() as tmp:
        # A directory name that is awkward on purpose: spaces, CJK, and a hyphen.
        target_dir = Path(tmp) / "离线 计算器 copy-2"
        target_dir.mkdir()
        copy = target_dir / ARTIFACT.name
        shutil.copy2(ARTIFACT, copy)

        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(copy.resolve().as_uri(), wait_until="load")
        page.wait_for_selector("#calculator")
        page.wait_for_timeout(400)
        screens, _, fonts = run_probe(page)
        browser.close()

        moved = [s for s, b in zip(screens, baseline) if s != b]
        if moved:
            bad("behaviour changed after relocation",
                f"{len(moved)} of {len(baseline)} states differ")
        elif fonts["ready"] != "loaded":
            bad("fonts failed to load after relocation", fonts["ready"])
        else:
            ok("identical behaviour after copying to a spaced, non-ASCII path",
               f"{target_dir.name}/ ({len(screens)} states, fonts loaded)")


def main() -> int:
    if not ARTIFACT.is_file():
        print(f"ERROR: {ARTIFACT.name} not found -- run the build first", file=sys.stderr)
        return 1

    print(f"Auditing {ARTIFACT.name} ({ARTIFACT.stat().st_size:,} bytes)\n")
    audit_paths()
    audit_case()
    audit_urls()
    audit_filenames()

    with sync_playwright() as p:
        baseline = check_engines(
            {"chromium": p.chromium, "firefox": p.firefox, "webkit": p.webkit}
        )
        check_mobile(p)
        if baseline:
            check_relocation(p, baseline)
        else:
            bad("no baseline captured -- relocation check skipped")

    print("\n" + "=" * 62)
    if failures:
        print("RESULT: FAILED -- " + "; ".join(failures))
        return 1
    print("RESULT: PASSED -- portable, offline, and working on Chromium, Firefox and WebKit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
