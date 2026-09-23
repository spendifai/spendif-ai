# ── Spendif.ai — Installer (Windows PowerShell) ────────────────────────────────
# Uso (PowerShell come utente normale):
#   irm https://raw.githubusercontent.com/spendifai/spendif-ai/main/install.ps1 | iex
# ─────────────────────────────────────────────────────────────────────────────
$ErrorActionPreference = "Stop"

$InstallDir = "$env:USERPROFILE\spendifai"
$ComposeUrl = "https://raw.githubusercontent.com/spendifai/spendif-ai/main/docker/docker-compose.release.yml"
$AppUrl     = "http://localhost:8501"

function Info    { param($msg) Write-Host "[spendif.ai] $msg" -ForegroundColor Cyan }
function Success { param($msg) Write-Host "✅ $msg" -ForegroundColor Green }
function Warn    { param($msg) Write-Host "⚠️  $msg" -ForegroundColor Yellow }
function Err     { param($msg) Write-Host "❌ $msg" -ForegroundColor Red; exit 1 }

# PowerShell 5.1 does not raise when a native command exits non-zero, not even
# under $ErrorActionPreference = "Stop" - that governs cmdlets, not executables.
# So `try { docker info } catch { }` caught nothing, and this script announced
# "Docker trovato" one line after Docker had printed that its engine was not
# running, then carried on to the end and opened a browser on an app that was
# never started. Every docker call goes through here now.
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$What,
        [Parameter(Mandatory = $true)][scriptblock]$Command
    )
    $global:LASTEXITCODE = 0
    & $Command
    if ($LASTEXITCODE -ne 0) {
        Err "$What non e' riuscito (codice $LASTEXITCODE).`n`nL'output qui sopra dice perche'."
    }
}

Write-Host ""
Write-Host "╔══════════════════════════════════════╗" -ForegroundColor White
Write-Host "║        Spendif.ai — Installer          ║" -ForegroundColor White
Write-Host "╚══════════════════════════════════════╝" -ForegroundColor White
Write-Host ""

# ── 1. Verifica Docker ────────────────────────────────────────────────────────
Info "Verifico Docker..."
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Err "Docker non trovato.`n`nInstalla Docker Desktop da: https://www.docker.com/products/docker-desktop/`nPoi riavvia questo script."
}

# Redirected to null on both streams: a stopped engine writes a long connect
# error to stderr, and the user does not need to read it to be told, in the
# next line, that Docker is not running.
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Err "Docker non è in esecuzione.`n`nAvvia Docker Desktop, attendi che l'icona smetta di caricare, poi riprova."
}

Success "Docker trovato: $(docker --version)"

# ── 2. AI locale (Ollama + gemma3:12b) ───────────────────────────────────────
Write-Host ""
Write-Host "Vuoi usare l'AI locale? (Ollama + gemma3:12b)" -ForegroundColor White
Write-Host "  • Nessuna API key richiesta — funziona completamente offline"
Write-Host "  • Richiede: ~8 GB di spazio disco e almeno 8 GB di RAM libera"
Write-Host "  • Prima volta: download del modello ~10-15 minuti"
Write-Host "  • Alternativa: inserire una API key (OpenAI/Anthropic) dopo l'avvio"
Write-Host ""

$UseOllama = $false
$reply = Read-Host "  Installa AI locale? [s/N]"
if ($reply -match '^(s|si|y|yes)$') {
    $UseOllama = $true
    Warn "Il download del modello (~8 GB) partirà in background dopo l'avvio."
}

$ProfileArgs = @()
if ($UseOllama) { $ProfileArgs = @("--profile", "ollama") }

# ── 3. Crea cartella di installazione ────────────────────────────────────────
Write-Host ""
Info "Cartella di installazione: $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Set-Location $InstallDir

# ── 4. Scarica docker-compose.release.yml ────────────────────────────────────
Info "Scarico la configurazione..."
Invoke-WebRequest -Uri $ComposeUrl -OutFile "docker-compose.yml" -UseBasicParsing
Success "Configurazione scaricata"

# ── 5. Pull immagine + avvio ──────────────────────────────────────────────────
Info "Scarico le immagini Docker (prima volta: ~500 MB, poi aggiornamenti incrementali)..."
Invoke-Native "Il download delle immagini" { docker compose @ProfileArgs pull }

Info "Avvio Spendif.ai..."
Invoke-Native "L'avvio dei container" { docker compose @ProfileArgs up -d }

# ── 6. Attendi che l'app sia pronta ───────────────────────────────────────────
Info "Attendo che l'app sia pronta..."
$ready = $false
for ($i = 1; $i -le 30; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "$AppUrl/_stcore/health" -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 2
}

if (-not $ready) {
    # Not a warning to scroll past: nothing below this point is true if the app
    # never answered, so the instructions and the browser stay unopened.
    Err ("L'app non risponde entro 60s.`n`nGuarda i log con:`n" +
         "  docker compose --project-directory $InstallDir logs -f`n`n" +
         "Poi riprova. I container restano avviati.")
}
Success "Spendif.ai è in esecuzione!"

# ── 7. Istruzioni finali ──────────────────────────────────────────────────────
Write-Host ""
Write-Host "🚀 Apri il browser su: $AppUrl" -ForegroundColor Green
Write-Host ""
if ($UseOllama) {
    Write-Host "  AI locale in download — attendere il completamento (vedi log):" -ForegroundColor Yellow
    Write-Host "  docker compose --project-directory $InstallDir logs -f ollama-init"
    Write-Host ""
    Write-Host "  Poi in Spendif.ai → ⚙️ Impostazioni → Backend LLM:"
    Write-Host "    Tipo: Ollama   URL: http://ollama:11434   Modello: gemma3:12b"
    Write-Host ""
}
$ProfileStr = if ($UseOllama) { " --profile ollama" } else { "" }
Write-Host "  Fermare:        docker compose --project-directory $InstallDir$ProfileStr down"
Write-Host "  Aggiornare:     docker compose --project-directory $InstallDir$ProfileStr pull; docker compose --project-directory $InstallDir$ProfileStr up -d"
Write-Host "  Log:            docker compose --project-directory $InstallDir logs -f"
Write-Host "  Disinstallare:  irm https://raw.githubusercontent.com/spendifai/spendif-ai/main/installer/uninstall.ps1 | iex" -ForegroundColor Cyan
Write-Host ""

Start-Process $AppUrl
