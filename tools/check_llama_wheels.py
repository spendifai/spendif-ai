#!/usr/bin/env python3
"""Check that the llama-cpp-python wheels we ship still have the bytes we vetted.

Every other package in uv.lock carries a sha256 recorded by uv at lock time.
The llama-cpp-python wheels do not: they come from the upstream prebuilt-wheel
indexes (`.../whl/metal/` and `.../whl/cpu/`), which publish no checksums. So
the lockfile pins the version and the URL, but nothing pins the bytes, and a
GitHub release asset can be replaced in place by whoever owns it.

This tool closes that gap the way `compute_prompt_hashes.py` does for prompts:
the checksums we observed are committed next to the code, and a mismatch is a
red build instead of a silent swap inside the component that runs the model.

    python tools/check_llama_wheels.py            # verify (default)
    python tools/check_llama_wheels.py --update   # re-record, after a version bump

Only the platforms we actually ship are checked - see PLATFORMS.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import tomllib
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOCK = REPO / "uv.lock"
HASHES = REPO / "packaging" / "llama_wheel_hashes.txt"
PACKAGE = "llama-cpp-python"

# The wheel tags behind the installers that take a prebuilt wheel: x86_64 MSIX,
# amd64/arm64 .deb and the RPM. macOS is absent on purpose - it resolves from
# PyPI, whose sdist uv already records with a sha256 of its own. Anything else
# upstream publishes (riscv64, wasm32, musllinux) is not a platform we ship.
PLATFORMS = (
    "manylinux2014_x86_64",
    "manylinux2014_aarch64",
    "win_amd64",
)

CHUNK = 1 << 20


def shipped_wheel_urls() -> list[str]:
    with LOCK.open("rb") as fh:
        lock = tomllib.load(fh)
    urls: set[str] = set()
    for package in lock.get("package", []):
        if package.get("name") != PACKAGE:
            continue
        for wheel in package.get("wheels", []):
            url = wheel.get("url", "")
            if any(tag in url for tag in PLATFORMS):
                urls.add(url)
    if not urls:
        sys.exit(f"ERROR: no {PACKAGE} wheel for a shipped platform found in {LOCK}")
    return sorted(urls)


def sha256_of(url: str) -> str:
    digest = hashlib.sha256()
    with urllib.request.urlopen(url) as response:  # noqa: S310 - fixed https URLs from the lockfile
        while chunk := response.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def read_recorded() -> dict[str, str]:
    if not HASHES.exists():
        return {}
    recorded = {}
    for line in HASHES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, url = line.partition("  ")
        recorded[url.strip()] = digest.strip()
    return recorded


def write_recorded(pairs: list[tuple[str, str]]) -> None:
    header = (
        "# sha256 of the llama-cpp-python wheels we ship, one per platform.\n"
        "# The upstream wheel indexes publish no checksums, so these are recorded\n"
        "# here and verified by tools/check_llama_wheels.py. Regenerate with\n"
        "# `python tools/check_llama_wheels.py --update` after a version bump,\n"
        "# and read the upstream release notes before committing the new values.\n"
    )
    body = "".join(f"{digest}  {url}\n" for url, digest in pairs)
    HASHES.write_text(header + body, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="download the wheels and record their checksums instead of verifying",
    )
    args = parser.parse_args()

    urls = shipped_wheel_urls()

    if args.update:
        pairs = []
        for url in urls:
            print(f"  hashing {url.rsplit('/', 1)[-1]}", flush=True)
            pairs.append((url, sha256_of(url)))
        write_recorded(pairs)
        print(f"\nRecorded {len(pairs)} checksums in {HASHES.relative_to(REPO)}")
        return 0

    recorded = read_recorded()
    if not recorded:
        print(
            f"ERROR: {HASHES.relative_to(REPO)} is missing or empty.\n"
            "       Run `python tools/check_llama_wheels.py --update` and commit it.",
            file=sys.stderr,
        )
        return 1

    failures = []
    for url in urls:
        name = url.rsplit("/", 1)[-1]
        expected = recorded.get(url)
        if expected is None:
            failures.append(
                f"{name}: not recorded. The lockfile points at a wheel nobody vetted."
            )
            continue
        actual = sha256_of(url)
        if actual != expected:
            failures.append(
                f"{name}: expected {expected}, got {actual}. "
                "The file behind this URL changed after we recorded it."
            )
        else:
            print(f"  ok  {name}")

    stale = sorted(set(recorded) - set(urls))
    for url in stale:
        print(f"  --  {url.rsplit('/', 1)[-1]} recorded but no longer in the lockfile")

    if failures:
        print("\nWheel integrity check FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        print(
            "\nDo not bypass this by re-running --update without looking: it is the\n"
            "only thing standing between an upstream swap and the component that\n"
            "runs the model on the user's machine.",
            file=sys.stderr,
        )
        return 1

    print(f"\nAll {len(urls)} shipped wheels match the recorded checksums.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
