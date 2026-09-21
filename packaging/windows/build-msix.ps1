<#
.SYNOPSIS
    Spendif.ai — Windows MSIX builder (local + CI parity)

.DESCRIPTION
    Produces build\SpendifAi-<version>.msix (unsigned).
    Mirrors the CI job in .github/workflows/release.yml.

.PARAMETER Version
    4-part version (e.g. 3.0.0.0). If omitted, reads VERSION file and
    pads to 4 parts.

.PARAMETER Publisher
    X.500 DN that MUST match the signing certificate Subject exactly.
    A mismatch fails at signing time with 0x8007000B, and every signing
    attempt burns a one-time OTP, so get this right before building.

    Default is the self-signed dev placeholder used for sideload testing.
    For production use -Production, which takes the DN from the environment
    rather than from this file. Do not assume the DN matches the product or
    company name: it is whatever the certificate authority issued.

.PARAMETER Production
    Build with the production publisher DN instead of the dev placeholder.

    The DN is NOT stored in this repository: it is the X.500 subject of the
    code signing certificate, and it identifies the certificate holder. It is
    read from the MSIX_PUBLISHER environment variable, which comes from a
    repository secret in CI and from the local credentials file otherwise.

    To recover the value from the certificate:
      openssl x509 -in <cert>.cer -noout -subject -nameopt RFC2253
    Keep every component byte for byte, then make two rewrites: spell
    stateOrProvince as OID.2.5.4.8=, and separate components with a comma
    AND A SPACE. RFC2253 prints bare commas, which the manifest schema
    rejects with the very same error as a bad abbreviation, so the two
    defects are easy to confuse: the log masks the value, and only the
    pattern in the message tells them apart.

    That spelling looks pedantic and is the only one that works. The two
    tools that read this DN disagree: makeappx validates the manifest against
    a schema whose list of accepted abbreviations contains S and NOT ST, and
    rejects the package with "error C00CE169 ... violates pattern constraint";
    the signer parses the same DN with BouncyCastle, which does not know S and
    rejects it with "Unknown object id - S passed to distinguished name". The
    schema also admits the numeric OID form, and BouncyCastle reads it as the
    same attribute as the certificate ST=, so that form is the only meeting
    point. Both measured 2026-09-21, the first after a build failed on it.

    Still unverified: that Windows accepts this form at install time. If an
    install fails with 0x8007000B on the publisher, this is where to look.

.PARAMETER PublisherDisplay
    Friendly publisher name (shown in Add/Remove Programs).

.PARAMETER Architecture
    x64 (default) | arm64 | neutral

.PARAMETER SkipPyInstaller
    Reuse existing dist\SpendifAi\ instead of rebuilding.

.PARAMETER WithSSM
    Compile llama-cpp-python from git HEAD with CUDA support before running
    PyInstaller, so the bundle contains an SSM-capable llama.cpp (required
    for Qwen 3.5 9B and other SSM-hybrid-architecture models).
    Requires VS Build Tools + C++ workload; NVIDIA CUDA Toolkit for GPU.

.EXAMPLE
    cd sw_artifacts
    .\packaging\windows\build-msix.ps1
    .\packaging\windows\build-msix.ps1 -Version 3.1.0.0 -Production
    .\packaging\windows\build-msix.ps1 -WithSSM

.NOTES
    Requires Windows SDK (for makeappx.exe). Install via:
      winget install Microsoft.WindowsSDK.10.0.22621
    Output is unsigned. MSIX cannot be installed in normal mode without a
    trusted signature, so sign before distributing:
      production: blueprint/sw_artifacts/tools/codesign/sign_windows.sh
                  (runs on macOS or Linux, no Windows machine needed)
      dev only:   packaging\windows\sign-local.ps1 with a self-signed cert
