# Spendif.ai — Design decisions

> 🇬🇧 [Read in English](design_decisions.md)

Questo documento raccoglie le scelte non-ovvie integrate nel codebase
e il ragionamento dietro di esse. Leggi qui se stai per refactorare
qualcosa e ti chiedi "perché è fatto così?".

---

## `Decimal` — mai `float`

Tutti gli importi sono `decimal.Decimal`. I float IEEE 754 introducono
errori di arrotondamento che corrompono saldi e risultati di
riconciliazione. Il normalizer non produce mai `float` per valori
monetari; i test asseriscono questo.

## Idempotenza SHA-256

Ogni transazione ha un `id` di 24 caratteri (SHA-256 troncato)
calcolato deterministicamente da `(source_file, date, amount, description)`.
Re-importare lo stesso file non crea duplicati: la riga viene upsertata
sul suo SHA-256, mai su una chiave primaria autoincrement sintetica.

## Correzione segno carta (`invert_sign`)

Gli export carta italiani spesso memorizzano gli acquisti come valori
positivi. Il flag `DocumentSchema.invert_sign`, impostato dall'LLM
durante la classificazione Flow 2, istruisce il normalizer a negare
tutti gli importi così che le spese diventino negative e i rimborsi
positivi — con una singola operazione simmetrica.

### Algoritmo di rilevazione a due step

Il classifier decide il valore di `invert_sign` con un algoritmo a due
step. **Step 0 ha priorità: se scatta, Step 1 è saltato del tutto.**
Step 1 è consultato solo quando Step 0 non trova una risposta definitiva.

**Step 0 — Synonym check del nome colonna (massima priorità)**

Il nome colonna viene ispezionato per appartenenza a uno di tre gruppi
di sinonimi:

| Gruppo | Nomi di esempio | Decisione |
|---|---|---|
| **Sinonimi outflow** | Uscita, Uscite, Addebito, Addebiti, Pagamento, Spesa, Dare, Importo addebitato | `invert_sign = true` (spese come positive → negate) |
| **Sinonimi inflow** | Entrata, Entrate, Accredito, Accrediti, Avere, Credito, Importo accreditato | `invert_sign = false` (entrate già positive → no cambio) |
| **Nomi neutri** | Importo, Amount, Valore, Totale | Nessuna decisione — procedi a Step 1 |

Il matching outflow/inflow è case-insensitive e parziale (es.
"Addebiti carta" matcha "Addebito"). La regola outflow vale solo per
doc_type carta; conti bancari e di risparmio tengono sempre
`invert_sign = false` indipendentemente dal nome colonna.

**Step 1 — Analisi distribuzione segni (solo per nomi colonna neutri)**

Quando Step 0 trova un nome neutro che non può classificare per nome,
il classifier conta valori positivi vs negativi nel sample e calcola
`positive_ratio` e `negative_ratio`:

- File carta, maggioranza positivi (> 60 %): le spese sono memorizzate
  come positive (AMEX / convenzione tipica export italiano) →
  `invert_sign = true`
- File carta, maggioranza negativi (> 60 %): le spese hanno già il segno
  corretto → `invert_sign = false`
- Split circa 50/50: si ispezionano le descrizioni (nomi commerciali
  con importi positivi → `invert_sign = true`; "bonifico ricevuto" con
  importi positivi → `invert_sign = false`)
- Conto / risparmio: sempre `invert_sign = false`, indipendentemente
  dalla distribuzione

### Campi diagnostici

Ogni `DocumentSchema` prodotto da Flow 2 include quattro campi
diagnostici per audit e debug:

| Campo | Tipo | Contenuto |
|---|---|---|
| `positive_ratio` | `float \| null` | Frazione di valori della colonna amount > 0 nel sample |
| `negative_ratio` | `float \| null` | Frazione di valori della colonna amount < 0 nel sample |
| `semantic_evidence` | `list[str]` | 2–4 frasi brevi dall'LLM che spiegano la decisione |
| `normalization_case_id` | `str \| null` | C1 = bank signed_single · C2 = card inverted · C3 = card already negative · C4 = colonne Dare/Avere · C5 = ambigua · C6 = debit\_credit\_signed (debit/credit separati, valori già con segno) |

Questi campi sono persistiti nella tabella DB `document_schema` e sono
visibili nello step di review schema Flow 2 nell'UI.

