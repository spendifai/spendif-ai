#!/usr/bin/env python3
"""Generate sitemap.xml for the Spendif.ai landing (GitHub Pages, repo root).

Covers the two multilingual page families (index.*, getting-started.*), each as
an hreflang cluster with x-default on English. Pages carrying
`robots: noindex` are skipped. `lastmod` comes from the file mtime, so the
sitemap never goes stale as long as this script is re-run before publishing.

Usage:  python3 scripts/gen_sitemap.py [--check]
        --check exits 1 if sitemap.xml differs from what would be generated.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

BASE = "https://spendif.ai/"
LANGS = ["it", "en", "de", "es", "fr", "ja", "nl", "pl", "pt"]
FAMILIES = ["index", "getting-started"]
ROOT = Path(__file__).resolve().parent.parent


def filename(family: str, lang: str) -> str:
    return f"{family}.html" if lang == "it" else f"{family}.{lang}.html"


def url_for(family: str, lang: str) -> str:
    # the Italian index is served as the site root
    if family == "index" and lang == "it":
        return BASE
    return BASE + filename(family, lang)


def is_noindex(path: Path) -> bool:
    head = path.read_text(encoding="utf-8")[:4000]
    return bool(re.search(r'name="robots"[^>]*content="[^"]*noindex', head))


def build() -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"',
           '        xmlns:xhtml="http://www.w3.org/1999/xhtml">']
    for family in FAMILIES:
        alternates = [
            f'    <xhtml:link rel="alternate" hreflang="{lang}" href="{url_for(family, lang)}"/>'
            for lang in LANGS
            if (ROOT / filename(family, lang)).exists()
        ]
        alternates.append(
            f'    <xhtml:link rel="alternate" hreflang="x-default" href="{url_for(family, "en")}"/>'
        )
        for lang in LANGS:
            path = ROOT / filename(family, lang)
            if not path.exists() or is_noindex(path):
                continue
            lastmod = dt.date.fromtimestamp(path.stat().st_mtime).isoformat()
            out.append("  <url>")
            out.append(f"    <loc>{url_for(family, lang)}</loc>")
            out.append(f"    <lastmod>{lastmod}</lastmod>")
            out.extend(alternates)
            out.append("  </url>")
    out.append("</urlset>")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if sitemap.xml is out of date")
    args = ap.parse_args()

    target = ROOT / "sitemap.xml"
    generated = build()
    n_urls = generated.count("<loc>")

    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != generated:
            print("sitemap.xml is out of date: re-run scripts/gen_sitemap.py")
            return 1
        print(f"sitemap.xml up to date ({n_urls} urls)")
        return 0

    target.write_text(generated, encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)} ({n_urls} urls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
