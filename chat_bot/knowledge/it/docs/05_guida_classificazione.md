# Guida alla Classificazione delle Transazioni

> Come Spendif.ai trasforma i tuoi movimenti bancari in dati categorizzati con il minimo intervento manuale.

---

## 1. Import

Carica il file movimenti (CSV, XLSX, XLS) trascinandolo nell'area di upload.

- Spendif.ai riconosce lo schema del file automaticamente e calcola un **confidence score** (0-100%).
- Se la confidenza e >= 80%, l'importazione procede senza intervento.
- Le transazioni vengono categorizzate dall'AI (LLM) e dalle regole deterministiche gia presenti.

---

## 2. Review

Le transazioni su cui l'AI non era sicura vengono marcate con il flag di warning.

- Le transazioni incerte sono marcate con **warning** (da rivedere)
- Per ciascuna puoi correggere **categoria**, **sottocategoria** e **contesto** dai menu a tendina
- Per dire a Spendif.ai "questa categoria e giusta" basta lasciarla o sceglierne un'altra: la scelta resta tua. La spunta **Validato** dice un'altra cosa: che il movimento e vero (vedi "Controllato e categorizzato" in fondo)
- Puoi anche creare una regola direttamente dalla Review, come nel Ledger

---

## 3. Ledger — Il centro di comando

Vista completa di tutte le transazioni importate, con filtri per data, conto, categoria e tipo.

**Modifica diretta:** cambia categoria, sottocategoria e contesto direttamente nella griglia. Ogni modifica aggiorna il campo `category_source` a "manual".

**Controllato da te:** spunta la casella *Controllato da te* quando hai verificato che il movimento e vero, cioe che i soldi sono usciti o entrati davvero. Non riguarda la categoria e non passa ai movimenti dei prossimi import: si fa un movimento alla volta, solo qui. Il salvataggio e immediato, il flag di warning viene rimosso automaticamente.

**Crea regola da una transazione:**

1. Seleziona **1 riga** nella colonna di selezione (📏) della griglia del ledger. Se ne selezioni piu di una, l'app mostra un errore rosso: *"Si puo creare ed applicare una regola alla volta"*.
2. Compare il form **"Crea regola"**, pre-compilato con:
   - **Pattern**: estratto dalla controparte della transazione (es. `ESSELUNGA`)
   - **Tipo**: *Contiene il testo* (default). Le opzioni disponibili sono: *Contiene il testo*, *Uguale esatto*, *Espressione avanzata*
   - **Categoria/Sottocategoria/Contesto**: copiati dalla transazione selezionata
3. L'**anteprima** mostra quante transazioni verranno matchate dalla regola
4. Se la regola esiste gia, viene mostrato un avviso giallo e il pulsante diventa **"Modifica regola e applica"**
5. Click su **"Crea regola e applica"** (o **"Modifica regola e applica"**):
   - La regola viene salvata nel database
   - Viene applicata **retroattivamente** a tutte le transazioni matching non ancora validate
   - Un toast conferma "Regola creata" o "Regola aggiornata"

---

## 4. Regole automatiche

Le regole create (dal Ledger, dalla Review o dalla pagina Regole) si applicano automaticamente ai prossimi import.

- **Priorita**: le regole piu specifiche hanno priorita piu alta. Il primo match vince.
- **Tipi di match**: *Uguale esatto*, *Contiene il testo*, *Espressione avanzata* (colonna "Tipo" nella griglia regole).
- **Pagina Regole**: gestisci, modifica, elimina le regole esistenti. Usa il pulsante "Esegui tutte le regole" per applicarle in blocco a tutto lo storico.

---

## 5. Il ciclo virtuoso

```
Import --> AI categorizza --> Utente rivede --> Crea regola --> Prossimo import: 0 interventi
```

Piu usi Spendif.ai, meno lavoro manuale. Ogni regola creata riduce il numero di transazioni da rivedere al prossimo import. Obiettivo: **zero pain**.

---

## 5b. Auto-apprendimento dallo storico

Oltre alle regole, Spendif.ai impara automaticamente dalle categorie che decidi tu. Ogni volta che scegli una categoria a mano, o che una tua regola la assegna, il sistema registra l'associazione tra la controparte e la categoria.

### Come funziona

