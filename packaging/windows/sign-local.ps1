<#
.SYNOPSIS
    Spendif.ai - Windows MSIX signing, DEVELOPMENT ONLY.

.DESCRIPTION
    Wraps SignTool.exe to sign an MSIX with a .pfx file, for sideload
    testing on a development machine with a self-signed certificate.

    THIS IS NOT THE PRODUCTION PATH, and it cannot become one. Since June
    2023 the CA/Browser Forum requires the private key of an OV code
    signing certificate to live on a hardware token or an HSM, so a .pfx
    on disk no longer exists for a real certificate. The production
    certificate (Actalis, issued 2026-09-21) is signed remotely and the
    key never reaches this machine.

    Production signing:
      blueprint/sw_artifacts/tools/codesign/sign_windows.sh
    It runs on macOS or Linux, so signing a Windows artifact does not need
    a Windows machine. Building the MSIX still does, because makeappx.exe
    ships with the Windows SDK.

    The certificate Subject MUST match the <Identity Publisher="..."> in
    the MSIX manifest, otherwise SignTool fails with 0x8007000B. Build the
    package accordingly: build-msix.ps1 with no arguments for the dev
    placeholder used here, or -Production for the real certificate.

.PARAMETER Msix
    Path to the MSIX file. If omitted, picks the newest in build\.

.PARAMETER CertPath
    Path to .pfx file. Defaults to env:MSIX_CERT_PATH.

.PARAMETER CertPassword
    Password for the .pfx. Defaults to env:MSIX_CERT_PASSWORD.

.PARAMETER TimestampUrl
    RFC 3161 timestamp server (default: DigiCert).

.EXAMPLE
    $env:MSIX_CERT_PATH = "C:\path\to\spendifai.pfx"
    $env:MSIX_CERT_PASSWORD = "secret"
    .\packaging\windows\sign-local.ps1

.NOTES
    SELF-SIGNED CERT (for testing / sideload):
      $cert = New-SelfSignedCertificate -Type CodeSigningCert `
          -Subject "CN=SpendifAi Dev, O=Spendif.ai, C=IT" `
          -KeyUsage DigitalSignature -FriendlyName "Spendif.ai Dev" `
          -CertStoreLocation Cert:\CurrentUser\My `
          -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3","2.5.29.19={text}")
      $pwd = ConvertTo-SecureString -String "secret" -Force -AsPlainText
      Export-PfxCertificate -Cert $cert -FilePath spendifai.pfx -Password $pwd
      # Install for trust:
      Import-Certificate -FilePath spendifai.cer -CertStoreLocation Cert:\LocalMachine\TrustedPeople

    The production certificate was bought from Actalis in September 2026
    (OV, not EV: SmartScreen reputation accumulates with downloads instead
    of being granted immediately). See the blueprint codesign README for
    the full procedure, credentials layout and known limits.
#>
[CmdletBinding()]
param(
    [string]$Msix = "",
    [string]$CertPath = $env:MSIX_CERT_PATH,
    [string]$CertPassword = $env:MSIX_CERT_PASSWORD,
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $RepoRoot

# ── 1. Resolve MSIX ──────────────────────────────────────────────────────────
if (-not $Msix) {
    $Msix = (Get-ChildItem "build\SpendifAi-*.msix" -ErrorAction SilentlyContinue |
             Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}
if (-not $Msix -or -not (Test-Path $Msix)) {
    throw "MSIX not found. Run packaging\windows\build-msix.ps1 first or pass -Msix."
}

# ── 2. Validate cert ─────────────────────────────────────────────────────────
if (-not $CertPath) {
    throw "CertPath not set. Pass -CertPath or set env:MSIX_CERT_PATH."
}
if (-not (Test-Path $CertPath)) {
    throw "Certificate not found: $CertPath"
}
if (-not $CertPassword) {
    throw "CertPassword not set. Pass -CertPassword or set env:MSIX_CERT_PASSWORD."
}

# ── 3. Locate SignTool.exe ───────────────────────────────────────────────────
$SignTool = $null
$Candidates = @(
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe",
    "${env:ProgramFiles}\Windows Kits\10\bin\*\x64\signtool.exe"
)
foreach ($pattern in $Candidates) {
    $found = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue |
             Sort-Object -Property FullName -Descending |
             Select-Object -First 1
    if ($found) { $SignTool = $found.FullName; break }
}
if (-not $SignTool) {
    throw "signtool.exe not found. Install Windows SDK."
}

# ── 4. Sign ──────────────────────────────────────────────────────────────────
Write-Host "▸ Signing $Msix"
Write-Host "  Cert: $CertPath"
Write-Host "  Timestamp: $TimestampUrl"

& "$SignTool" sign `
    /fd SHA256 `
    /a `
    /f $CertPath `
    /p $CertPassword `
    /tr $TimestampUrl `
    /td SHA256 `
    $Msix

if ($LASTEXITCODE -ne 0) { throw "SignTool failed (exit $LASTEXITCODE)" }

# ── 5. Verify ────────────────────────────────────────────────────────────────
Write-Host "▸ Verifying..."
& "$SignTool" verify /pa /v $Msix
if ($LASTEXITCODE -ne 0) { throw "Signature verification failed" }

Write-Host ""
Write-Host "✔ $Msix signed and verified"
Write-Host ""
Write-Host "Install (requires cert trusted on target machine):"
Write-Host "  Add-AppxPackage $Msix"
