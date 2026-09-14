#!/usr/bin/env python3
"""
Step 1 of 3 -- fetch_upstream.py

Download the raw assets of https://baiiplusfinancialcalculator.com/ into
upstream_raw/ so the rest of the pipeline has a pristine, offline copy to work
from. Nothing is modified here; this is a faithful mirror of the live files.

Grabbed:
    index.html          the calculator page
    styles.css          versioned URL, resolved from index.html
    script.js           versioned URL, resolved from index.html
    google-fonts.css    the @font-face sheet served to a modern browser
    fonts/*.woff2       the actual font binaries referenced by that sheet

Usage:
    python tools/fetch_upstream.py            # skip files already downloaded
    python tools/fetch_upstream.py --force    # re-download everything
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.request
from pathlib import Path

SITE = "https://baiiplusfinancialcalculator.com/"
# A current desktop Chrome UA. Google Fonts serves woff2 only to browsers it
# recognises; the default python-urllib agent gets a legacy ttf sheet instead.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "upstream_raw"
FONTS = RAW / "fonts"


def fetch(url: str, *, attempts: int = 4) -> bytes:
    """
    GET a URL and return the body, following redirects.

    Retries on connection errors. gstatic in particular drops a connection
    mid-handshake often enough that a single attempt is not reliable.
    """
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - any transport error is retryable
            last = exc
            if attempt < attempts:
                wait = 2 ** (attempt - 1)
                print(f"  retry  {attempt}/{attempts - 1} in {wait}s -- {exc}")
                time.sleep(wait)
    raise RuntimeError(f"failed to fetch {url} after {attempts} attempts") from last


def save(path: Path, data: bytes, *, force: bool) -> None:
    """Write bytes to path unless it already exists and force is off."""
    if path.exists() and not force:
        print(f"  skip   {path.relative_to(ROOT)}  ({path.stat().st_size:,} bytes, exists)")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    print(f"  write  {path.relative_to(ROOT)}  ({len(data):,} bytes)")


def asset_url(index_html: str, needle: str) -> str | None:
    """
    Pull an asset URL out of index.html, keeping the ?v= cache-busting query.

    The site links assets as "styles.css?v=20260909". We match on the file stem
    so a future version bump is picked up automatically instead of silently
    serving a stale copy.
    """
    pattern = rf'(?:href|src)="([^"]*{re.escape(needle)}[^"]*)"'
    m = re.search(pattern, index_html)
    if not m:
        return None
    url = m.group(1).replace("&amp;", "&")
    return url if url.startswith("http") else SITE + url.lstrip("/")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-download existing files")
    args = ap.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    print(f"Fetching {SITE}\n")

    # --- the page itself -------------------------------------------------
    print("index.html")
    html = fetch(SITE).decode("utf-8")
    save(RAW / "index.html", html.encode("utf-8"), force=args.force)

    # --- stylesheet and script, versioned URLs resolved from the page ----
    print("\ncss / js")
    for needle, out in (("styles.css", "styles.css"), ("script.js", "script.js")):
        url = asset_url(html, needle) or (SITE + needle)
        print(f"  url    {url}")
        save(RAW / out, fetch(url), force=args.force)

    # --- site logo, used only as the source for the embedded favicon -------
    print("\nimages")
    url = asset_url(html, "logo.png") or (SITE + "logo.png")
    print(f"  url    {url}")
    save(RAW / "logo.png", fetch(url), force=args.force)

    # --- webfonts --------------------------------------------------------
    # The page links the Google Fonts sheet rather than hosting fonts itself,
    # so an offline copy has to carry the font binaries inline.
    print("\nwebfonts")
    gf_link = re.search(r'https://fonts\.googleapis\.com/css2[^"]*', html)
    if not gf_link:
        print("  ERROR: no Google Fonts stylesheet link found in index.html", file=sys.stderr)
        return 1
    gf_url = gf_link.group(0).replace("&amp;", "&")
    print(f"  url    {gf_url}")
    gf_css = fetch(gf_url).decode("utf-8")
    save(RAW / "google-fonts.css", gf_css.encode("utf-8"), force=args.force)

    # Every woff2 the sheet points at, keyed by the family/weight block it
    # belongs to so the filenames stay readable.
    font_urls = sorted(set(re.findall(r"https://fonts\.gstatic\.com/[^)\s]+\.woff2", gf_css)))
    if not font_urls:
        print("  ERROR: no woff2 URLs in the Google Fonts sheet", file=sys.stderr)
        return 1
    for url in font_urls:
        name = url.rsplit("/", 1)[-1]
        # gstatic filenames are opaque hashes; prefix with the family so
        # src/fonts/ is navigable by a human.
        family = "space-grotesk" if "spacegrotesk" in url else "share-tech-mono"
        save(FONTS / f"{family}__{name}", fetch(url), force=args.force)

    print(f"\nDone. Raw mirror in {RAW.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
