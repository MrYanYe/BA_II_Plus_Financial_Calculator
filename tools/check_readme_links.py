#!/usr/bin/env python3
"""
Verification -- check_readme_links.py

Check that every in-document link in README.md resolves to an anchor that
actually exists.

This exists because the README's language toggle silently did nothing. The links
pointed at `#中文` and a hand-written guess at the English heading's slug, and
neither anchor existed -- the Chinese section is headed "BA II Plus 金融计算器 —
离线版", not "中文". Nothing catches that: a broken in-page link is not an error
in any renderer, it just does nothing when clicked.

Slugging is also renderer-specific. GitHub, VS Code and GitLab disagree about how
to slug a heading containing an em dash or CJK text, so a working `#heading-slug`
in one viewer can be dead in another. The README therefore uses explicit
`<a id="...">` anchors, which are identical everywhere, and this check verifies
each link finds one.

Usage:
    python tools/check_readme_links.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"


def main() -> int:
    if not README.is_file():
        print(f"ERROR: {README.name} not found", file=sys.stderr)
        return 1

    text = README.read_text(encoding="utf-8")

    # Explicit anchors we control: <a id="foo"></a>
    explicit = set(re.findall(r'<a\s+id="([^"]+)"', text))

    # Heading slugs a renderer would generate, as a fallback. Deliberately
    # cautious -- lowercase, drop punctuation, spaces to hyphens -- so this
    # accepts a link only if some renderer could plausibly resolve it.
    headings = set()
    for h in re.findall(r"(?m)^#{1,6}\s+(.+?)\s*$", text):
        slug = h.strip().lower()
        slug = re.sub(r"[^\w\s一-鿿-]", "", slug)
        slug = re.sub(r"\s+", "-", slug)
        headings.add(slug)

    links = re.findall(r"\]\(#([^)]+)\)", text)
    if not links:
        print("WARNING: no in-document links found -- did the README change format?")
        return 0

    known = explicit | headings
    broken = [l for l in links if l not in known]

    print(f"README.md: {len(links)} in-document link(s), "
          f"{len(explicit)} explicit anchor(s)")
    for link in sorted(set(links)):
        status = "ok " if link in known else "DEAD"
        source = "explicit" if link in explicit else ("heading slug" if link in headings else "-")
        print(f"  {status}  #{link:<20} ({source})")

    if broken:
        print(f"\nFAILED: {len(broken)} link(s) resolve to nothing: {broken}", file=sys.stderr)
        return 1

    print("\nPASSED: every in-document link resolves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
