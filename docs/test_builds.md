# Test builds

How to download a test build of Spendif.ai, launch it on each supported system and send back the report that ticks your configuration in the [test matrix](compatibility.md#test-matrix).

Test builds come from the `develop` branch, before a release. They carry the version they were built from, for example `0.3.1+g9cac349`: the part after `+g` is the commit. The application never offers them an update.

The newest test build is always on the [test-build pre-release](https://github.com/spendifai/spendif-ai/releases/tag/test-build), at addresses that never change. No GitHub account and no extra tools are needed. It is not signed and is replaced by the next test build without notice.

## 1. Pick your configuration

| System | Architecture | Download | Section |
|---|---|---|---|
| Debian 12 and 13, Ubuntu 22.04 and newer, Linux Mint 21 and newer | amd64 (x86_64) | [spendifai-test_amd64.deb](https://github.com/spendifai/spendif-ai/releases/download/test-build/spendifai-test_amd64.deb) | [2](#2-debian-ubuntu-linux-mint-deb) |
| Debian 12 and 13, Ubuntu 22.04 and newer | arm64 (aarch64) | [spendifai-test_arm64.deb](https://github.com/spendifai/spendif-ai/releases/download/test-build/spendifai-test_arm64.deb) | [2](#2-debian-ubuntu-linux-mint-deb) |
| Fedora, Arch Linux, any other distribution with glibc 2.35 or newer | amd64 | [SpendifAi-test-linux-amd64.tar.gz](https://github.com/spendifai/spendif-ai/releases/download/test-build/SpendifAi-test-linux-amd64.tar.gz) | [3](#3-fedora-arch-linux-and-other-distributions-folder) |
| Fedora, Arch Linux, any other distribution with glibc 2.35 or newer | arm64 | [SpendifAi-test-linux-arm64.tar.gz](https://github.com/spendifai/spendif-ai/releases/download/test-build/SpendifAi-test-linux-arm64.tar.gz) | [3](#3-fedora-arch-linux-and-other-distributions-folder) |
| Windows 10 and 11 | x64 | [SpendifAi-test-windows-x64.zip](https://github.com/spendifai/spendif-ai/releases/download/test-build/SpendifAi-test-windows-x64.zip) | [4](#4-windows-x64-folder) |

Your architecture: `uname -m` on Linux (`x86_64` is amd64, `aarch64` is arm64). Windows on ARM is not covered: the local model does not run there. Checksums: [SHA256SUMS](https://github.com/spendifai/spendif-ai/releases/download/test-build/SHA256SUMS).

Fedora and Arch get the folder rather than a package for now: their `.rpm` and `PKGBUILD` have not yet moved to the self-contained build.

## 2. Debian, Ubuntu, Linux Mint (.deb)

```bash
ARCH=$(dpkg --print-architecture)          # amd64 or arm64
wget -O /tmp/spendifai-test.deb \
  "https://github.com/spendifai/spendif-ai/releases/download/test-build/spendifai-test_${ARCH}.deb"
sudo apt install /tmp/spendifai-test.deb
```

**Launch:** from the applications menu, **Spendif.ai**. The interface opens in your browser: on Linux there is no separate window. From a terminal: `/opt/spendifai/launch.sh`.

**Graphics card:** acceleration needs a Vulkan driver.

```bash
sudo apt install mesa-vulkan-drivers vulkan-tools    # AMD and Intel; NVIDIA's own driver includes Vulkan
vulkaninfo --summary                                 # lists your card if the driver is in place
```

**Uninstall:**

```bash
sudo apt remove spendifai
```

Your data stays in `~/.spendifai`. Delete that folder only if it holds nothing but test data.

## 3. Fedora, Arch Linux and other distributions (folder)

The folder runs where you put it, with no installation.

```bash
case "$(uname -m)" in x86_64) ARCH=amd64 ;; aarch64) ARCH=arm64 ;; esac
rm -rf ~/SpendifAi
curl -L "https://github.com/spendifai/spendif-ai/releases/download/test-build/SpendifAi-test-linux-${ARCH}.tar.gz" \
  | tar -xz -C ~
```

**Launch:** `~/SpendifAi/SpendifAi`. The interface opens in your browser.

**Graphics card:**

```bash
# Fedora
sudo dnf install vulkan-loader mesa-vulkan-drivers vulkan-tools
# Arch, AMD
sudo pacman -S vulkan-icd-loader vulkan-radeon vulkan-tools
# Arch, NVIDIA (with the nvidia driver)
sudo pacman -S vulkan-icd-loader nvidia-utils vulkan-tools

vulkaninfo --summary
```

**Uninstall:** `rm -rf ~/SpendifAi`. Your data stays in `~/.spendifai`.

For the report, a folder counts as installed once it starts, and as uninstalled once it is deleted. There is no menu entry: tick "Started" if it started from the file.

## 4. Windows x64 (folder)

With the browser: download [SpendifAi-test-windows-x64.zip](https://github.com/spendifai/spendif-ai/releases/download/test-build/SpendifAi-test-windows-x64.zip), right-click it, **Properties**, tick **Unblock**, **OK**, then **Extract All**. Open the `SpendifAi` folder and start `SpendifAi.exe`.

Or in PowerShell:

```powershell
$ZIP = "$env:TEMP\SpendifAi-test.zip"
Invoke-WebRequest -Uri "https://github.com/spendifai/spendif-ai/releases/download/test-build/SpendifAi-test-windows-x64.zip" -OutFile $ZIP
if (Test-Path "$HOME\SpendifAi") { Remove-Item -Recurse -Force "$HOME\SpendifAi" }
Expand-Archive -Path $ZIP -DestinationPath $HOME
& "$HOME\SpendifAi\SpendifAi.exe"
```

The build is not signed. If Windows SmartScreen stops it: **More info**, then **Run anyway**.

**Graphics card:** the Vulkan driver comes with the card's own driver (NVIDIA, AMD, Intel). Nothing to install separately.

**Uninstall:** delete the folder, `Remove-Item -Recurse -Force "$HOME\SpendifAi"`. Your data stays in `%USERPROFILE%\.spendifai`.

There is no Start menu entry: tick "Started" if it started from the file.

## 5. Run the test and send the report

1. Start the application and download the suggested model, if it asks.
2. Import a bank or card statement.
3. Open **Diagnostics** in the menu. Under **Active compute engines**, `Vulkan0` means the graphics card is in use; `CPU` alone means the processor does the work.
4. Send the report, one of two ways:
   - **With a GitHub account:** **Send as a test report on GitHub**. A form opens with the document already in it. Tick the steps that went through and submit.
   - **Without:** **Save the document**, then the email link below the button. The email arrives with a checklist in it: put an `x` in the brackets of each step that went through, like `[x]`, attach the saved document and send.
5. Uninstall (section 2, 3 or 4) and, on GitHub, edit the issue to tick **Uninstalled cleanly**. By email, reply to your own message with the box ticked.

The four steps:

| Step | Ticked when |
|---|---|
| Installed | the package installed, or the folder started |
| Started from the application menu or icon | the interface opened |
| Imported a statement | a statement went through and its rows were categorised |
| Uninstalled cleanly | the package was removed, or the folder deleted, without errors |

A configuration gets ✅ in the test matrix when one report has all four, and ◐ when a report arrived with some missing.

## 6. Other builds, from GitHub Actions

Every build of `develop` is also kept for 14 days as a build artifact, including the ones never published as the test build. Downloading those needs a signed-in GitHub account and the `gh` command:

```bash
gh auth login
REPO=spendifai/spendif-ai
RUN=$(gh run list -R "$REPO" --workflow build-llama-wheels.yml --branch develop \
      --status success -L 1 --json databaseId -q '.[0].databaseId')
gh run download "$RUN" -R "$REPO" -n spendifai-deb-amd64 -D /tmp/spendifai
```

Artifact names: `spendifai-deb-amd64`, `spendifai-deb-arm64`, `linux-bundle-amd64`, `linux-bundle-arm64`, `windows-bundle-x64`. A Linux folder downloaded this way loses its executable bit: `chmod +x SpendifAi/SpendifAi`.

## For maintainers

**Publishing a test build:** run the **Publish test build** workflow on `develop`. It builds the packages and replaces the files of the `test-build` pre-release; given the id of a finished **Build llama wheels** run of `develop` in `from_run`, it publishes that run's packages without building again.

```bash
gh workflow run publish-test-build.yml -R spendifai/spendif-ai --ref develop
gh workflow run publish-test-build.yml -R spendifai/spendif-ai --ref develop -f from_run=<run id>
```

**An emailed report:** save the email as an `.eml` file (drag it out of the mail client), then:

```bash
python3 scripts/compat_report.py --mail ~/Downloads/report.eml \
    --store docs/compatibility/reports.jsonl --docs docs
```

It reads the attached document and the ticked boxes with the same checks the issue form goes through, and rewrites `docs/compatibility.md` and `docs/compatibility.it.md`. Commit them in a pull request to `develop`. A document without its email: `--xml report.xml --outcomes installed,started,imported,uninstalled`.

The configurations in the matrix live in [`docs/compatibility/matrix.json`](compatibility/matrix.json).
