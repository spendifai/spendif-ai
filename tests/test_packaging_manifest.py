"""The packaging lists must contain what the application imports, and nothing dead.

WHY IT EXISTS
    On 2026-09-23 the Chat page was found broken in every package ever
    published, on every operating system: chat_bot/ was never listed in any
    packaging manifest, so it was never copied into any artifact. The
    application still started, because app.py imports the page lazily, and the
    crash only arrived when somebody clicked Chat. No test saw it, because
    every test imports from the source tree, where chat_bot/ sits next to ui/.

    The same audit found the mirror-image defect: nsi/ was listed in all of
    them and is gitignored, so CI silently skipped it while a developer machine
    silently included 16 MB. Two artifacts with the same name and different
    contents depending on who built them.

    Both defects are invisible to every other test in this suite, because both
    live in the gap between "the code imports it" and "the package ships it".
    This is the only test that looks at that gap.

WHAT IT CHECKS
    1. Every first-party module reachable from app.py appears in every list.
    2. Every directory named by a list is tracked in the repository, so an
       entry cannot exist on one machine and be skipped in silence on another.
    3. The lists agree with each other.

    It deliberately does not require the lists to equal the set of imports:
    they also carry runtime data directories (prompts/, config/) that nothing
    imports.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY_POINT = "app.py"

# Every copy of the list, and how to read it. packaging/linux/arch/build/ is
# absent on purpose: it is generated from packaging/linux/arch/PKGBUILD by
# make-pkgbuild.sh, so it cannot drift on its own.
MANIFESTS = (
    "desktop.spec",
    "packaging/linux/build-deb.sh",
    "packaging/linux/build-rpm.sh",
    "packaging/linux/arch/PKGBUILD",
)


def _read_manifest(rel_path: str) -> set[str]:
    """The directories one packaging manifest promises to copy."""
    text = (REPO_ROOT / rel_path).read_text()

    if rel_path.endswith(".spec"):
        # A PyInstaller spec is Python, but it is not importable on its own
        # (it runs with injected globals), so read the literal rather than
        # executing the file.
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "APP_PACKAGES" for t in node.targets
            ):
                entries = ast.literal_eval(node.value)
                break
        else:
            pytest.fail(f"{rel_path}: no APP_PACKAGES assignment found")
    elif "PKGBUILD" in rel_path:
        match = re.search(r"for d in ([^;]+); do", text)
        assert match, f"{rel_path}: no package loop found"
        entries = match.group(1).split()
    else:
        match = re.search(r"APP_DIRS=\(([^)]*)\)", text)
        assert match, f"{rel_path}: no APP_DIRS assignment found"
        entries = match.group(1).split()

    # Top-level files (app.py, VERSION, ...) travel through a separate loop in
    # the shell scripts, so compare directories only.
    return {e for e in entries if not e.endswith(".py") and not Path(e).suffix}


def _is_first_party(name: str) -> bool:
    """A top-level name that resolves to code in this repository.

    ui/ and support/ have no __init__.py and are imported anyway, as namespace
    packages. Requiring __init__.py here is not a detail: it breaks the walk at
    ui/, which is precisely where the import of chat_bot lives, and this test
    would then pass while shipping the defect it exists to catch.
    """
    if (REPO_ROOT / f"{name}.py").exists():
        return True
    directory = REPO_ROOT / name
    return directory.is_dir() and any(directory.rglob("*.py"))


def _imported_packages() -> set[str]:
    """First-party packages reachable from app.py, transitively.

    Transitive and not just app.py plus ui/: a package pulled in only by
    services/ is needed at runtime exactly as much as one the UI names.
    """
    found: set[str] = set()
    queue = [REPO_ROOT / ENTRY_POINT]
    seen: set[Path] = set()

    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)

        try:
            tree = ast.parse(current.read_text())
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # Relative imports stay inside a package already queued.
                names = [node.module] if node.module and node.level == 0 else []
            else:
                continue

            for dotted in names:
                top = dotted.split(".")[0]
                if not _is_first_party(top):
                    continue
                found.add(top)
                package_dir = REPO_ROOT / top
                if package_dir.is_dir():
                    queue.extend(
                        p for p in package_dir.rglob("*.py") if "__pycache__" not in p.parts
                    )

    return found


def _tracked(name: str) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--", name],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(result.stdout.strip())


@pytest.fixture(scope="module")
def manifests() -> dict[str, set[str]]:
    return {path: _read_manifest(path) for path in MANIFESTS}


@pytest.mark.parametrize("manifest_path", MANIFESTS)
def test_manifest_ships_every_imported_package(manifest_path):
    """A first-party import that no manifest copies is a page that crashes."""
    listed = _read_manifest(manifest_path)
    missing = sorted(_imported_packages() - listed)
    assert not missing, (
        f"{manifest_path} does not ship {missing}, and the application imports "
        f"it. Installed builds will fail with ModuleNotFoundError as soon as "
        f"the feature is used."
    )


@pytest.mark.parametrize("manifest_path", MANIFESTS)
def test_manifest_has_no_untracked_entry(manifest_path):
    """An entry that is not in the repository is copied on some machines only.

    Every manifest guards its copy with an existence check, so a gitignored
    directory is skipped without a word in CI and included on the machine that
    happens to have it.
    """
    if not (REPO_ROOT / ".git").exists():  # pragma: no cover - tarball checkout
        pytest.skip("not a repository checkout")

    dead = sorted(name for name in _read_manifest(manifest_path) if not _tracked(name))
    assert not dead, (
        f"{manifest_path} lists {dead}, which the repository does not track. "
        f"Either it belongs in the repository, or it is generation input and "
        f"does not belong in the package."
    )


def test_all_manifests_agree(manifests):
    """Four hand-written copies of one list drift. This is what says when."""
    reference_path, reference = next(iter(manifests.items()))
    for path, entries in manifests.items():
        assert entries == reference, (
            f"{path} and {reference_path} do not ship the same directories: "
            f"only in {path}: {sorted(entries - reference)}, "
            f"only in {reference_path}: {sorted(reference - entries)}"
        )


def test_chat_bot_is_shipped(manifests):
    """The named defect, kept as its own line so a regression reads plainly."""
    for path, entries in manifests.items():
        assert "chat_bot" in entries, f"{path} does not ship chat_bot"
