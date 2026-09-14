#!/usr/bin/env python3
"""
Verification -- check_readme_links.py

Check that every in-document link and every relative file path in the prose
documents resolves.

This exists because the README's language toggle silently did nothing. The links
pointed at `#中文` and a hand-written guess at the English heading's slug, and
neither anchor existed -- the Chinese section is headed "BA II Plus 金融计算器 —
离线版", not "中文". Nothing catches that: a broken in-page link is not an error
in any renderer, it just does nothing when clicked.

Slugging is also renderer-specific. GitHub, VS Code and GitLab disagree about how
to slug a heading containing an em dash or CJK text, so a working `#heading-slug`
in one viewer can be dead in another. The documents therefore use explicit
`<a id="...">` anchors, which are identical everywhere, and this check verifies
each link finds one.

It also checks relative file paths written in the prose -- `docs/images/x.svg`,
`src/y.css` -- because a diagram that fails to render in a README looks like a
broken repository, and nothing else in the toolchain would notice.

Usage:
    python tools/check_readme_links.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", ROOT / "docs" / "ENGINEERING_GUIDE.md"]


def explicit_anchors(text: str) -> set[str]:
    """Anchors we control: <a id="foo"></a>."""
    return set(re.findall(r'<a\s+id="([^"]+)"', text))


def heading_slugs(text: str) -> set[str]:
    """
    Slugs a renderer would plausibly generate, as a fallback.

    Deliberately lenient -- lowercase, drop punctuation, spaces to hyphens -- so a
    link is accepted if any common renderer could resolve it, and this never
    fails a link that genuinely works somewhere.
    """
    slugs = set()
    for h in re.findall(r"(?m)^#{1,6}\s+(.+?)\s*$", text):
        slug = h.strip().lower()
        slug = re.sub(r"[^\w\s一-鿿-]", "", slug)
        slugs.add(re.sub(r"\s+", "-", slug))
    return slugs


def check_doc(path: Path) -> list[str]:
    """Return problem descriptions for one document; empty means it is clean."""
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(ROOT)
    known = explicit_anchors(text) | heading_slugs(text)
    problems: list[str] = []

    anchors = sorted(set(re.findall(r"\]\(#([^)]+)\)", text)))
    for a in anchors:
        if a not in known:
            problems.append(f"{rel}: #{a} resolves to nothing")

    # Relative paths in markdown links and images, e.g. [x](docs/images/y.svg)
    paths = sorted(set(re.findall(r"!?\[[^\]]*\]\((?!https?:|#|mailto:)([^)]+)\)", text)))
    images = set(re.findall(r"!\[[^\]]*\]\((?!#)([^)]+)\)", text))
    for ref in paths:
        target = (path.parent / ref.split("#")[0]).resolve()
        if not target.exists():
            kind = "image" if ref in images and not ref.startswith("http") else "file link"
            problems.append(f"{rel}: {kind} does not exist: {ref}")

    print(f"  {rel}: {len(anchors)} anchor link(s), {len(paths)} relative path(s)")
    return problems


def main() -> int:
    missing = [d for d in DOCS if not d.is_file()]
    if missing:
        print(f"ERROR: not found: {[str(m.relative_to(ROOT)) for m in missing]}", file=sys.stderr)
        return 1

    print("Checking in-document links and relative paths")
    problems: list[str] = []
    for doc in DOCS:
        problems += check_doc(doc)

    if problems:
        print()
        for p in problems:
            print(f"  FAIL  {p}", file=sys.stderr)
        print(f"\nFAILED: {len(problems)} unresolved reference(s)")
        return 1

    print("\nPASSED: every link, anchor and relative path resolves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