1. **Tabella associazioni**: Spendif.ai raggruppa tutte le transazioni con una categoria decisa da te per descrizione + categoria + sottocategoria e conta le ricorrenze.
2. **Omogenità** — Per ogni controparte, calcola quanto è "stabile" nella classificazione (da 0 a 1). Se ESSELUNGA è *sempre* Alimentari → omogenità = 1.00. Se AMAZON è sparpagliata tra Tecnologia, Alimentari, Abbigliamento → omogenità bassa.
3. **Decisione automatica** — Al prossimo import, prima di chiamare l'AI, Spendif.ai consulta lo storico:
   - **Confidenza ≥ 0.90** → assegna direttamente (fonte: 📚 Storico), nessuna review necessaria
   - **Confidenza 0.50–0.89** → suggerisce la categoria (fonte: 📚 Storico), ma la mette in review
   - **Confidenza < 0.50** → chiama l'AI come al solito

### In pratica

```
Metti a mano 12 transazioni ESSELUNGA in Alimentari
  → Prossimo import con ESSELUNGA: classificata automaticamente, zero interventi

Metti a mano 8 ROSSOPOMODORO in Ristorazione + 2 in Vacanze
  → Prossimo import: suggerisce Ristorazione, ma te la mette in review

AMAZON con 5 categorie diverse
  → Prossimo import: chiama l'AI (troppo eterogenea per lo storico)
```

> **Importante:** contano per lo storico solo le transazioni con una categoria **decisa da te** (a mano o da una tua regola). Quelle categorizzate dall'AI hanno classificazioni incerte e non vengono usate per l'apprendimento automatico. La spunta *Controllato da te* non c'entra: dice che la spesa e vera, non come l'hai fatta.

---

## Colonne indicatore

| Indicatore | Significato |
|------------|------------|
| Warning | Da rivedere (classificazione incerta) |
| Controllato da te | Movimento verificato come vero dall'utente |
| Giroconto | Giroconto interno (bonifico tra propri conti) |

---

## Controllato e categorizzato

Spendif.ai gestisce due domande **distinte** per ogni transazione, e una non risponde mai all'altra:

- **Categorizzato da te: come ho speso questi soldi?** Vale per una controparte: una tua regola decide la categoria dei movimenti gia caricati e di quelli dei prossimi import. E cio che chiede tutto il flusso di lavoro: una controparte con una tua regola non viene mai richiesta.
- **Controllato da te: questi soldi li ho spesi davvero?** Vale per un movimento solo e non si eredita mai: ogni addebito nuovo e un fatto nuovo. Si fa solo nel Registro, un movimento alla volta.

Cambiare la categoria di un movimento non lo segna come controllato, e controllarlo non ne decide la categoria.

| Concetto | Campo | Significato |
|----------|-------|-------------|
| **Controllato da te** | `human_validated` | "Ho visto questa spesa e confermo che e vera (non anomala)." E l'approvazione della **spesa**, non della categoria. |
| **Fonte classificazione** | `category_source` | "Chi ha assegnato l'ultima categoria." Valori: AI, Regola, Manuale, Storico. |
| **Modello classificazione** | `category_model` | "Quale modello LLM specifico ha categorizzato." Es. `gemma-2-2b-it-Q4_K_M`. Vuoto se fonte = Regola o Manuale. |

**Regola chiave:** i due campi sono indipendenti. Quando una regola, l'AI o tu stesso riclassificate una transazione, la fonte cambia ma il controllo **non viene toccato**. Solo un click esplicito sulla casella *Controllato da te* lo mette o lo toglie.

**In pratica:**
- Spunti *Controllato da te* su una transazione classificata dall'AI come "Alimentari" -> `controllato=True`, `fonte=AI`
- Poi crei una regola che la riclassifica come "Supermercato" -> `controllato=True` (invariato), `fonte=Regola`
- Il controllo resta perche tu avevi gia confermato che la spesa era vera; la categoria ora e decisa da te perche viene da una tua regola

---

## Badge fonte classificazione

| Badge | Significato |
|-------|------------|
| AI | Categorizzata dall'intelligenza artificiale |
| Regola | Categorizzata da regola deterministica |
| Manuale | Modificata manualmente dall'utente |
| Storico | Categorizzata dallo storico delle tue scelte (auto-apprendimento) |

---

Per dettagli operativi su ogni pagina, consulta la [Guida Utente](guida_utente.md).
