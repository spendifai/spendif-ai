"""Copy the user documentation into the support bot's knowledge.

The bot answers from chat_bot/knowledge/<lang>/docs/. Those files used to be
copied by hand and fell months behind the guides they came from, so the bot
did not know features the guides already described. This script is the only
way they are written; --check fails when they differ from the guides.

Usage:
    python tools/build_chat_knowledge.py           # write the copies
    python tools/build_chat_knowledge.py --check   # exit 1 if any copy is stale
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
KNOWLEDGE = ROOT / "chat_bot" / "knowledge"

# knowledge file name -> source guide, per language
SOURCES = {
    "it": {
        "02_installazione.md": "installazione.md",
        "03_guida_utente.md": "guida_utente.md",
        "04_configurazione.md": "configurazione.md",
        "05_guida_classificazione.md": "guida_classificazione.md",
        "06_reference_guide.md": "reference_guide.md",
    },
    "en": {
        "02_installazione.md": "installazione.en.md",
        "03_guida_utente.md": "guida_utente.en.md",
        "04_configurazione.md": "configurazione.en.md",
        "05_guida_classificazione.md": "guida_classificazione.en.md",
        "06_reference_guide.md": "reference_guide.en.md",
    },
}


def plan() -> list[tuple[Path, Path]]:
    return [
        (DOCS / src, KNOWLEDGE / lang / "docs" / dst)
        for lang, files in SOURCES.items()
        for dst, src in files.items()
    ]


def stale() -> list[Path]:
    return [dst for src, dst in plan()
            if not dst.exists() or dst.read_bytes() != src.read_bytes()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if any copy differs from its guide")
    args = ap.parse_args()

    if args.check:
        behind = stale()
        for path in behind:
            print(f"stale: {path.relative_to(ROOT)}")
        if behind:
            print("Run: python tools/build_chat_knowledge.py")
            return 1
        print(f"OK: {len(plan())} knowledge files match the guides")
        return 0

    for src, dst in plan():
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
    print(f"wrote {len(plan())} knowledge files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
