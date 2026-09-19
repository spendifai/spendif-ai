"""Tests for the APT archive generator (packaging/linux/update-apt-repo.py).

These cover the index logic, which is what a malformed archive comes from. The
signature path and apt's acceptance of the result are exercised in CI by
.github/workflows/apt-repo.yml, which builds a real archive and installs from
it with a real apt client.
"""
from __future__ import annotations

import gzip
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "packaging" / "linux" / "update-apt-repo.py"


def _load():
    spec = importlib.util.spec_from_file_location("apt_repo", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


apt_repo = _load()

needs_dpkg = pytest.mark.skipif(
    shutil.which("dpkg-deb") is None,
    reason="dpkg-deb not installed (brew install dpkg)",
)


def _make_deb(tmp_path: Path, version: str, arch: str) -> Path:
    """Build a minimal but real .deb, so dpkg-deb parses what dpkg-deb wrote."""
    root = tmp_path / f"pkg_{version}_{arch}"
    (root / "DEBIAN").mkdir(parents=True)
    (root / "opt" / "spendifai").mkdir(parents=True)
    (root / "opt" / "spendifai" / "app.py").write_text("# test\n", encoding="utf-8")
    (root / "DEBIAN" / "control").write_text(
        f"""Package: spendifai
Version: {version}
Section: misc
Priority: optional
Architecture: {arch}
Maintainer: Test <test@example.invalid>
Description: Test package
 A longer description
 spanning two continuation lines.
""",
        encoding="utf-8",
    )
    out = tmp_path / f"spendifai_{version}_{arch}.deb"
    subprocess.run(["dpkg-deb", "--build", str(root), str(out)],
                   check=True, capture_output=True)
    return out


# ── Version ordering ──────────────────────────────────────────────────────────

def test_version_key_orders_numerically():
    """String ordering would put 0.2.10 before 0.2.9 and prune the wrong one."""
    versions = ["0.2.9", "0.10.0", "0.2.10", "0.3.0"]
    assert sorted(versions, key=apt_repo.version_key) == [
        "0.2.9", "0.2.10", "0.3.0", "0.10.0",
    ]


# ── Pool pruning ──────────────────────────────────────────────────────────────

@needs_dpkg
def test_prune_pool_keeps_the_newest_versions(tmp_path):
    pool = tmp_path / "pool"
    pool.mkdir()
    for version in ("0.1.0", "0.2.0", "0.2.10", "0.2.9"):
        for arch in ("amd64", "arm64"):
            (pool / f"spendifai_{version}_{arch}.deb").write_bytes(b"x")

    apt_repo.prune_pool(pool, keep=2)
    kept = sorted({p.name.split("_")[1] for p in pool.glob("*.deb")})
    assert kept == ["0.2.10", "0.2.9"]
    # Both architectures of a kept version survive together.
    assert len(list(pool.glob("*.deb"))) == 4


@needs_dpkg
def test_prune_pool_is_a_noop_below_the_limit(tmp_path):
    pool = tmp_path / "pool"
    pool.mkdir()
    for version in ("0.1.0", "0.2.0"):
        (pool / f"spendifai_{version}_amd64.deb").write_bytes(b"x")
    apt_repo.prune_pool(pool, keep=5)
    assert len(list(pool.glob("*.deb"))) == 2


# ── Packages stanza ───────────────────────────────────────────────────────────

@needs_dpkg
def test_packages_stanza_carries_location_and_hash(tmp_path):
    deb = _make_deb(tmp_path, "1.2.3", "amd64")
    stanza = apt_repo.packages_stanza(deb, "pool/main/s/spendifai/x.deb")

    assert "Package: spendifai" in stanza
    assert "Version: 1.2.3" in stanza
    assert "Filename: pool/main/s/spendifai/x.deb" in stanza
    assert f"Size: {deb.stat().st_size}" in stanza
    assert f"SHA256: {apt_repo.sha256_of(deb)}" in stanza


@needs_dpkg
def test_packages_stanza_puts_description_last(tmp_path):
    """Description continuation lines swallow every field printed after them,
    so a Filename emitted below it would vanish from apt's view."""
    deb = _make_deb(tmp_path, "1.2.3", "amd64")
    stanza = apt_repo.packages_stanza(deb, "pool/main/s/spendifai/x.deb")

    lines = stanza.splitlines()
    description_at = next(i for i, l in enumerate(lines) if l.startswith("Description:"))
    for field in ("Filename:", "Size:", "SHA256:"):
        assert next(i for i, l in enumerate(lines) if l.startswith(field)) < description_at


# ── Full index ────────────────────────────────────────────────────────────────

@needs_dpkg
def test_build_indexes_produces_a_complete_release(tmp_path):
    repo = tmp_path / "repo"
    pool = repo / apt_repo.POOL
    pool.mkdir(parents=True)
    shutil.copy2(_make_deb(tmp_path, "1.0.0", "amd64"), pool)
    shutil.copy2(_make_deb(tmp_path, "1.0.0", "arm64"), pool)

    apt_repo.build_indexes(repo, gpg_key=None, dry_run=True)

    release = (repo / "dists" / apt_repo.SUITE / "Release").read_text()
    for field in ("Origin:", "Label:", "Suite:", "Codename:",
                  "Architectures:", "Components:", "Date:", "SHA256:"):
        assert field in release, f"Release is missing {field}"

    # Valid-Until is omitted on purpose: apt refuses an expired Release file
    # outright rather than warning, which would break every user at once.
    assert "Valid-Until:" not in release

    # Every index file must be listed in the Release hashes, or apt rejects it.
    for arch in apt_repo.ARCHITECTURES:
        for name in ("Packages", "Packages.gz"):
            assert f"{apt_repo.COMPONENT}/binary-{arch}/{name}" in release


@needs_dpkg
def test_build_indexes_hashes_match_the_files_on_disk(tmp_path):
    """A mismatch here is the single most common way a hand-rolled archive
    fails: apt downloads Packages, hashes it, and rejects the repository."""
    repo = tmp_path / "repo"
    pool = repo / apt_repo.POOL
    pool.mkdir(parents=True)
    shutil.copy2(_make_deb(tmp_path, "1.0.0", "amd64"), pool)

    apt_repo.build_indexes(repo, gpg_key=None, dry_run=True)

    dists = repo / "dists" / apt_repo.SUITE
    listed = {}
    for line in (dists / "Release").read_text().splitlines():
        if line.startswith(" "):
            digest, size, name = line.split()
            listed[name] = (digest, int(size))

    assert listed, "Release carries no hashes"
    for name, (digest, size) in listed.items():
        path = dists / name
        assert path.is_file(), f"{name} is listed in Release but absent"
        assert apt_repo.sha256_of(path) == digest, f"{name}: hash mismatch"
        assert path.stat().st_size == size, f"{name}: size mismatch"


@needs_dpkg
def test_packages_gz_decompresses_to_packages(tmp_path):
    repo = tmp_path / "repo"
    pool = repo / apt_repo.POOL
    pool.mkdir(parents=True)
    shutil.copy2(_make_deb(tmp_path, "1.0.0", "amd64"), pool)

    apt_repo.build_indexes(repo, gpg_key=None, dry_run=True)

    arch_dir = repo / "dists" / apt_repo.SUITE / apt_repo.COMPONENT / "binary-amd64"
    plain = (arch_dir / "Packages").read_bytes()
    assert gzip.decompress((arch_dir / "Packages.gz").read_bytes()) == plain


@needs_dpkg
def test_index_is_byte_stable_across_rebuilds(tmp_path):
    """Packages.gz embeds an mtime by default, so an unchanged pool would
    produce a different file every run and the archive would collect empty
    commits forever."""
    repo = tmp_path / "repo"
    pool = repo / apt_repo.POOL
    pool.mkdir(parents=True)
    shutil.copy2(_make_deb(tmp_path, "1.0.0", "amd64"), pool)

    apt_repo.build_indexes(repo, gpg_key=None, dry_run=True)
    arch_dir = repo / "dists" / apt_repo.SUITE / apt_repo.COMPONENT / "binary-amd64"
    first = (arch_dir / "Packages.gz").read_bytes()

    apt_repo.build_indexes(repo, gpg_key=None, dry_run=True)
    assert (arch_dir / "Packages.gz").read_bytes() == first


@needs_dpkg
def test_older_versions_stay_installable(tmp_path):
    """The index is built from the pool, not from the release being published:
    `apt install spendifai=0.2.1` must keep working after 0.3.0 ships."""
    repo = tmp_path / "repo"
    pool = repo / apt_repo.POOL
    pool.mkdir(parents=True)
    shutil.copy2(_make_deb(tmp_path, "0.2.1", "amd64"), pool)
    shutil.copy2(_make_deb(tmp_path, "0.3.0", "amd64"), pool)

    apt_repo.build_indexes(repo, gpg_key=None, dry_run=True)

    packages = (repo / "dists" / apt_repo.SUITE / apt_repo.COMPONENT
                / "binary-amd64" / "Packages").read_text()
    assert "Version: 0.2.1" in packages
    assert "Version: 0.3.0" in packages