#>
[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$Publisher = "CN=SpendifAi Dev, O=Spendif.ai, C=IT",
    [string]$PublisherDisplay = "Spendif.ai",
    [switch]$Production,
    [ValidateSet("x64", "arm64", "neutral")]
    [string]$Architecture = "x64",
    [switch]$SkipPyInstaller,
    [switch]$WithSSM
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $RepoRoot

# The production publisher DN is personal data of the certificate holder, so it
# is never committed. It arrives from the environment, and the manifest must
# reproduce the certificate subject character by character or signing fails.
if ($Production) {
    if ($PSBoundParameters.ContainsKey("Publisher")) {
        throw "-Production and -Publisher are mutually exclusive: pick one."
    }
    if (-not $env:MSIX_PUBLISHER) {
        throw "-Production needs MSIX_PUBLISHER set to the certificate subject DN. See the -Production help in this script for how to recover it."
    }
    $Publisher = $env:MSIX_PUBLISHER
    # Deliberately not printed: it would end up in public CI logs.
    Write-Host "Publisher taken from MSIX_PUBLISHER"
}

# Validate the DN against the manifest schema BEFORE building anything. makeappx
# applies this same pattern, but it does so after PyInstaller has run, it reports
# a masked value because the DN comes from a secret, and it raises one identical
# C00CE169 for every possible defect. Ten minutes to learn nothing. The pattern
# below is the one makeappx prints in that error, kept verbatim.
$dnAttr = '(CN|L|O|OU|E|C|S|STREET|T|G|I|SN|DC|SERIALNUMBER|Description|PostalCode|POBox|Phone|X21Address|dnQualifier|(OID\.(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))+))'
$dnVal  = '(([^,+="<>#;])+|".*")'
$dnPattern = '^' + $dnAttr + '=' + $dnVal + '(, (' + $dnAttr + '=' + $dnVal + '))*$'
if ($Publisher -cnotmatch $dnPattern) {
    # Say which defect it is, since the schema cannot. These two account for
    # every failure seen so far, and the value itself is never echoed.
    $hint = @()
    if ($Publisher -cmatch ',(?! )')  { $hint += "components must be separated by a comma AND a space; RFC2253 output uses bare commas" }
    if ($Publisher -cmatch '(^|, )ST=') { $hint += "stateOrProvince must be spelled OID.2.5.4.8=, not ST=: the schema accepts S and the numeric OID, and the signer accepts ST and the numeric OID, so only the OID form passes both" }
    if (-not $hint) { $hint += "check every component against the pattern; attribute names are case sensitive" }
    throw "the publisher DN does not match the manifest schema.`n  " + ($hint -join "`n  ")
}

# ── 1. Resolve version (must be 4 parts) ─────────────────────────────────────
if (-not $Version) {
    if (Test-Path "VERSION") {
        $Version = (Get-Content "VERSION" -Raw).Trim()
    } else {
        $Version = "0.0.0"
    }
}
$parts = $Version.Split('.')
while ($parts.Count -lt 4) { $parts += "0" }
$Version4 = ($parts[0..3] -join '.')
Write-Host "▸ Spendif.ai MSIX builder — version $Version4 ($Architecture)"

# ── 1b. SSM build (optional) ─────────────────────────────────────────────────
# Compiles llama-cpp-python from git HEAD with CUDA support so PyInstaller
# bundles an SSM-capable llama.cpp (required for Qwen 3.5 9B).
if ($WithSSM) {
    Write-Host "▸ Building llama-cpp-python with SSM support (CUDA)..."
    & "$PSScriptRoot\..\..\scripts\setup-ssm.ps1" -Yes
    if ($LASTEXITCODE -ne 0) { throw "SSM build failed — check VS Build Tools and CUDA Toolkit" }
    Write-Host "✔ SSM build complete"
}

# ── 1c. Stamp build info ─────────────────────────────────────────────────────
$BuildTs = (Get-Date -Format "yyyy-MM-dd HH:mm")
@"
# Generated at build time — do not edit manually.
BUILD_TIME = "$BuildTs"
BUILD_VERSION = "$Version4"
"@ | Set-Content -Path "core\_build_info.py" -Encoding UTF8
Write-Host "▸ Build stamp: v$Version4 @ $BuildTs"

# ── 2. PyInstaller ───────────────────────────────────────────────────────────
$AppRoot = "dist\SpendifAi"
$AppExe = "$AppRoot\SpendifAi.exe"

if (-not $SkipPyInstaller) {
    Write-Host "▸ Building .exe via PyInstaller..."
    & uv run --extra desktop pyinstaller desktop.spec --noconfirm --clean
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
}

if (-not (Test-Path $AppExe)) {
    throw "$AppExe not found. Run without -SkipPyInstaller."
}
Write-Host "✔ $AppExe ready"

# ── 3. Stage MSIX layout ─────────────────────────────────────────────────────
$Stage = "build\msix-stage"
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
New-Item -ItemType Directory -Force -Path "$Stage\SpendifAi" | Out-Null
New-Item -ItemType Directory -Force -Path "$Stage\Assets" | Out-Null

Write-Host "▸ Staging payload..."
Copy-Item -Path "$AppRoot\*" -Destination "$Stage\SpendifAi\" -Recurse -Force

# ── 4. Assets (logos) ────────────────────────────────────────────────────────
# MSIX requires specific PNG assets. If a project-provided ICO exists,
# we render it to the required sizes via System.Drawing; otherwise we
# generate flat-colour placeholders so the package is still valid.
$Ico = "packaging\windows\spendifai.ico"
$Sizes = @{
    "StoreLogo.png"        = 50
    "Square44x44Logo.png"  = 44
    "Square150x150Logo.png" = 150
    "Wide310x150Logo.png"  = @(310, 150)
}

Add-Type -AssemblyName System.Drawing
foreach ($name in $Sizes.Keys) {
    $dims = $Sizes[$name]
    if ($dims -is [array]) { $w = $dims[0]; $h = $dims[1] } else { $w = $dims; $h = $dims }
    $out = "$Stage\Assets\$name"
    $bmp = New-Object System.Drawing.Bitmap $w, $h
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.Clear([System.Drawing.Color]::FromArgb(0, 184, 148))  # brand teal
    if (Test-Path $Ico) {
        try {
            $icon = New-Object System.Drawing.Icon($Ico, $w, $h)
            $g.DrawIcon($icon, 0, 0)
            $icon.Dispose()
        } catch { }
    }
    $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
}
Write-Host "✔ Assets generated"

# ── 5. Render AppxManifest.xml from template ─────────────────────────────────
$Template = "packaging\windows\AppxManifest.xml.in"
$Manifest = "$Stage\AppxManifest.xml"
if (-not (Test-Path $Template)) { throw "$Template not found" }

(Get-Content $Template -Raw) `
    -replace '@VERSION@',           $Version4 `
    -replace '@PUBLISHER@',         $Publisher `
    -replace '@PUBLISHER_DISPLAY@', $PublisherDisplay `
    -replace '@ARCH@',              $Architecture |
    Set-Content -Path $Manifest -Encoding UTF8

Write-Host "✔ Manifest rendered"

# ── 6. Locate makeappx.exe ───────────────────────────────────────────────────
$MakeAppx = $null
$Candidates = @(
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\makeappx.exe",
    "${env:ProgramFiles}\Windows Kits\10\bin\*\x64\makeappx.exe"
)
foreach ($pattern in $Candidates) {
    $found = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue |
             Sort-Object -Property FullName -Descending |
             Select-Object -First 1
    if ($found) { $MakeAppx = $found.FullName; break }
}
if (-not $MakeAppx) {
    throw "makeappx.exe not found. Install Windows SDK: winget install Microsoft.WindowsSDK.10.0.22621"
}
Write-Host "▸ Using makeappx: $MakeAppx"

# ── 7. Pack ──────────────────────────────────────────────────────────────────
$MsixName = "SpendifAi-$Version.msix"
$MsixPath = "build\$MsixName"
if (Test-Path $MsixPath) { Remove-Item $MsixPath -Force }

& "$MakeAppx" pack /d $Stage /p $MsixPath /o
if ($LASTEXITCODE -ne 0) { throw "makeappx pack failed (exit $LASTEXITCODE)" }

$size = "{0:N1} MB" -f ((Get-Item $MsixPath).Length / 1MB)
Write-Host ""
Write-Host "✔ MSIX ready: $MsixPath ($size)"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  • Sign:    .\packaging\windows\sign-local.ps1 -Msix $MsixPath"
Write-Host "  • Install: Add-AppxPackage $MsixPath  (requires trusted signature)"
