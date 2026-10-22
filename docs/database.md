# Spendif.ai — Gestione del database

> Il database di Spendif.ai è un singolo file SQLite (`ledger.db`).
> Questa guida copre tutto quello che riguarda i dati: dove si trovano, come fare backup, come ripristinarli, come spostarli su un altro computer.

---

## Indice

1. [Cosa contiene il database](#1--cosa-contiene-il-database)
2. [Dove si trova il database](#2--dove-si-trova-il-database)
3. [Backup](#3--backup)
4. [Ripristino](#4--ripristino)
5. [Primo avvio con un database esistente](#5--primo-avvio-con-un-database-esistente)
6. [Spostare il database su un altro computer](#6--spostare-il-database-su-un-altro-computer)
7. [Ispezione diretta (sqlite3)](#7--ispezione-diretta-sqlite3)
8. [Corruzione del database](#8--corruzione-del-database)

---

## 1 — Cosa contiene il database

Il file `ledger.db` contiene **tutto** — non esistono altri file di dati da considerare nel backup:

| Tabella | Contenuto |
|---------|-----------|
| `transaction` | Tutte le transazioni importate |
| `category_rule` | Regole deterministiche di categorizzazione |
| `description_rule` | Regole di pulizia delle descrizioni |
| `document_schema` | Schemi dei file CSV/XLSX (colonne, formato, ecc.). Gli schema senza `header_sha256` vengono eliminati automaticamente all'avvio (migrazione `_migrate_purge_orphan_schemas`). A runtime, schema con parse rate < 10% vengono auto-invalidati e rimossi. |
| `user_settings` | Impostazioni utente (LLM, locale, formato date, contesti, flag onboarding…) |
| `account` | Conti bancari definiti dall'utente (nome, banca, `account_type`). `closed_on` (data ISO `YYYY-MM-DD`, vuota se il conto è attivo) segna il conto chiuso: la Check List smette di chiederne i movimenti (migrazione `_migrate_add_account_closed_on`) |
| `taxonomy_category` / `taxonomy_subcategory` | Tassonomia personalizzata (modificabile) |
| `taxonomy_default` | Template tassonomia built-in (5 lingue — sola lettura, non modificare) |
| `reconciliation_link` | Link carta–conto (riconciliazione RF-03) |
| `internal_transfer_link` | Giroconti interni (RF-04): coppia uscita/entrata con `confidence`. `user_decision` (`"yes"`, `"no"`, NULL se non ancora risposto) registra la risposta dell'utente sulla coppia; una coppia a `"no"` non viene più proposta (migrazione `_migrate_add_transfer_link_decision`) |
| `held_movement` | Movimenti messi da parte all'import perché la data letta è dopo il giorno dell'import: il movimento intero in `payload` (JSON dei campi di `upsert_transaction`), più data letta, conto, importo, descrizione, file, `reason` (`date_future`) e `batch_id`. Con la data corretta entra nel registro così com'era; altrimenti si scarta. Non conta da nessuna parte |
| `transfer_destination` | Destinazione dei giroconti senza controparte, una riga per beneficiario: `key` (IBAN nella descrizione, altrimenti la descrizione pulita), `answer` (`unloaded`, `untracked`, `other_person`), `created_at`. Con `other_person` i movimenti diventano spesa o entrata, anche negli import successivi |
| `import_batch` / `import_job` | Storico delle importazioni. `import_job.n_rows` conta le righe lette dai file del job, duplicati inclusi: base della stima dei tempi di import (migrazione `_migrate_add_import_job_n_rows`, NULL sui job precedenti) |
| `budget_target` | Obiettivi di budget mensili in % sulle spese, per categoria |
| `llm_usage_log` | Una riga per chiamata LLM: backend, modello, chiamante, token, durata |
| `nsi_tag_mapping` | Mapping tag OSM verso categoria e sottocategoria, invalidato al cambio di tassonomia |
| `category_correction` | Una riga per ogni correzione di categoria fatta dall'utente, con l'assegnazione originale |
| `update_event` | Una riga per ogni versione dell'app eseguita su questa installazione |

> Le tabelle sono 20, definite in `db/models.py`. Le colonne aggiunte dopo la prima versione arrivano con le funzioni `_migrate_*` chiamate da `create_tables()` all'avvio.

> I template tassonomia (`taxonomy_default`) sono incorporati nel codice (`db/taxonomy_defaults.py`) e vengono ricreati automaticamente da zero ad ogni migrazione. Non è necessario includerli in un backup separato.

---

## 2 — Dove si trova il database

| Modalità di installazione | Percorso |
|--------------------------|----------|
| **App desktop** (DMG / installer Windows / AppImage) | `~/.spendifai/ledger.db` — vedi tabella per sistema operativo qui sotto |
| **One-liner Docker** (`install.sh` / `install.ps1`) | Volume Docker `spendifai_data` → `/app/data/ledger.db` dentro il container |
| **Docker Compose da repo** | Volume Docker `spendifai_data` → `/app/data/ledger.db` dentro il container |
| **Nativa (Mac/Linux, uv)** | `./ledger.db` nella cartella del progetto |

### App desktop: la cartella `~/.spendifai/`

Quando installi Spendif.ai come applicazione desktop, tutti i dati vivono in un'unica cartella nella tua home utente, **con lo stesso nome su ogni sistema operativo**:

| Sistema operativo | Percorso completo |
|-------------------|-------------------|
| **macOS** | `/Users/<utente>/.spendifai/ledger.db` |
| **Linux** | `/home/<utente>/.spendifai/ledger.db` |
| **Windows** | `C:\Users\<utente>\.spendifai\ledger.db` |

> Su macOS/Linux la cartella `.spendifai` è **nascosta** (inizia con un punto): nel Finder premi `Cmd+Shift+.` per vederla, nel file manager Linux premi `Ctrl+H`. Su Windows la cartella è visibile normalmente sotto il tuo profilo utente.

Accanto a `ledger.db`, nella stessa cartella, l'app tiene altri file:

| File / cartella | Cosa contiene | Va nel backup? |
|-----------------|---------------|----------------|
| `ledger.db` | Il database SQLite — tutti i tuoi dati | ✅ **Sempre** |
| `.env` | Configurazione utente e **chiavi API** (OpenAI/Anthropic) in chiaro | ✅ Sì (⚠️ file sensibile) |
| `system_settings.yaml` | Override delle impostazioni di sistema | ✅ Sì (se presente) |
| `models/` | Modelli LLM `.gguf` scaricati (vari GB) | ⛔ Opzionale — si riscaricano da soli |
| `.schema_hash` | Cache interna dello schema DB | ⛔ No — vedi sezione 6 |
| `launcher.lock` | Lock di istanza singola | ⛔ No — mai copiarlo |
| `logs/` | Log applicativi | ⛔ No |

> ⚠️ **Il file `.env` contiene le tue chiavi API in chiaro** (nessuna cifratura): trattalo come una password. Se copi la cartella su una chiavetta o su un cloud, quelle chiavi viaggiano con lei.

### Perché il volume Docker non è una cartella normale?

Il volume `spendifai_data` è gestito da Docker e non è direttamente accessibile dal filesystem del tuo computer come una cartella normale. Per leggere o scrivere nel volume bisogna usare un container temporaneo come "ponte" — i comandi nelle sezioni seguenti fanno esattamente questo.

---

## 3 — Backup

### 3.1 — Backup (installazione nativa)

```bash
# Crea la cartella di backup (una volta sola)
mkdir -p ~/spendifai-backup

# Copia il DB con un nome che include la data
cp ledger.db ~/spendifai-backup/ledger_$(date +%Y%m%d_%H%M%S).db
```

### 3.2 — Backup (Docker — container in esecuzione)

Metodo diretto con `docker cp`, non richiede container aggiuntivi:

```bash
mkdir -p ~/spendifai-backup

docker cp spendifai_app:/app/data/ledger.db \
  ~/spendifai-backup/ledger_$(date +%Y%m%d_%H%M%S).db
```

> `spendifai_app` è il nome del container (definito in `docker-compose.yml`).
> Il container **deve essere in esecuzione** per usare `docker cp`.

### 3.3 — Backup (Docker — container fermo)

Se il container è fermo usa un container temporaneo Alpine (più leggero di Python):

```bash
mkdir -p ~/spendifai-backup

docker run --rm \
  -v spendifai_data:/data \
  -v ~/spendifai-backup:/backup \
  alpine cp /data/ledger.db /backup/ledger_$(date +%Y%m%d_%H%M%S).db
```

> **Windows (PowerShell):** sostituisci `~/spendifai-backup` con `$env:USERPROFILE\spendifai-backup`
> e `$(date +%Y%m%d_%H%M%S)` con la data a mano, es. `20260317_120000`.

### 3.4 — Backup automatico (crontab, Linux/Mac)

```cron
# Backup ogni giorno alle 03:00
0 3 * * * docker cp spendifai_app:/app/data/ledger.db ~/spendifai-backup/ledger_$(date +\%Y\%m\%d).db

# Cancella i backup più vecchi di 30 giorni
0 4 * * * find ~/spendifai-backup -name "ledger_*.db" -mtime +30 -delete
```

Per installazione nativa:

```cron
0 3 * * * cp /percorso/progetto/ledger.db ~/spendifai-backup/ledger_$(date +\%Y\%m\%d).db
0 4 * * * find ~/spendifai-backup -name "ledger_*.db" -mtime +30 -delete
```

---

## 4 — Ripristino

### 4.1 — Ripristino (installazione nativa)

```bash
# 1. Ferma l'app
pkill -f "streamlit run app.py"

# 2. Salva il DB attuale (per sicurezza)
cp ledger.db ledger_before_restore_$(date +%Y%m%d_%H%M%S).db

# 3. Ripristina il backup scelto
cp ~/spendifai-backup/ledger_20260317_030000.db ledger.db

# 4. Riavvia
uv run streamlit run app.py
```

### 4.2 — Ripristino (Docker)

```bash
# 1. Ferma il container
docker compose -C ~/spendifai down

# 2. Copia il backup nel volume
docker run --rm \
  -v spendifai_data:/data \
  -v ~/spendifai-backup:/backup:ro \
  alpine cp /backup/ledger_20260317_030000.db /data/ledger.db

# 3. Riavvia
docker compose -C ~/spendifai up -d
```

> Se hai installato da repository invece che con il one-liner, sostituisci
> `docker compose -C ~/spendifai` con `docker compose` dalla cartella del progetto.

### 4.3 — Ripristino parziale (solo alcune tabelle)

Utile se vuoi recuperare solo le regole di categorizzazione da un backup senza sovrascrivere le transazioni. Richiede `sqlite3` installato sull'host:

```bash
sqlite3 ledger.db "
ATTACH DATABASE '/percorso/backup/ledger_20260317.db' AS bkp;
DELETE FROM category_rule;
INSERT INTO category_rule SELECT * FROM bkp.category_rule;
DETACH DATABASE bkp;
"
```

Stessa logica per altre tabelle: `description_rule`, `user_settings`, `taxonomy_category`, `taxonomy_subcategory`.

---

## 5 — Primo avvio con un database esistente

Se hai già un `ledger.db` (ad esempio creato con l'installazione nativa) e vuoi usarlo nel container Docker, devi copiarlo nel volume **prima** di avviare l'app.

```bash
# 1. Assicurati che il container sia fermo
docker compose -C ~/spendifai down

# 2. Copia il DB nel volume
docker run --rm \
  -v spendifai_data:/data \
  -v "/percorso/assoluto/ledger.db":/source/ledger.db:ro \
  alpine cp /source/ledger.db /data/ledger.db

# 3. Verifica che il file sia arrivato
docker run --rm \
  -v spendifai_data:/data \
  alpine ls -lh /data/

# 4. Avvia l'app
docker compose -C ~/spendifai up -d
```

> **Mac:** il percorso assoluto è `/Users/tuonome/spendifai/ledger.db`
> **Linux:** `/home/tuonome/spendifai/ledger.db`

---

## 6 — Spostare il database su un altro computer

Il file SQLite è **portabile**: funziona identicamente su Mac, Linux e Windows, indipendentemente dall'architettura del processore (Intel / ARM). Puoi quindi passare da Windows a Mac, da Linux a Windows, da un Mac a un altro, ecc. senza alcuna conversione.

### 6.1 — App desktop → altro computer con app desktop (il caso più comune)

Questa è la procedura per chi usa Spendif.ai come applicazione installata (non Docker), sia che vada da **Windows/Linux a un altro PC**, sia da **un Mac a un altro Mac** o tra sistemi operativi diversi.

**Passo 1 — Chiudi Spendif.ai** sul computer di origine (esci del tutto, non lasciarlo in esecuzione). Copiare il database mentre l'app scrive può corromperlo.

**Passo 2 — Copia i dati.** Vai nella cartella `~/.spendifai/` (vedi sezione 2 per il percorso esatto del tuo sistema operativo) e copia questi tre elementi su una chiavetta USB o su un cloud:

- `ledger.db` — il database, obbligatorio
- `.env` — configurazione e chiavi API (⚠️ file sensibile, contiene le chiavi in chiaro)
- `system_settings.yaml` — solo se presente

> **Non copiare** `launcher.lock` né `.schema_hash` (vedi nota sotto). La cartella `models/` (i modelli LLM, vari GB) puoi lasciarla: sull'altro computer vengono riscaricati al primo avvio. Se però la nuova macchina sarà **offline**, oppure vuoi evitare il ri-download, copia anche `models/`.

**Passo 3 — Installa Spendif.ai** sul computer di destinazione e **avvialo almeno una volta**, poi **chiudilo**. Questo crea la cartella `~/.spendifai/` vuota.

**Passo 4 — Incolla i file** copiati al passo 2 dentro `~/.spendifai/` sul computer di destinazione, sovrascrivendo il `ledger.db` appena creato.

**Passo 5 — Riavvia Spendif.ai.** Tutte le transazioni, le regole, la tassonomia e le impostazioni sono presenti.

> **Cambio di versione dell'app** — Se il computer di destinazione ha una versione di Spendif.ai **più recente** dell'origine, va tutto bene: lo schema del database viene aggiornato automaticamente al primo avvio (assicurati solo di **non** aver copiato `.schema_hash`, così le migrazioni girano). Il percorso inverso — portare un DB di una versione **più nuova** su un'app **più vecchia** — non è supportato: installa prima la stessa versione (o più recente) sulla destinazione.

### 6.2 — Da/verso installazione Docker

Se una delle due macchine usa l'installazione Docker anziché l'app desktop:

1. **Fai il backup** del DB sul computer di origine (sezione 3 — usa `docker cp` se l'origine è Docker, oppure copia `~/.spendifai/ledger.db` se è l'app desktop)
2. **Copia il file** `ledger.db` sul nuovo computer (USB, cloud, scp, ecc.)
3. **Installa Spendif.ai** sul nuovo computer
4. **Importa il DB**: nel volume Docker → sezione 5; nell'app desktop → copialo in `~/.spendifai/ledger.db` (app chiusa)
5. Apri l'app: tutte le transazioni, regole e impostazioni sono presenti

---

## 7 — Ispezione diretta (sqlite3)

Puoi aprire il database con qualsiasi client SQLite. Esempi:

**Da terminale (sqlite3):**
```bash
# Installazione nativa — dalla cartella del progetto
sqlite3 ledger.db

# Docker — estrai prima il DB con docker cp
docker cp spendifai_app:/app/data/ledger.db /tmp/ledger_inspect.db
sqlite3 /tmp/ledger_inspect.db
```

**Query utili:**
```sql
-- Numero di transazioni per anno
SELECT strftime('%Y', date) AS anno, COUNT(*) FROM "transaction" GROUP BY anno;

-- Ultime 10 transazioni
SELECT date, description, amount, category FROM "transaction" ORDER BY date DESC LIMIT 10;

-- Regole attive
SELECT pattern, category, subcategory FROM category_rule ORDER BY priority;

-- Impostazioni utente
SELECT key, value FROM user_settings;
```

**Client grafici:** [DB Browser for SQLite](https://sqlitebrowser.org) (gratuito, Mac/Linux/Windows) — apri direttamente il file `.db`.

---

## 8 — Corruzione del database

La corruzione del file SQLite è rara ma può avvenire in caso di interruzione di corrente durante una scrittura.

### Verifica

```bash
sqlite3 ledger.db "PRAGMA integrity_check;"
# Output atteso: ok
# Se l'output contiene errori, il file è corrotto
```

### Tentativo di recupero automatico

```bash
sqlite3 ledger.db ".recover" | sqlite3 ledger_recovered.db
mv ledger.db ledger_corrupted_$(date +%Y%m%d).db
mv ledger_recovered.db ledger.db
```

Verifica di nuovo con `PRAGMA integrity_check;`. Se il recupero non riesce, ripristina dall'ultimo backup valido (sezione 4).

### Prevenzione

- L'installazione Docker ha `restart: unless-stopped` che evita shutdown improvvisi del container
- Fare backup regolari (sezione 3.4) garantisce sempre un punto di ripristino recente
