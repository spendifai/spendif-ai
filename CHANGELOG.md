# Changelog

All notable changes to Spendif.ai are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-08-25

### Added
- Traffic-light privacy bar on the LLM configuration page: shows at a glance what leaves the machine for each backend, before the user picks one (#AI-229, #161)
- Landing pages: SEO + GEO metadata, `sitemap.xml`, `robots.txt` and `llms.txt` (#155)
- Alpha feedback page on gh-pages, backed by a Tally form (#162)
- Direct installer download buttons (DMG / MSIX / .deb / .rpm) on the getting-started page, in all 9 locales (#163)
- New brand mark applied to the landing pages and to the app bundle icons (#159)

### Changed
- Sidebar navigation fully translated to Italian (#158)
- Documentation: Python >= 3.12 stated in the install instructions, English brand landing, reciprocal IT README link (#164)
- Version references aligned across README and `pyproject.toml` (#160)

### Fixed
- Desktop window: the Streamlit Deploy button and its toolbar are no longer visible inside the pywebview window; `toolbarMode = viewer` keeps Settings and Theme reachable (#165)
- Coupling: the UI layer no longer imports `db`/`core` directly, and the coupling CI gate that was silently passing is repaired (#157)

### Security
- Dependency floors raised after a pip-audit finding: `gitpython >= 3.1.58` (GHSA-9rj7-rf2p-w77r, GHSA-4gmw-gg2m-w46p, CVE-2026-76217, GHSA-wvpp-8hx9-p66j, GHSA-jm78-9fvv-mhgr) and `cryptography >= 50.0.0` (PYSEC-2026-3552). Lockfile resolves to gitpython 3.1.59 and cryptography 50.0.0

### Known limitations
- The macOS DMG and the Windows MSIX are **not signed**: Gatekeeper and SmartScreen will warn on first launch. Code signing and notarisation are tracked separately
- The winget manifests and the Homebrew cask in `packaging/` still reference the retired Windows ZIP artefact and a `Spendif.ai-<version>.dmg` filename; neither channel is submitted for this release

## [0.1.0] - 2026-07-29

*First public release, tagged and published on 2026-07-29 with installers for macOS, Windows and Linux. The entries below were previously split between an `Unreleased` section and a `0.1.0` section dated with the development start (2026-04-06); they are merged here to match what actually shipped in the v0.1.0 artefacts.*

### Added
- Initial release
- Import CSV/XLSX bank statements (9 Italian financial instruments)
- Local AI categorisation via llama.cpp (Qwen3.5, Gemma4, Phi4, Llama3.2)
- NSI/OSI-aware counterpart matching
- History cache, user rules, taxonomy customisation
- macOS .app bundle, Spotlight integration
- Windows installer via winget/PowerShell
- Interactive analytics dashboard (coming soon)
- Native desktop app: pywebview window embedding Streamlit (no Terminal, no browser)
- Auto-download of recommended LLM model based on hardware detection (RAM/GPU)
- Zero-config llama.cpp setup on first launch
- Splash screen with download progress bar
- macOS installer (`packaging/macos/install.sh`) with native window launcher
- Windows installer (`packaging/windows/install.ps1`) with native window launcher
- Ubuntu/Debian installer (`packaging/linux/install-debian.sh`) and .deb builder
- Red Hat/Fedora installer (`packaging/linux/install-redhat.sh`) and .rpm builder
- PyInstaller spec file for building standalone .app (macOS) and .exe (Windows)
- Uninstall scripts for macOS, Windows (with `-Silent` flag), and Linux
- Windows Add/Remove Programs registration during install
- Linux CI workflow (`release-linux.yml`): builds .deb/.rpm, smoke-tests in containers, attaches to GitHub Release
- winget identifier renamed to `SpendifAi.SpendifAi`
- Windows MSIX installer (`packaging/windows/build-msix.ps1` + `AppxManifest.xml.in`), replaces the previous ZIP-only Windows artefact
- Local signing scripts: `packaging/macos/sign-local.sh` (codesign + notarytool + stapler) and `packaging/windows/sign-local.ps1` (SignTool wrapper)
- Local DMG builder (`packaging/macos/build-dmg.sh`) mirroring the CI job for offline reproducibility
- Hybrid CI release model: `release.yml` builds all four installers unsigned and publishes a **draft** GitHub Release; the owner signs DMG and MSIX locally and replaces them via `gh release upload --clobber` before flipping `--draft=false`. Documented in `docs/release_process.md` §2bis (EN+IT)
- Getting-started page on gh-pages, full 9-locale coverage (`getting-started.{html,en,de,es,fr,ja,nl,pl,pt}.html`): three-step illustrated install/first-launch guide with download buttons for DMG/MSIX/.deb/.rpm and screenshot placeholders (`assets/screenshots/`). Each page includes the Cloudflare Web Analytics beacon and a full language switcher
- Updated `installation_{macos,windows}.{md,it.md}` "First Launch" section to describe the native pywebview flow (splash + model download + onboarding wizard), replacing the obsolete Terminal/browser sequence which only applies to the legacy `install.sh`/`install.ps1` scripts
- Landing pages (all 9 locales: `index.html` IT, `index.{en,de,es,fr,ja,nl,pl,pt}.html`): added localized "Download installer" CTA pointing to the locale-matched getting-started page above the existing curl-script tabs
- README restructured for technical/developer audience (was a mix of end-user + dev content): trimmed from 663 → ~180 lines, banner directing end users to the gh-pages getting-started, six "What's implemented" bullets with file paths + RF-codes + honest beta tags, develop-locally section with Ollama vs llama.cpp clarification, reciprocal IT·EN documentation links
- New `docs/architecture.{md,it.md}` capturing the layer diagram and Flow 1 vs Flow 2 details previously inlined in the README
- New `docs/design_decisions.{md,it.md}` capturing the Decimal/SHA-256/invert_sign/RF-03/RF-04 rationale previously inlined in the README, with explicit `(beta)` status flags on RF-03 and RF-04
- New "What you get" section on all 9 `getting-started.*.html` pages with six user-value bullets (translated into all locales) — duplicate-free framing complementing the README's "What's implemented"
- README "What leaves the machine" section made explicit and honest: shows a concrete before/after PII redaction example and acknowledges that transaction **amounts** and **dates** still travel to remote LLM backends in the current implementation (backlog AI-55 covers redaction modes). Privacy bullet on all 9 gh-pages getting-started rewritten in the same honest tone
- Persistent launcher boot log at `~/Library/Logs/spendifai-launcher.log` (macOS) / `~/.spendifai/spendifai-launcher.log` (Linux/Windows). Captures stdout/stderr from the bundled launcher even when `console=False` in `desktop.spec` would otherwise route them to `/dev/null`. Truncated on every launch
- Parallel model download with live ETA banner. `desktop/launcher.py` now splits the synchronous environment bootstrap from the multi-GB model download. The download runs on a daemon thread and updates `~/.spendifai/model_download.status` with `pct`, `elapsed_s`, `eta_remaining_s`. A new `ui/widgets/model_download_banner.py` reads it via `st.fragment(run_every=2)` and shows a sticky banner without rerunning the whole page. `core/model_manager._download_from_hf` wires Hugging Face's tqdm into our callback for real per-chunk progress (previously the callback only fired at 100%)
- Immersive first-run page. When `onboarding_done == false`, `app.py` injects CSS to hide sidebar / header / toolbar and renders only the download banner + onboarding wizard. As soon as the wizard is confirmed the layout reverts to the normal sidebar app
- Cross-arch `.deb` build. `release.yml` runs `build-deb.sh` for both `amd64` and `arm64` via a matrix; same Python source, only the `Architecture:` declaration in `DEBIAN/control` changes (postinst compiles native deps on the target machine via `uv sync`). Unblocks install on Apple Silicon VMs (#AI-57)
- Documented end-to-end developer flow in `docs/desktop_build_and_test.{md,it.md}`: local DMG build, CI MSIX via `workflow_dispatch`, MSIX install on a clean VM through `packaging/windows/dev-install.ps1` (auto self-signed cert, signs, installs), all log paths, total cleanup
- `packaging/windows/dev-install.ps1`: one-shot script for testers — generates a self-signed cert with the manifest's exact Publisher CN, imports it as trusted, locates SignTool, signs the MSIX, and `Add-AppxPackage` to install. Idempotent (cert reuse), auto-elevates if not Administrator
- Total cleanup scripts for all three OSes: `packaging/macos/cleanup.sh`, `packaging/linux/cleanup.sh`, `packaging/windows/cleanup.ps1`. Each kills running processes, uninstalls the system package (AppX / apt / dnf / .app removal), wipes `~/.spendifai`, the Hugging Face model cache, and launcher logs. Options for `--keep-models` to preserve the GGUF cache and (Windows-only) `-RemoveDevCert` to also drop the self-signed cert

### Fixed
- Model registry no longer points the 16 GB tier at the gated `google/gemma-3-12b-it-GGUF` repo (anonymous downloads got HTTP 401 → entire AI feature disabled on Apple Silicon / NVIDIA workstations). Now uses the public `bartowski/google_gemma-3-12b-it-GGUF` re-up with the same model weights. `ensure_model_available` also gained an automatic fallback chain: on any download failure it tries the next-smaller tier instead of returning `None`. Worst case a 64 GB host falls back gemma-3-12b → qwen2.5-7b → qwen2.5-3b → qwen2.5-1.5b. Closes #AI-59
- Onboarding wizard now gates the final "Start" button on the LLM model download. The desktop launcher kicks off the download as soon as the app boots; the user can navigate all wizard steps in parallel while the model streams in the background. On the summary step the button stays disabled with a live "⏳ Waiting for AI model — 78% · ~3 min" indicator until the download reaches 100%. Auto-refreshes every 2 s without losing wizard state (#AI-58 follow-up)
- Onboarding wizard now seeds invisible LLM defaults during its apply step (`llm_backend=local_llama_cpp`, `cat_llm_backend=local_llama_cpp`, `llama_cpp_n_gpu_layers=0`, `llama_cpp_n_ctx=4096`, `llama_cpp_model_path` from `$LLAMA_CPP_MODEL_PATH` set by the launcher). Without this a fresh user who completed the wizard would still hit `llm_backend not configured` on the Import page
- Auto-skip migration tightened to require ALL four onboarding-wizard prerequisites before silently flagging an existing user as already-onboarded: `ui_language` set, `owner_names` non-empty, `llm_backend` configured, and ≥1 row in `account`. Previously a single populated taxonomy row was enough — but the default taxonomy ships seeded on every fresh install, so every brand-new user was being skipped past the wizard (#AI-58)
- Schema auto-invalidation: cached schemas (Flow 1) producing < 10% parse rate are automatically deleted and retried with Flow 2 (LLM re-classification)
- Orphan schema purge: startup migration removes `document_schema` rows without `header_sha256`, preventing unreachable stale entries
- Header SHA256 always populated on schema before persist, preventing orphan schemas from being created
- Sanitizer crash on pandas 2.x `string`-dtype columns with NaN: `astype(str).tolist()` was leaking float NaNs into the regex-based sanitizer, raising `TypeError: expected string or bytes-like object, got 'float'` on real bank CSV imports. Fix in `core/classifier.py` (`fillna("").astype(str)`) plus defensive guard in `core/sanitizer.py` (`redact_pii` coerces non-string inputs to `""`). 5 regression tests in `tests/test_sanitizer.py::TestNonStringInputs` (#108)
- Desktop bundle silent crash chain: five distinct bugs that made the PyInstaller `.app` exit with zero output on every launch — `console=False` swallowing stdout, `splash.html` path collapsing to `_MEIPASS` instead of `_MEIPASS/desktop/`, infinite re-exec loop when launcher.py invoked `subprocess.Popen([sys.executable, "-m", "streamlit", …])` (the executable is the bundle, which re-entered the launcher), Streamlit refusing `--server.port` because `global.developmentMode` defaulted to `true` in the bundle, and `support/logging.py` hard-coding `os.makedirs("logs")` which is read-only inside the bundle. All addressed in `fix(desktop): make the PyInstaller bundle actually start` (#AI-49 follow-up)
- Subprocess tree not killed on window close. `_cleanup` now uses `os.killpg` against process groups started with `start_new_session=True` so Streamlit + uvicorn + grandchildren die together. Previously the leftover Streamlit kept the port open and macOS Launch Services would treat the next double-click as "reopen of the running instance"
- `packaging/linux/build-rpm.sh` icon entry in `%files` made conditional on `ICON_PRESENT=1` so rpmbuild succeeds even when no `spendifai_256.png` is present on the build host (closes AI-56)
