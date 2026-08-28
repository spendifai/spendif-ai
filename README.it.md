# Spendif.ai

[![CI](https://github.com/spendifai/spendif-ai/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/spendifai/spendif-ai/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/spendifai/spendif-ai/graph/badge.svg)](https://codecov.io/gh/spendifai/spendif-ai)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: PolyForm NC](https://img.shields.io/badge/license-PolyForm%20Noncommercial-orange)](LICENSE)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Streamlit](https://img.shields.io/badge/UI-Streamlit-ff4b4b?logo=streamlit&logoColor=white)](https://streamlit.io)
[![Issues](https://img.shields.io/github/issues/spendifai/spendif-ai)](https://github.com/spendifai/spendif-ai/issues)
[![Last commit](https://img.shields.io/github/last-commit/spendifai/spendif-ai)](https://github.com/spendifai/spendif-ai/commits/main)
[![Supporta su Patreon](https://img.shields.io/badge/Patreon-offrimi%20un%20caffè%20☕-F96854?logo=patreon&logoColor=white)](https://patreon.com/drake69)

> 🇬🇧 [Read in English](README.md)

Registro finanziario personale unificato con pipeline ibrida deterministica + LLM. Aggrega esportazioni bancarie eterogenee (conti correnti, carte di credito/debito/prepagate, conti deposito) in un unico ledger cronologico, eliminando i doppi conteggi causati da addebiti carta ricorrenti e da giroconti interni. Offline-first; backend LLM remoti opt-in, con sanitizzazione PII obbligatoria.

---

> 👋 **Utente finale — vuoi installare Spendif.ai?**
> Vai alla [pagina Primo avvio](https://spendifai.github.io/spendif-ai/getting-started.html) per la guida illustrata all'installazione e al primo avvio. Questo README è dedicato a sviluppatori e contributor.
>
> Su macOS puoi installare e restare aggiornato via Homebrew:
> ```bash
> brew tap spendifai/spendifai
> brew trust --cask spendifai/spendifai/spendifai   # una volta per macchina, Homebrew 6+
> brew install --cask --no-quarantine spendifai
> brew update && brew upgrade --cask spendifai     # release successive
> ```

---

## Cos'è (tecnicamente)

- **Python 3.13 · Streamlit · SQLAlchemy · Pydantic · Pandas**
- **Pipeline ibrida**: normalizer deterministico + classifier LLM + categorizer a cascata
- **LLM multi-backend** con circuit breaker: llama.cpp (default per desktop), Ollama, OpenAI, Claude — interazione diretta tramite SDK, senza LangChain
- **Launcher desktop nativo**: pywebview + PyInstaller → DMG / MSIX / .deb / .rpm
- **UI Streamlit articolata in 17 pagine**, i18n IT+EN completo (760+ chiavi di traduzione)

## Cosa è implementato

- **Pipeline ibrida (deterministica + LLM)** — `core/normalizer.py` analizza qualunque esportazione bancaria tabellare; `core/classifier.py` deduce `DocumentSchema` tramite LLM; `core/categorizer.py` esegue una cascata in 4 fasi (regole utente → regex → LLM → fallback)
- **LLM multi-backend con circuit breaker** — factory in `core/llm_backends.py`: llama.cpp, Ollama, OpenAI, Claude. Fallback automatico e quarantena in caso di fallimento
- **Sanitizzazione PII (RF-10)** — redazione di IBAN / PAN / codice fiscale / nomi titolari in `core/sanitizer.py`, obbligatoria prima di qualsiasi chiamata remota (`assert_sanitized()` è una precondizione, non un'opzione best-effort)
- **Tassonomia multilingua** — 2 livelli in DB, 5 lingue (it/en/fr/de/es), configurabile dall'UI Streamlit
- **Riconciliazione carta-conto (RF-03, beta)** — algoritmo in 3 fasi in `core/normalizer.py`: abbina gli addebiti carta con le spese sottostanti per eliminare i doppi conteggi *(casi limite ancora in fase di affinamento)*
- **Rilevazione giroconti interni (RF-04, beta)** — abbinamento per importo simbolico e finestra di ±7 giorni, con permutazioni dei nomi del titolare per intercettare esportazioni del tipo «Cognome Nome» *(casi limite ancora in fase di affinamento)*

## Cosa arriva dopo

Spendif.ai è in fase di alpha test. Le funzioni che aggiungeremo le decidiamo insieme a chi usa l'app oggi: nessun lavoro speculativo, nessuna roadmap calata dall'alto.

Funzioni in valutazione in base ai riscontri degli alpha tester:

- **Registrazione del contante** — registrare manualmente le spese in contanti, senza estratto bancario
- **Andamento degli investimenti** — vedere a colpo d'occhio come si comportano gli strumenti in portafoglio
- **App mobile compagna** — registrare al volo le spese in contanti dal telefono e sincronizzarle con il desktop

Una di queste ti servirebbe? Segnalacelo su [GitHub Discussions](https://github.com/spendifai/spendif-ai/discussions). Quando arrivano abbastanza richieste sulla stessa funzione, sale in cima alla coda.

## 👩‍💻 Sviluppo locale

```bash
git clone https://github.com/spendifai/spendif-ai.git
cd spendif-ai
uv sync --extra desktop

# LLM locale (scelta dello sviluppatore — l'installer desktop gestisce
# questo automaticamente):
#   → se hai già Ollama attivo:   ollama pull gemma3:12b
#   → altrimenti: `uv sync` installa llama-cpp-python e il launcher
#     scarica automaticamente un modello GGUF al primo avvio

./start.sh                    # oppure: streamlit run app.py
```

Prerequisiti: **Python 3.13+**, **[uv](https://github.com/astral-sh/uv)** e, in alternativa, Ollama (oppure nulla: llama.cpp è già incluso). Configurazione completa → [CONTRIBUTING.md](CONTRIBUTING.md).

### Eseguire come app desktop nativa da sorgente

```bash
uv run python -m desktop.launcher
```

Apre una finestra pywebview, scarica un modello AI al primo avvio e avvia Streamlit all'interno della stessa finestra. L'esperienza è identica a quella dei bundle DMG/MSIX.

## Eseguire i test

```bash
uv run pytest -v                                  # suite completa (no mock LLM)
uv run pytest --cov=. --cov-report=term-missing   # con coverage (target ≥ 90%)
uv run pytest -k "architecture"                   # gate separazione layer
uv run pytest -k "security"                       # forbidden pattern + SQL injection
```

I test architetturali e di sicurezza sono gate CI obbligatori e devono restare verdi sul branch `main`.

## Architettura

```
ui/
 ↓
services/  ─┬──→  core/  (pipeline: normalizer, classifier, categorizer,
            │             sanitizer, llm_backends, orchestrator)
            │
            └──→  db/    (models + repository CRUD) → SQLite
```

**Layer:**

- **`ui/`** — Pagine Streamlit (onboarding, upload, review, history, analysis,
  report, budget, budget vs actual, bulk edit, chat, checklist, home, registry,
  rules, settings, taxonomy, llm_models) più `i18n/`, `widgets/`, `components/`
  e `sidebar.py`. Sola presentazione: importa **esclusivamente** da `services/`.

- **`services/`** — Facade fra UI e logica interna. I servizi
  (`transaction_service`, `import_service`, `review_service`, `llm_service`,
  `budget_service`, `category_service`, `rule_service`, `settings_service`,
  `nsi_taxonomy_service`) orchestrano la pipeline di `core/` e persistono lo
  stato via `db.repository` (pattern Repository). Riesportano i tipi di dominio
  (`DocumentSchema`, …) per evitare che l'UI tocchi i layer inferiori.

- **`core/`** — Pipeline di dominio, indipendente da Streamlit:
  normalizzazione deterministica (`normalizer`, RF-02/03/04/06),
  classificazione documenti via LLM (`classifier`, RF-01), categorizzazione
  a cascata (`categorizer`, RF-05), sanitizzazione PII obbligatoria prima
  di chiamate LLM remote (`sanitizer`, RF-10), factory dei backend LLM
  (`llm_backends`), orchestratore Flow 1 / Flow 2 (`orchestrator`, RF-01→RF-07)
  e motore di auto-learning storico (`history_engine`). Funzioni pure dove
  possibile, unit-testabili senza mock di Streamlit.

- **`db/`** — Persistenza SQLAlchemy: `models.py` definisce le tabelle ORM
  (Transaction, ImportBatch, DocumentSchemaModel, ReconciliationLink,
  InternalTransferLink, CategoryRule, UserSettings, BudgetTarget,
  LlmUsageLog, …, RF-07); `repository.py` espone CRUD idempotenti upsert-style
  (RF-06/07); `taxonomy_defaults.py` contiene i template di tassonomia per
  lingua.

**Regola verificata in CI dal coupling gate:** `ui/` non può importare da
`core/` né da `db/` — passa solo per `services/`. `tools/coupling_check.py
--strict` blocca le PR che la violano.

**Debito noto:** `db/repository.py` importa al top-level alcuni tipi da
`core/` (`DocumentSchema`, `CategoryRule`); `core/` a sua volta importa da
`db/` in alcuni punti con import inline dentro le funzioni per evitare cicli.
L'estrazione di un layer `schemas/` ignorante che spezzi il ciclo è tracciata
in backlog come **AI-153**.

Diagramma completo e Flusso 1 vs Flusso 2 → [docs/architecture.it.md](docs/architecture.it.md).

## Struttura del repository

```
sw_artifacts/
├── app.py                  # Entry point Streamlit (onboarding gate + 17 pagine)
├── core/                   # Pipeline: orchestrator, normalizer, classifier, categorizer, sanitizer, llm_backends
├── services/               # Facade layer per l'UI; async runner; settings; import
├── ui/                     # Pagine Streamlit + i18n + widgets
├── db/                     # SQLAlchemy ORM, repository pattern, schema con auto-hash migrations
├── api/                    # Endpoint REST FastAPI (opzionale)
├── desktop/                # Launcher nativo (pywebview) + splash
├── packaging/              # Build script: macos/, windows/, linux/, homebrew/, winget/
├── docker/                 # Containerizzazione
├── prompts/                # Template prompt LLM (JSON versionato)
├── reports/                # Export HTML + CSV + XLSX
├── tests/                  # Suite pytest (target coverage ≥ 90%)
├── benchmark/              # Suite benchmark LLM (multi-provider)
└── docs/                   # Documentazione utente e sviluppatore
```

Dettagli → [docs/developer_guide.md](docs/developer_guide.md).

## 📚 Documentazione

| Argomento | Lingue |
|---|---|
| Installazione e primo avvio | [EN](docs/installazione.en.md) · [IT](docs/installazione.md) |
| Guida utente (ogni pagina) | [EN](docs/guida_utente.en.md) · [IT](docs/guida_utente.md) |
| Reference guide (pipeline, tassonomia, RF-03/04) | [EN](docs/reference_guide.en.md) · [IT](docs/reference_guide.md) |
| Architettura | [EN](docs/architecture.md) · [IT](docs/architecture.it.md) |
| Design decisions | [EN](docs/design_decisions.md) · [IT](docs/design_decisions.it.md) |
| Configurazione | [EN](docs/configurazione.en.md) · [IT](docs/configurazione.md) |
| Developer guide | [EN](docs/developer_guide.en.md) · [IT](docs/developer_guide.md) |
| Guida alla categorizzazione | [EN](docs/guida_classificazione.en.md) · [IT](docs/guida_classificazione.md) |
| Schema database | [EN](docs/database.en.md) · [IT](docs/database.md) |
| Deployment | [EN](docs/deployment.en.md) · [IT](docs/deployment.md) |
| Processo di rilascio | [EN](docs/release_process.md) · [IT](docs/release_process.it.md) |
| Loop di build e test desktop | [EN](docs/desktop_build_and_test.md) · [IT](docs/desktop_build_and_test.it.md) |
| Contribuire | [EN](CONTRIBUTING.en.md) · [IT](CONTRIBUTING.md) |
| Politica di sicurezza | [EN](SECURITY.md) · [IT](SECURITY.it.md) |
| Changelog | [EN](CHANGELOG.md) · [IT](CHANGELOG.it.md) |

## Contribuire

Segnalazioni di bug, proposte di funzionalità e PR sono benvenute. Vedi [CONTRIBUTING.md](CONTRIBUTING.md) per workflow, regole di branching, framework delle priorità e gate CI.

## Licenza

**PolyForm Noncommercial 1.0.0** — vedi [LICENSE](LICENSE). Uso personale libero; l'uso commerciale richiede una licenza separata.

---

### Cosa lascia la macchina — trasparenza

Tutti i dati finanziari sono memorizzati localmente in `~/.spendifai/ledger.db`.

**Backend LLM locale (default — llama.cpp, Ollama)**: nulla lascia la macchina.

**Backend LLM remoto (opt-in — OpenAI, Claude)**: il payload contiene
descrizioni sanitizzate insieme a **importi**, **date** e **metadati
delle colonne**.

#### Esempio di redazione — categorizer (`core/categorizer.py:303`)

Riga grezza della transazione dal CSV:

```
date:        2026-03-15
description: "BONIFICO da MARIO ROSSI IT60X0542811101000000123456 CAU 12345 STIPENDIO MENSILE"
amount:      1500.00
```

Cosa l'LLM remoto riceve effettivamente:

```json
{
  "amount": "1500.00",
  "description": "BONIFICO da Carlo Brambilla <ACCOUNT_ID> <TX_CODE> STIPENDIO MENSILE"
}
```

Cosa è cambiato:
- `MARIO ROSSI` (nome titolare configurato) → `Carlo Brambilla` (nome fittizio dal pool italiano, ripristinato dopo la risposta LLM)
- `IT60X...` (IBAN) → `<ACCOUNT_ID>`
- `CAU 12345` (codice transazione bancaria) → `<TX_CODE>`
- `amount` e metadati di data: **inviati in chiaro**

Il prompt del categorizer istruisce il modello a "basare la decisione su
descrizione, importo e contesto" (`prompts/categorizer.json`). L'effettivo
impatto dell'importo sull'accuratezza, in pratica, non è stato misurato
contro una baseline senza importi: il comportamento predefinito,
conservativo, lo lascia nel payload finché la misurazione non sarà disponibile.

#### Roadmap

Le modalità di redazione di importi e date per i backend remoti
(`none` / `buckets` / `strip`) sono in roadmap — elemento di backlog **AI-55**.
