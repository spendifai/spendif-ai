# Build di prova

Come scaricare una build di prova di Spendif.ai, avviarla su ciascun sistema supportato e mandare il resoconto che spunta la tua configurazione nella [matrice di prova](compatibility.it.md#matrice-di-prova).

Le build di prova escono dal branch `develop`, prima di una release. Portano la versione da cui sono state costruite, per esempio `0.3.1+g9cac349`: la parte dopo `+g` è il commit. L'applicazione non propone mai loro un aggiornamento.

L'ultima build di prova è sempre sulla [pre-release test-build](https://github.com/spendifai/spendif-ai/releases/tag/test-build), a indirizzi che non cambiano mai. Non servono un account GitHub né altri strumenti. Non è firmata e viene sostituita dalla build di prova successiva senza preavviso.

## 1. Scegli la tua configurazione

| Sistema | Architettura | Scarica | Sezione |
|---|---|---|---|
| Debian 12 e 13, Ubuntu 22.04 e successive, Linux Mint 21 e successive | amd64 (x86_64) | [spendifai-test_amd64.deb](https://github.com/spendifai/spendif-ai/releases/download/test-build/spendifai-test_amd64.deb) | [2](#2-debian-ubuntu-linux-mint-deb) |
| Debian 12 e 13, Ubuntu 22.04 e successive | arm64 (aarch64) | [spendifai-test_arm64.deb](https://github.com/spendifai/spendif-ai/releases/download/test-build/spendifai-test_arm64.deb) | [2](#2-debian-ubuntu-linux-mint-deb) |
| Fedora, Arch Linux, qualunque altra distribuzione con glibc 2.35 o successiva | amd64 | [SpendifAi-test-linux-amd64.tar.gz](https://github.com/spendifai/spendif-ai/releases/download/test-build/SpendifAi-test-linux-amd64.tar.gz) | [3](#3-fedora-arch-linux-e-altre-distribuzioni-cartella) |
| Fedora, Arch Linux, qualunque altra distribuzione con glibc 2.35 o successiva | arm64 | [SpendifAi-test-linux-arm64.tar.gz](https://github.com/spendifai/spendif-ai/releases/download/test-build/SpendifAi-test-linux-arm64.tar.gz) | [3](#3-fedora-arch-linux-e-altre-distribuzioni-cartella) |
| Windows 10 e 11 | x64 | [SpendifAi-test-windows-x64.zip](https://github.com/spendifai/spendif-ai/releases/download/test-build/SpendifAi-test-windows-x64.zip) | [4](#4-windows-x64-cartella) |

La tua architettura: `uname -m` su Linux (`x86_64` è amd64, `aarch64` è arm64). Windows su ARM non è coperto: lì il modello locale non gira. Somme di controllo: [SHA256SUMS](https://github.com/spendifai/spendif-ai/releases/download/test-build/SHA256SUMS).

Fedora e Arch per ora ricevono la cartella invece di un pacchetto: il loro `.rpm` e il `PKGBUILD` non sono ancora passati alla build autosufficiente.

## 2. Debian, Ubuntu, Linux Mint (.deb)

```bash
ARCH=$(dpkg --print-architecture)          # amd64 oppure arm64
wget -O /tmp/spendifai-test.deb \
  "https://github.com/spendifai/spendif-ai/releases/download/test-build/spendifai-test_${ARCH}.deb"
sudo apt install /tmp/spendifai-test.deb
```

**Avvio:** dal menu delle applicazioni, **Spendif.ai**. L'interfaccia si apre nel browser: su Linux non c'è una finestra a parte. Da terminale: `/opt/spendifai/launch.sh`.

**Scheda grafica:** l'accelerazione richiede un driver Vulkan.

```bash
sudo apt install mesa-vulkan-drivers vulkan-tools    # AMD e Intel; il driver proprietario NVIDIA include Vulkan
vulkaninfo --summary                                 # elenca la tua scheda se il driver c'è
```

**Disinstallazione:**

```bash
sudo apt remove spendifai
```

I tuoi dati restano in `~/.spendifai`. Cancella quella cartella solo se contiene soltanto dati di prova.

## 3. Fedora, Arch Linux e altre distribuzioni (cartella)

La cartella funziona dove la metti, senza installazione.

```bash
case "$(uname -m)" in x86_64) ARCH=amd64 ;; aarch64) ARCH=arm64 ;; esac
rm -rf ~/SpendifAi
curl -L "https://github.com/spendifai/spendif-ai/releases/download/test-build/SpendifAi-test-linux-${ARCH}.tar.gz" \
  | tar -xz -C ~
```

**Avvio:** `~/SpendifAi/SpendifAi`. L'interfaccia si apre nel browser.

**Scheda grafica:**

```bash
# Fedora
sudo dnf install vulkan-loader mesa-vulkan-drivers vulkan-tools
# Arch, AMD
sudo pacman -S vulkan-icd-loader vulkan-radeon vulkan-tools
# Arch, NVIDIA (con il driver nvidia)
sudo pacman -S vulkan-icd-loader nvidia-utils vulkan-tools

vulkaninfo --summary
```

**Disinstallazione:** `rm -rf ~/SpendifAi`. I tuoi dati restano in `~/.spendifai`.

Per il resoconto, una cartella conta come installata quando parte, e come disinstallata quando la cancelli. Non c'è una voce di menu: spunta "Started" se è partita dal file.

## 4. Windows x64 (cartella)

Con il browser: scarica [SpendifAi-test-windows-x64.zip](https://github.com/spendifai/spendif-ai/releases/download/test-build/SpendifAi-test-windows-x64.zip), tasto destro, **Proprietà**, spunta **Annulla blocco**, **OK**, poi **Estrai tutto**. Apri la cartella `SpendifAi` e avvia `SpendifAi.exe`.

Oppure in PowerShell:

```powershell
$ZIP = "$env:TEMP\SpendifAi-test.zip"
Invoke-WebRequest -Uri "https://github.com/spendifai/spendif-ai/releases/download/test-build/SpendifAi-test-windows-x64.zip" -OutFile $ZIP
if (Test-Path "$HOME\SpendifAi") { Remove-Item -Recurse -Force "$HOME\SpendifAi" }
Expand-Archive -Path $ZIP -DestinationPath $HOME
& "$HOME\SpendifAi\SpendifAi.exe"
```

La build non è firmata. Se Windows SmartScreen la blocca: **Ulteriori informazioni**, poi **Esegui comunque**.

**Scheda grafica:** il driver Vulkan arriva con il driver della scheda (NVIDIA, AMD, Intel). Non c'è niente da installare a parte.

**Disinstallazione:** cancella la cartella, `Remove-Item -Recurse -Force "$HOME\SpendifAi"`. I tuoi dati restano in `%USERPROFILE%\.spendifai`.

Non c'è una voce nel menu Start: spunta "Started" se è partita dal file.

## 5. Fai la prova e manda il resoconto

1. Avvia l'applicazione e scarica il modello suggerito, se te lo chiede.
2. Importa un estratto conto della banca o della carta.
3. Apri **Diagnostica** dal menu. Alla voce **Motori di calcolo attivi**, `Vulkan0` vuol dire che lavora la scheda grafica; solo `CPU` vuol dire che lavora il processore.
4. Manda il resoconto, in uno dei due modi:
   - **Con un account GitHub:** **Invia come resoconto di prova su GitHub**. Si apre un modulo con il documento già dentro. Spunta i passi riusciti e invia.
   - **Senza:** **Salva il documento**, poi il link email sotto il pulsante. L'email arriva con un elenco di caselle: metti una `x` tra le parentesi di ogni passo riuscito, così `[x]`, allega il documento salvato e invia.
5. Disinstalla (sezione 2, 3 o 4) e, su GitHub, modifica la issue per spuntare **Uninstalled cleanly**. Via email, rispondi al tuo stesso messaggio con la casella spuntata.

I quattro passi (le etichette restano in inglese, come nel modulo):

| Passo | Si spunta quando |
|---|---|
| Installed | il pacchetto si è installato, o la cartella è partita |
| Started from the application menu or icon | l'interfaccia si è aperta |
| Imported a statement | un estratto conto è passato e i movimenti hanno ricevuto la categoria |
| Uninstalled cleanly | il pacchetto è stato rimosso, o la cartella cancellata, senza errori |

Una configurazione riceve ✅ nella matrice quando un resoconto li ha tutti e quattro, e ◐ quando è arrivato un resoconto con qualche passo mancante.

## 6. Altre build, da GitHub Actions

Ogni build di `develop` resta disponibile per 14 giorni anche come file di build, comprese quelle mai pubblicate come build di prova. Per scaricarle servono un account GitHub con l'accesso fatto e il comando `gh`:

```bash
gh auth login
REPO=spendifai/spendif-ai
RUN=$(gh run list -R "$REPO" --workflow build-llama-wheels.yml --branch develop \
      --status success -L 1 --json databaseId -q '.[0].databaseId')
gh run download "$RUN" -R "$REPO" -n spendifai-deb-amd64 -D /tmp/spendifai
```

Nomi dei file: `spendifai-deb-amd64`, `spendifai-deb-arm64`, `linux-bundle-amd64`, `linux-bundle-arm64`, `windows-bundle-x64`. Una cartella Linux scaricata così perde il permesso di esecuzione: `chmod +x SpendifAi/SpendifAi`.

## Per chi mantiene il progetto

**Pubblicare una build di prova:** lancia il workflow **Publish test build** su `develop`. Costruisce i pacchetti e sostituisce i file della pre-release `test-build`; se in `from_run` gli dai l'id di una run **Build llama wheels** di `develop` già finita, pubblica i pacchetti di quella run senza ricostruire.

```bash
gh workflow run publish-test-build.yml -R spendifai/spendif-ai --ref develop
gh workflow run publish-test-build.yml -R spendifai/spendif-ai --ref develop -f from_run=<id della run>
```

**Un resoconto arrivato via email:** salva l'email come file `.eml` (trascinala fuori dal client di posta), poi:

```bash
python3 scripts/compat_report.py --mail ~/Downloads/report.eml \
    --store docs/compatibility/reports.jsonl --docs docs
```

Legge il documento allegato e le caselle spuntate con gli stessi controlli del modulo delle issue, e riscrive `docs/compatibility.md` e `docs/compatibility.it.md`. Committali in una pull request verso `develop`. Un documento senza la sua email: `--xml report.xml --outcomes installed,started,imported,uninstalled`.

Le configurazioni della matrice stanno in [`docs/compatibility/matrix.json`](compatibility/matrix.json).
