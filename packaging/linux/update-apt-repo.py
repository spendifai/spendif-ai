#!/usr/bin/env python3
"""Publish the signed APT repository for a release.

Downloads the .deb assets of a published GitHub Release, rebuilds the archive
index, signs it with the project GPG key, and pushes the result to the apt
repository, which GitHub Pages serves over HTTPS.

WHY the .deb cannot simply stay a release asset. The `Filename:` field inside
`Packages` is resolved relative to the repository base URL, so apt will only
fetch packages that live under that URL. Release assets sit at flat paths
behind a redirect, with no directory index. The package therefore has to be
copied into the published tree. At roughly 11 MB per architecture that is
cheap: the .deb is a thin source wrapper, not a frozen bundle.

WHY the signing happens here and not in CI. A repository signing key that leaks
lets an attacker serve arbitrary packages to everyone who added the repository,
which is the same damage as a leaked code signing key. The project already
decided that model for macOS (docs/release_process.md, Section 2bis: sign
locally, keep the credentials off CI), and this follows it. The cost is that
publishing is a manual step, so .github/workflows/verify-apt-repo.yml fails
when the repository falls behind the newest release.

WHY no apt-ftparchive. It only exists on Debian systems, and the signing key is
on a Mac. `Packages` and `Release` are small, well-specified text formats, so
generating them here keeps the whole publish step runnable wherever the key is.

USAGE
    Publish a release (the normal case):
        python3 packaging/linux/update-apt-repo.py [--version X.Y.Z]
                                                  [--repo owner/name]
                                                  [--gpg-key KEYID]
                                                  [--keep N] [--dry-run]

    Build an archive from local .deb files, touching nothing remote. This is
    what CI uses to check the generated index against a real apt client, with a
    throwaway key, so the format is exercised without the signing key ever
    leaving the machine that holds it:
        python3 packaging/linux/update-apt-repo.py --local-dir build \
                                                  --output /tmp/apt

PREREQUISITES
    gh (authenticated), git, gpg with the signing secret key, dpkg-deb
    (`brew install dpkg` on macOS). --local-dir with --output needs neither gh
    nor git.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import shutil
import subprocess
import sys
import tempfile
from email.utils import formatdate
from pathlib import Path

SOURCE_REPO = "spendifai/spendif-ai"
APT_REPO = "spendifai/apt"
ARCHITECTURES = ("amd64", "arm64")
SUITE = "stable"
COMPONENT = "main"
ORIGIN = "Spendif.ai"
POOL = f"pool/{COMPONENT}/s/spendifai"
KEYRING_NAME = "spendifai-archive-keyring.gpg"

# How many versions stay installable. Older ones are dropped from the pool so
# the Pages repository does not grow without bound; the GitHub Release keeps
# every version regardless, so nothing is actually lost.
DEFAULT_KEEP = 5


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Run a command, raising with the captured stderr when it fails."""
    proc = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if proc.returncode != 0:
        raise SystemExit(
            f"FAILED: {' '.join(cmd)}\n{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc


def info(msg: str) -> None:
    print(f">  {msg}")


def ok(msg: str) -> None:
    print(f"OK {msg}")


def die(msg: str) -> None:
    raise SystemExit(f"ERROR: {msg}")


def require(tool: str, hint: str = "") -> None:
    if shutil.which(tool) is None:
        die(f"{tool} is required{f' ({hint})' if hint else ''}")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def deb_control(deb: Path) -> dict[str, str]:
    """Control fields of a .deb, as a dict, via dpkg-deb."""
    out = run(["dpkg-deb", "-f", str(deb)]).stdout
    fields: dict[str, str] = {}
    key = None
    for line in out.splitlines():
        if line.startswith((" ", "\t")) and key:
            fields[key] += "\n" + line
        elif ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            fields[key] = value.strip()
    return fields


def packages_stanza(deb: Path, pool_path: str) -> str:
    """One `Packages` entry: the control fields plus where and what it is.

    Field order follows what dpkg produces, because apt does not care but a
    human diffing two generated indexes very much does.
    """
    fields = deb_control(deb)
    # Description must come last: everything after its continuation lines would
    # otherwise be swallowed into it.
    description = fields.pop("Description", "")
    lines = [f"{k}: {v}" for k, v in fields.items()]
    lines += [
        f"Filename: {pool_path}",
        f"Size: {deb.stat().st_size}",
        f"SHA256: {sha256_of(deb)}",
    ]
    if description:
        lines.append(f"Description: {description}")
    return "\n".join(lines) + "\n"


def version_key(version: str) -> tuple[int, ...]:
    parts = version.split(".")
    return tuple(int(p) if p.isdigit() else 0 for p in parts)


def prune_pool(pool_dir: Path, keep: int) -> None:
    """Keep the newest `keep` versions in the pool, drop the rest."""
    versions: dict[str, list[Path]] = {}
    for deb in pool_dir.glob("*.deb"):
        # spendifai_<version>_<arch>.deb
        try:
            version = deb.name.split("_")[1]
        except IndexError:
            continue
        versions.setdefault(version, []).append(deb)

    if len(versions) <= keep:
        return
    for version in sorted(versions, key=version_key)[:-keep]:
        for deb in versions[version]:
            info(f"pruning {deb.name}")
            deb.unlink()


def build_indexes(repo_dir: Path, gpg_key: str | None, dry_run: bool) -> None:
    """Regenerate Packages, Packages.gz, Release, InRelease and Release.gpg."""
    dists = repo_dir / "dists" / SUITE
    pool_dir = repo_dir / POOL

    # Packages, per architecture, from everything currently in the pool. Built
    # from the pool rather than from the release being published, so that older
    # versions stay installable and `apt install spendifai=0.2.1` keeps working.
    hashed: list[tuple[str, int, str]] = []
    for arch in ARCHITECTURES:
        arch_dir = dists / COMPONENT / f"binary-{arch}"
        arch_dir.mkdir(parents=True, exist_ok=True)

        stanzas = [
            packages_stanza(deb, f"{POOL}/{deb.name}")
            for deb in sorted(pool_dir.glob(f"*_{arch}.deb"))
        ]
        content = "\n".join(stanzas).encode("utf-8")

        packages = arch_dir / "Packages"
        packages.write_bytes(content)
        # mtime=0 so rebuilding an unchanged index produces an identical file
        # and the tap repository does not collect empty commits.
        packages_gz = arch_dir / "Packages.gz"
        packages_gz.write_bytes(gzip.compress(content, mtime=0))

        for path in (packages, packages_gz):
            rel = path.relative_to(dists).as_posix()
            hashed.append((sha256_of(path), path.stat().st_size, rel))
        info(f"{arch}: {len(stanzas)} package(s)")

    # Release. No Valid-Until on purpose: apt does not warn when a Release file
    # expires, it refuses the repository outright. With a release every few
    # months that field is a timer set to break every user's `apt update` on a
    # date nobody wrote down.
    release_lines = [
        f"Origin: {ORIGIN}",
        f"Label: {ORIGIN}",
        f"Suite: {SUITE}",
        f"Codename: {SUITE}",
        f"Architectures: {' '.join(ARCHITECTURES)}",
        f"Components: {COMPONENT}",
        "Description: Spendif.ai personal finance manager",
        f"Date: {formatdate(usegmt=True)}",
        "SHA256:",
    ]
    release_lines += [f" {h} {size} {name}" for h, size, name in hashed]
    release = dists / "Release"
    release.write_text("\n".join(release_lines) + "\n", encoding="utf-8")

    if dry_run:
        info("dry run: not signing")
        return

    # InRelease (inline signature) is what modern apt fetches; Release.gpg
    # (detached) is kept for older clients. Without one of the two, apt refuses
    # the repository, and the only way to silence it would be [trusted=yes],
    # which turns off the very check this exists to provide.
    sign = ["gpg", "--batch", "--yes"]
    if gpg_key:
        sign += ["--local-user", gpg_key]

    inrelease = dists / "InRelease"
    run(sign + ["--clearsign", "--output", str(inrelease), str(release)])
    run(sign + ["--detach-sign", "--armor",
                "--output", str(dists / "Release.gpg"), str(release)])
    ok("Release signed (InRelease + Release.gpg)")

    # The public key, dearmored, is what users drop into /etc/apt/keyrings.
    export = ["gpg", "--export"] + ([gpg_key] if gpg_key else [])
    exported = subprocess.run(export, capture_output=True, check=True).stdout
    if not exported:
        die("gpg exported an empty public key: is --gpg-key correct?")
    (repo_dir / KEYRING_NAME).write_bytes(exported)
    ok(f"{KEYRING_NAME} exported ({len(exported)} bytes)")


def write_readme(repo_dir: Path) -> None:
    base = f"https://{APT_REPO.split('/')[0]}.github.io/{APT_REPO.split('/')[1]}"
    (repo_dir / "README.md").write_text(f"""\
# APT repository - Spendif.ai

Personal finance manager with local AI categorisation.
Source code: https://github.com/{SOURCE_REPO}

## Install

```bash
sudo install -d -m 0755 /etc/apt/keyrings
sudo curl -fsSL -o /etc/apt/keyrings/{KEYRING_NAME} \\
  {base}/{KEYRING_NAME}

sudo tee /etc/apt/sources.list.d/spendifai.sources > /dev/null <<'EOF'
Types: deb
URIs: {base}
Suites: {SUITE}
Components: {COMPONENT}
Architectures: {' '.join(ARCHITECTURES)}
Signed-By: /etc/apt/keyrings/{KEYRING_NAME}
EOF

sudo apt update
sudo apt install spendifai
```

The key is fetched already dearmored, so it needs no `gpg --dearmor` step, and
`Signed-By:` scopes it to this repository alone. Do not use `apt-key add`: it
was removed in Debian 12 and Ubuntu 24.04, and it would trust this key for
every repository on the machine.

`Architectures:` bounds apt to the two architectures this repository actually
ships. It matters on a machine with multiarch enabled: apt would otherwise ask
for `main/binary-i386/Packages`, which does not exist here, and print
`Skipping acquire of configured file` on every update. The cost is that apt
also fetches the index for the other listed architecture, a few hundred bytes.

## Update

```bash
sudo apt update && sudo apt upgrade spendifai
```

## Requirements

The package needs `python3 (>= 3.12)`. Ubuntu 24.04 and Debian 13 satisfy it;
**Debian 12 ships Python 3.11 and cannot install this package**, and `apt` will
say so rather than installing something broken.

## Uninstall

```bash
sudo apt remove spendifai        # keeps your data in ~/.spendifai
rm -rf ~/.spendifai              # also deletes it
```

This repository is generated: the index is built and signed by
`packaging/linux/update-apt-repo.py` in the main repository.
""", encoding="utf-8")


def build_local(source: Path, output: Path, gpg_key: str | None, keep: int) -> None:
    """Build a signed archive from local .deb files, into a local directory.

    Same index and same signing path as a real publish: the point of this mode
    is that CI exercises exactly what users will fetch, not an approximation
    of it.
    """
    debs = sorted(source.glob("*.deb"))
    if not debs:
        die(f"no .deb files in {source}")

    pool_dir = output / POOL
    pool_dir.mkdir(parents=True, exist_ok=True)
    for deb in debs:
        shutil.copy2(deb, pool_dir / deb.name)
        ok(f"pooled {deb.name}")
    prune_pool(pool_dir, keep)

    build_indexes(output, gpg_key, dry_run=False)
    write_readme(output)
    (output / ".nojekyll").write_text("", encoding="utf-8")
    ok(f"archive built in {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="Release to publish. Defaults to the VERSION file.")
    parser.add_argument("--repo", default=APT_REPO, help=f"APT repository (default: {APT_REPO})")
    parser.add_argument("--gpg-key", help="Signing key id or email. Defaults to the gpg default key.")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                        help=f"Versions to keep in the pool (default: {DEFAULT_KEEP})")
    parser.add_argument("--dry-run", action="store_true", help="Build the index, push nothing.")
    parser.add_argument("--local-dir", help="Index the .deb files in this directory "
                                            "instead of downloading a release.")
    parser.add_argument("--output", help="Write the archive here instead of cloning "
                                         "and pushing the apt repository.")
    args = parser.parse_args()

    offline = bool(args.local_dir and args.output)

    require("gpg")
    require("dpkg-deb", "brew install dpkg on macOS")
    if not offline:
        require("gh", "authenticated: gh auth login")
        require("git")

    if offline:
        build_local(Path(args.local_dir), Path(args.output), args.gpg_key, args.keep)
        return

    repo_root = Path(__file__).resolve().parents[2]
    version = args.version
    if not version:
        version_file = repo_root / "VERSION"
        if not version_file.is_file():
            die("no --version given and no VERSION file")
        version = version_file.read_text().strip()
    tag = f"v{version}"

    info(f"Release: {tag}  ->  {args.repo}")

    draft = run(["gh", "release", "view", tag, "--repo", SOURCE_REPO,
                 "--json", "isDraft", "--jq", ".isDraft"]).stdout.strip()
    if draft == "true":
        die(f"{tag} is still a draft: apt cannot download from a draft release. "
            f"Publish it first with: gh release edit {tag} --draft=false")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)

        # ── 1. the .deb assets of this release ──────────────────────────────
        assets = work / "assets"
        assets.mkdir()
        run(["gh", "release", "download", tag, "--repo", SOURCE_REPO,
             "--pattern", "*.deb", "--dir", str(assets)])
        debs = sorted(assets.glob("*.deb"))
        if not debs:
            die(f"release {tag} carries no .deb asset")
        for deb in debs:
            ok(f"downloaded {deb.name}")

        # ── 2. the apt repository ───────────────────────────────────────────
        clone = work / "apt"
        exists = subprocess.run(["gh", "repo", "view", args.repo],
                                capture_output=True).returncode == 0
        if not exists:
            if args.dry_run:
                info(f"dry run: would create {args.repo}")
                clone.mkdir()
            else:
                info(f"creating public repository {args.repo}")
                run(["gh", "repo", "create", args.repo, "--public", "--description",
                     "APT repository for Spendif.ai - sudo apt install spendifai"])
                run(["git", "clone", f"https://github.com/{args.repo}.git", str(clone)])
        else:
            run(["git", "clone", f"https://github.com/{args.repo}.git", str(clone)])

        pool_dir = clone / POOL
        pool_dir.mkdir(parents=True, exist_ok=True)
        for deb in debs:
            shutil.copy2(deb, pool_dir / deb.name)
        prune_pool(pool_dir, args.keep)

        build_indexes(clone, args.gpg_key, args.dry_run)
        write_readme(clone)
        # GitHub Pages runs Jekyll by default, which skips files and folders it
        # considers special. An archive is plain static files; opt out.
        (clone / ".nojekyll").write_text("", encoding="utf-8")

        if args.dry_run:
            info("dry run: nothing pushed")
            print(f"\n--- {SUITE}/Release ---")
            print((clone / "dists" / SUITE / "Release").read_text())
            return

        # ── 3. publish ──────────────────────────────────────────────────────
        status = run(["git", "-C", str(clone), "status", "--porcelain"]).stdout.strip()
        if not status:
            ok(f"repository already up to date for {version}: nothing to push")
            return

        name = run(["git", "-C", str(repo_root), "config", "user.name"]).stdout.strip()
        email = run(["git", "-C", str(repo_root), "config", "user.email"]).stdout.strip()
        run(["git", "-C", str(clone), "add", "-A"])
        run(["git", "-C", str(clone),
             "-c", f"user.name={name or 'spendifai-release-bot'}",
             "-c", f"user.email={email or 'release-bot@users.noreply.github.com'}",
             "commit", "-q", "-m", f"spendifai {version}"])
        run(["git", "-C", str(clone), "push", "-q", "origin", "HEAD"])
        ok(f"pushed spendifai {version} to {args.repo}")

        owner, repo_name = args.repo.split("/")
        print(f"""
============================================================
  APT repository updated - Spendif.ai {version}
============================================================

  Pages must be enabled once, serving from the default branch root:
    https://github.com/{args.repo}/settings/pages

  On any Debian or Ubuntu machine:
    see https://{owner}.github.io/{repo_name}/
""")


if __name__ == "__main__":
    main()