## Subcategory come chiave primaria

Il categorizer tratta la subcategory come autoritativa.
`TaxonomyConfig.find_category_for_subcategory()` risolve la category
genitore da qualunque nome di subcategory valido. Questo significa che
LLM e regole possono specificare il livello più granulare e la
gerarchia è sempre coerente nel DB.

## Tassonomia in DB

La tassonomia a 2 livelli (categorie + subcategorie) vive in due tabelle
DB (`taxonomy_category`, `taxonomy_subcategory`). Al primo avvio il
wizard di onboarding copia il template della lingua scelta dalla
tabella immutabile `taxonomy_default` nella tassonomia editabile
dell'utente. Niente file YAML coinvolti. I cambiamenti sono gestiti
interamente dall'UI — niente edit di file o restart.

## PII sanitization come precondizione

`assert_sanitized()` viene chiamata dentro `call_with_fallback()` prima
di qualunque richiesta a un backend remoto. Se il testo contiene
pattern IBAN/PAN/codice-fiscale rilevabili, la call viene rifiutata —
non degradata silenziosamente.

## Circuit breaker e quarantena

`call_with_fallback(primary, ...)` prova il backend primario, poi
Ollama locale come fallback. Se entrambi falliscono, la transazione
riceve `to_review=True` e viene messa in coda per review manuale
senza bloccare il resto del batch.

## Niente LangChain

I backend LLM usano direttamente l'SDK `openai`, l'SDK `anthropic` e
`requests` (per Ollama). Niente dipendenza da framework di orchestrazione
LLM — superficie d'attacco minore, aggiornamenti SDK indipendenti.

## RF-03: algoritmo di riconciliazione a 3 fasi

La riconciliazione carta-conto usa:

1. **Finestra temporale** ± 45 giorni tra l'addebito carta e le
   spese sottostanti candidate.
2. **Sliding window contigua** con gap ≤ 5 giorni tra transazioni
   adiacenti, O(n²) sulla dimensione della finestra di candidate.
3. **Boundary subset sum** con k = 10 transazioni, ~10⁶ operazioni
   massimo.

Dai totali resta fuori solo l'addebito del saldo, per il suo tipo (saldo
carta, come i giroconti); le righe di dettaglio della carta sono le spese
vere e contano. Il segno di riconciliazione marca i due lati di un abbinamento
e non nasconde nulla.

**Stato: beta.** Edge cases (multi-currency, addebiti parziali, cambio
emittente carta a metà mese) sono ancora in raffinamento.

## RF-04: rilevazione giroconti interni

Matching importo simbolico con finestra temporale e permutazioni nomi
titolare:

- Finestra: la coppia deve avvenire entro ± 7 giorni.
- Segno: gli importi devono essere opposti (uno debito, uno credito)
  ed uguali in valore assoluto.
- Owner match: il nome controparte in una transazione matcha una
  qualunque permutazione dei nomi titolare configurati. Le
  permutazioni coprono export italiani che usano "Cognome Nome"
  invece di "Nome Cognome".

**Stato: beta.** Edge cases (conti di passaggio intermedi, commissioni
di conversione valuta) sono ancora in raffinamento.

## Versioni di calendario

Da 2026.10.0 le versioni sono di calendario, `AAAA.M.N`: anno, mese senza zero
iniziale, release del mese da 0. Le release precedenti (fino a 0.3.5)
seguivano Semantic Versioning.

- Un'app di finanza personale distribuita come pacchetto desktop non ha
  un'API pubblica di cui un numero major prometta la compatibilita'; la data
  dice quanto e' vecchia la copia installata, che e' cio' che serve sapere.
- Tre numeri puri restano validi per ogni formato di pacchetto: MSIX vuole
  quattro parti numeriche fino a 65535, RPM vieta i trattini, Homebrew e
  winget confrontano numeri. Niente suffissi (niente `-beta`, niente `-rc1`) e
  niente zero iniziale, che non e' semver valido e romperebbe i tag
  dell'immagine Docker che leggono la versione come semver.
- Il controllo degli aggiornamenti confronta i numeri come tupla, quindi
  2026.10.0 e' piu' recente di 0.3.5 e alle installazioni esistenti viene
  proposto l'aggiornamento.
- La fase di lancio (alpha, beta) non fa parte della versione: si dice nelle
  note di rilascio.
