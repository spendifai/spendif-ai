# Changelog

*English version: [CHANGELOG.md](CHANGELOG.md).*

Tutti i cambiamenti rilevanti di Spendif.ai sono documentati in questo file.
Il formato segue [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Il versioning segue [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-08-25

### Added
- Barra privacy a semaforo nella pagina di configurazione LLM: mostra a colpo d'occhio cosa esce dalla macchina per ciascun backend, prima che l'utente ne scelga uno (#AI-229, #161)
- Landing page: metadati SEO + GEO, `sitemap.xml`, `robots.txt` e `llms.txt` (#155)
- Pagina di feedback alpha su gh-pages, basata su un form Tally (#162)
- Pulsanti di download diretto degli installer (DMG / MSIX / .deb / .rpm) sulla pagina getting-started, in tutte e 9 le lingue (#163)
- Nuovo brand mark applicato alle landing page e alle icone dell'app bundle (#159)

### Changed
- Navigazione della sidebar completamente tradotta in italiano (#158)
- Documentazione: Python >= 3.12 dichiarato nelle istruzioni di installazione, landing di brand in inglese, link reciproco al README italiano (#164)
- Riferimenti di versione allineati tra README e `pyproject.toml` (#160)

### Fixed
- Finestra desktop: il pulsante Deploy di Streamlit e la sua toolbar non sono più visibili nella finestra pywebview; `toolbarMode = viewer` mantiene raggiungibili Settings e Tema (#165)
- Coupling: il layer UI non importa più direttamente `db`/`core`, ed è riparato il gate CI sul coupling che passava silenziosamente (#157)

### Security
- Alzati i floor delle dipendenze dopo una segnalazione di pip-audit: `gitpython >= 3.1.58` (GHSA-9rj7-rf2p-w77r, GHSA-4gmw-gg2m-w46p, CVE-2026-76217, GHSA-wvpp-8hx9-p66j, GHSA-jm78-9fvv-mhgr) e `cryptography >= 50.0.0` (PYSEC-2026-3552). Il lockfile risolve a gitpython 3.1.59 e cryptography 50.0.0

### Known limitations
- Il DMG macOS e l'MSIX Windows **non sono firmati**: Gatekeeper e SmartScreen mostreranno un avviso al primo avvio. Firma del codice e notarization sono tracciate separatamente
- I manifest winget e il cask Homebrew in `packaging/` fanno ancora riferimento all'artefatto ZIP Windows ritirato e a un nome file `Spendif.ai-<versione>.dmg`; per questa release nessuno dei due canali viene sottomesso

## [0.1.0] - 2026-07-29

*Prima release pubblica, taggata e pubblicata il 2026-07-29 con gli installer per macOS, Windows e Linux. Le voci qui sotto erano prima divise tra una sezione `Unreleased` e una sezione `0.1.0` datata con l'inizio dello sviluppo (2026-04-06); sono state unite qui per corrispondere a ciò che è effettivamente uscito negli artefatti v0.1.0.*

### Added
- Prima release
- Import di estratti conto CSV/XLSX (9 strumenti finanziari italiani)
- Categorizzazione AI locale via llama.cpp (Qwen3.5, Gemma4, Phi4, Llama3.2)
- Matching delle controparti con consapevolezza NSI/OSI
- Cache storica, regole utente, personalizzazione della tassonomia
- App bundle macOS, integrazione con Spotlight
- Installer Windows via winget/PowerShell
- Dashboard di analytics interattiva (in arrivo)
- App desktop nativa: finestra pywebview che incorpora Streamlit (niente Terminal, niente browser)
- Download automatico del modello LLM consigliato in base al rilevamento hardware (RAM/GPU)
- Setup llama.cpp zero-config al primo avvio
- Splash screen con barra di avanzamento del download
- Installer macOS (`packaging/macos/install.sh`) con launcher per finestra nativa
- Installer Windows (`packaging/windows/install.ps1`) con launcher per finestra nativa
- Installer Ubuntu/Debian (`packaging/linux/install-debian.sh`) e builder .deb
- Installer Red Hat/Fedora (`packaging/linux/install-redhat.sh`) e builder .rpm
- File spec PyInstaller per generare .app standalone (macOS) e .exe (Windows)
- Script di disinstallazione per macOS, Windows (con flag `-Silent`) e Linux
- Registrazione in Add/Remove Programs di Windows durante l'installazione
- Workflow CI Linux (`release-linux.yml`): build di .deb/.rpm, smoke test in container, allegati alla GitHub Release
- Identificatore winget rinominato in `SpendifAi.SpendifAi`
- Installer MSIX per Windows (`packaging/windows/build-msix.ps1` + `AppxManifest.xml.in`), sostituisce il precedente artefatto ZIP-only per Windows
- Script di firma locale: `packaging/macos/sign-local.sh` (codesign + notarytool + stapler) e `packaging/windows/sign-local.ps1` (wrapper SignTool)
- Builder DMG locale (`packaging/macos/build-dmg.sh`) che replica il job CI per riproducibilità offline
- Modello release CI ibrido: `release.yml` builda tutti e quattro gli installer unsigned e pubblica una GitHub Release in **draft**; l'owner firma DMG e MSIX in locale e li sostituisce via `gh release upload --clobber` prima di rimuovere `--draft`. Documentato in `docs/release_process.it.md` §2bis (EN+IT)
- Pagina "Primo avvio" su gh-pages, copertura completa 9 lingue (`getting-started.{html,en,de,es,fr,ja,nl,pl,pt}.html`): guida illustrata a 3 step (Download → Installa → Primo avvio) con bottoni di download per DMG/MSIX/.deb/.rpm e placeholder per screenshot (`assets/screenshots/`). Ogni pagina include il beacon Cloudflare Web Analytics e il selettore lingua completo
- Aggiornata la sezione "Primo avvio" di `installation_{macos,windows}.{md,it.md}` per descrivere il flusso nativo pywebview (splash + download modello + wizard onboarding), sostituendo la sequenza obsoleta Terminale/browser che vale solo per i vecchi script `install.sh`/`install.ps1`
- Landing page (tutte le 9 lingue: `index.html` IT, `index.{en,de,es,fr,ja,nl,pl,pt}.html`): aggiunto CTA localizzato "Scarica installer" che punta alla pagina getting-started corrispondente alla lingua, sopra le tab esistenti con gli script curl
- README riorganizzato per audience tecnica/sviluppatori (era misto end-user + dev): ridotto da 663 → ~180 righe, banner che dirige gli utenti finali alla getting-started su gh-pages, sei bullet "Cosa è implementato" con path file + RF-codes + beta tag onesti, sezione develop-locally con chiarimento Ollama vs llama.cpp, link reciproci IT·EN nella tabella documentazione
- Nuovi `docs/architecture.{md,it.md}` con diagramma a layer e dettagli Flow 1 vs Flow 2 prima inseriti nel README
- Nuovi `docs/design_decisions.{md,it.md}` con il razionale Decimal/SHA-256/invert_sign/RF-03/RF-04 prima nel README, con flag espliciti `(beta)` su RF-03 e RF-04
- Nuova sezione "Cosa ottieni" su tutti i 9 `getting-started.*.html` con sei bullet user-value (tradotti in tutte le lingue) — framing complementare a "Cosa è implementato" del README
- Sezione README "Cosa lascia la macchina" resa esplicita e onesta: esempio concreto before/after di redazione PII e ammissione che **importi** e **date** delle transazioni viaggiano comunque verso i backend LLM remoti nell'implementazione attuale (item di backlog AI-55 copre le modalità di redazione). Bullet privacy su tutti i 9 getting-started gh-pages riscritto con lo stesso tono onesto
- Log persistente del boot launcher in `~/Library/Logs/spendifai-launcher.log` (macOS) / `~/.spendifai/spendifai-launcher.log` (Linux/Windows). Cattura stdout/stderr del launcher anche quando `console=False` in `desktop.spec` li avrebbe deviati a `/dev/null`. Troncato ad ogni launch
- Download modello in parallelo con banner ETA live. `desktop/launcher.py` separa il bootstrap sincrono dell'ambiente dal download multi-GB del modello. Il download gira in un daemon thread e aggiorna `~/.spendifai/model_download.status` con `pct`, `elapsed_s`, `eta_remaining_s`. Nuovo `ui/widgets/model_download_banner.py` lo legge via `st.fragment(run_every=2)` e mostra un banner sticky senza re-renderizzare l'intera pagina. `core/model_manager._download_from_hf` aggancia il tqdm di Hugging Face al nostro callback per progress reale chunk-per-chunk (prima il callback partiva solo al 100%)
- Pagina di primo avvio immersiva. Quando `onboarding_done == false`, `app.py` inietta CSS per nascondere sidebar / header / toolbar e renderizza solo il banner download + wizard onboarding. Appena il wizard è confermato, il layout torna all'app normale con sidebar
- Build `.deb` cross-arch. `release.yml` esegue `build-deb.sh` per `amd64` e `arm64` via matrix; stesso source Python, cambia solo `Architecture:` in `DEBIAN/control` (il postinst compila le deps native sulla macchina target via `uv sync`). Sblocca l'install su VM Apple Silicon (#AI-57)
- Documentato il flusso dev end-to-end in `docs/desktop_build_and_test.{md,it.md}`: build DMG locale, MSIX CI via `workflow_dispatch`, install MSIX su VM clean tramite `packaging/windows/dev-install.ps1` (auto self-signed cert, firma, installa), tutti i path di log, cleanup totale
- `packaging/windows/dev-install.ps1`: script all-in-one per tester — genera un cert self-signed con l'esatto Publisher CN del manifest, lo importa come trusted, trova SignTool, firma l'MSIX e `Add-AppxPackage` per installare. Idempotente (cert reuse), auto-elevate se non Admin
- Script di cleanup totale per tutti e tre i sistemi: `packaging/macos/cleanup.sh`, `packaging/linux/cleanup.sh`, `packaging/windows/cleanup.ps1`. Ognuno killa processi attivi, disinstalla il pacchetto di sistema (AppX / apt / dnf / rm dell'.app), cancella `~/.spendifai`, la cache modelli Hugging Face e i log launcher. Opzioni `--keep-models` per preservare la cache GGUF e (solo Windows) `-RemoveDevCert` per rimuovere anche il cert self-signed

### Fixed
- Il registry dei modelli non punta più il tier 16 GB al repo gated `google/gemma-3-12b-it-GGUF` (i download anonimi ricevevano HTTP 401 → tutta la feature AI disabilitata su Apple Silicon / workstation NVIDIA). Ora usa il mirror pubblico `bartowski/google_gemma-3-12b-it-GGUF` con gli stessi pesi. `ensure_model_available` ha anche un fallback automatico: ad ogni fallimento di download prova il tier inferiore invece di restituire `None`. Worst case un host da 64 GB scende gemma-3-12b → qwen2.5-7b → qwen2.5-3b → qwen2.5-1.5b. Chiude #AI-59
- Onboarding wizard ora aspetta il download del modello LLM sul bottone finale "Avvia". Il launcher desktop fa partire il download all'apertura dell'app; l'utente può completare tutti gli step del wizard in parallelo mentre il modello si scarica in background. Sullo step di riepilogo il bottone resta disabilitato con indicatore live "⏳ Attendi modello AI — 78% · ~3 min" finché il download non raggiunge il 100%. Auto-refresh ogni 2 s senza perdere lo stato del wizard (#AI-58 follow-up)
- Onboarding wizard ora include nei suoi settings di completamento i default LLM invisibili (`llm_backend=local_llama_cpp`, `cat_llm_backend=local_llama_cpp`, `llama_cpp_n_gpu_layers=0`, `llama_cpp_n_ctx=4096`, `llama_cpp_model_path` da `$LLAMA_CPP_MODEL_PATH` settato dal launcher). Senza questo seed un utente che completava il wizard si trovava comunque l'errore `llm_backend not configured` sulla pagina Import
- Auto-skip migration ora richiede TUTTE e 4 le prerequisiti del wizard onboarding prima di marcare silenziosamente un utente come "già onboardato": `ui_language` configurata, `owner_names` non vuoto, `llm_backend` configurato, e ≥1 riga in `account`. Prima bastava una sola riga di tassonomia popolata — ma il default taxonomy seed parte su ogni fresh install, quindi ogni nuovo utente veniva saltato direttamente al di là del wizard (#AI-58)
- Auto-invalidazione degli schemi: gli schemi in cache (Flow 1) con parse rate < 10% vengono eliminati automaticamente e ritentati con Flow 2 (riclassificazione LLM)
- Pulizia schemi orfani: la migration di avvio rimuove le righe `document_schema` senza `header_sha256`, prevenendo voci stale irraggiungibili
- Header SHA256 sempre popolato sullo schema prima del persist, evitando la creazione di schemi orfani
- Crash del sanitizer su colonne pandas 2.x `string`-dtype con NaN: `astype(str).tolist()` lasciava trapelare float NaN nel sanitizer regex, sollevando `TypeError: expected string or bytes-like object, got 'float'` su import CSV bancari reali. Fix in `core/classifier.py` (`fillna("").astype(str)`) più guard difensivo in `core/sanitizer.py` (`redact_pii` coerce gli input non-string a `""`). 5 test di regressione in `tests/test_sanitizer.py::TestNonStringInputs` (#108)
- Catena di silent crash del bundle desktop: cinque bug distinti che facevano uscire il `.app` PyInstaller senza output ad ogni launch — `console=False` che ingoiava stdout, il path di `splash.html` che collassava a `_MEIPASS` invece di `_MEIPASS/desktop/`, loop di re-exec infinito quando launcher.py invocava `subprocess.Popen([sys.executable, "-m", "streamlit", …])` (l'eseguibile è il bundle stesso, che rientrava nel launcher), Streamlit che rifiutava `--server.port` perché `global.developmentMode` di default era `true` nel bundle, e `support/logging.py` che hard-codava `os.makedirs("logs")` read-only nel bundle. Tutto sistemato in `fix(desktop): make the PyInstaller bundle actually start` (#AI-49 follow-up)
- Albero processi non killato alla chiusura finestra. `_cleanup` ora usa `os.killpg` sui process group avviati con `start_new_session=True` così Streamlit + uvicorn + grandchildren muoiono insieme. Prima lo Streamlit residuo teneva aperta la porta e macOS Launch Services trattava il doppio-click successivo come "riapertura dell'istanza in esecuzione"
- `packaging/linux/build-rpm.sh` entry icona in `%files` resa condizionale su `ICON_PRESENT=1` così rpmbuild riesce anche quando non c'è `spendifai_256.png` sul build host (chiude AI-56)
