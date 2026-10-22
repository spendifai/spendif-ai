# Spendif.ai — Design decisions

> 🇮🇹 [Leggi in italiano](design_decisions.it.md)

This document collects the non-obvious choices baked into the codebase
and the reasoning behind them. Read this if you are about to refactor
something and wonder "why is it done this way?".

---

## `Decimal` — never `float`

All amounts are `decimal.Decimal`. IEEE 754 floats introduce rounding
errors that corrupt balances and reconciliation results. The normalizer
never produces `float` for monetary values; tests assert this.

## SHA-256 idempotency

Each transaction has a 24-character `id` (truncated SHA-256) computed
deterministically from `(source_file, date, amount, description)`.
Re-importing the same file does not create duplicates: the row is upserted
on its SHA-256, never on a synthetic auto-increment primary key.

## Card sign correction (`invert_sign`)

Italian card exports often store purchases as positive values. The
`DocumentSchema.invert_sign` flag, set by the LLM during Flow 2
classification, instructs the normalizer to negate all amounts so that
expenses become negative and refunds become positive — with a single
symmetric operation.

### Two-step detection algorithm

The classifier decides the value of `invert_sign` using a two-step
algorithm. **Step 0 takes priority: if it fires, Step 1 is skipped
entirely.** Step 1 is only consulted when Step 0 finds no definitive
answer.

**Step 0 — Column name synonym check (highest priority)**

The column name is inspected for membership in one of three synonym
groups:

| Group | Example names | Decision |
|---|---|---|
| **Outflow synonyms** | Uscita, Uscite, Addebito, Addebiti, Pagamento, Spesa, Dare, Importo addebitato | `invert_sign = true` (expenses stored as positive → negate) |
| **Inflow synonyms** | Entrata, Entrate, Accredito, Accrediti, Avere, Credito, Importo accreditato | `invert_sign = false` (incomes already positive → no change) |
| **Neutral names** | Importo, Amount, Valore, Totale | No decision — proceed to Step 1 |

Outflow and inflow synonym matching is case-insensitive and partial
(e.g. "Addebiti carta" matches "Addebito"). The outflow rule applies to
card doc_types only; bank account and savings files always keep
`invert_sign = false` regardless of column name.

**Step 1 — Sign distribution analysis (neutral column names only)**

When Step 0 finds a neutral column name it cannot classify by name
alone, the classifier counts positive vs. negative values in the sample
and computes `positive_ratio` and `negative_ratio`:

- Card file, majority positive (> 60 %): expenses are stored as positive
  (AMEX / typical Italian export convention) → `invert_sign = true`
- Card file, majority negative (> 60 %): expenses already carry the
  correct sign → `invert_sign = false`
- Roughly 50/50 split: descriptions are inspected (merchant names with
  positive amounts → `invert_sign = true`; "bonifico ricevuto" with
  positive amounts → `invert_sign = false`)
- Bank account / savings: always `invert_sign = false`, regardless of
  distribution

### Diagnostic fields

Every `DocumentSchema` produced by Flow 2 includes four diagnostic
fields for audit and debugging:

| Field | Type | Content |
|---|---|---|
| `positive_ratio` | `float \| null` | Fraction of amount-column values > 0 in the sample |
| `negative_ratio` | `float \| null` | Fraction of amount-column values < 0 in the sample |
| `semantic_evidence` | `list[str]` | 2–4 short sentences from the LLM explaining the decision |
| `normalization_case_id` | `str \| null` | C1 = bank signed_single · C2 = card inverted · C3 = card already negative · C4 = Dare/Avere columns · C5 = ambiguous · C6 = debit\_credit\_signed (separate debit/credit columns, values already carry sign) |

These fields are persisted in the `document_schema` DB table and are
visible in the Flow 2 schema review step in the UI.

## Subcategory as primary key

The categorizer treats subcategory as authoritative.
`TaxonomyConfig.find_category_for_subcategory()` resolves the parent
category from any valid subcategory name. This means LLMs and rules can
specify the most granular level and the hierarchy is always consistent
in the DB.

## Taxonomy in DB

The 2-level taxonomy (categories + subcategories) lives in two DB
tables (`taxonomy_category`, `taxonomy_subcategory`). On first run the
onboarding wizard copies the chosen language template from the
immutable `taxonomy_default` table into the user's editable taxonomy.
No YAML files involved. Changes are managed entirely from the UI — no
file edits or restarts required.

## PII sanitization as a precondition

`assert_sanitized()` is called inside `call_with_fallback()` before any
request to a remote backend. If the text contains detectable
IBAN/PAN/fiscal-code patterns, the call is rejected — not silently
degraded.

## Circuit breaker and quarantine

`call_with_fallback(primary, ...)` tries the primary backend, then
local Ollama as fallback. If both fail, the transaction receives
`to_review=True` and is queued for manual review without blocking the
rest of the batch.

## No LangChain

LLM backends use the `openai` SDK, `anthropic` SDK, and `requests` (for
Ollama) directly. No LLM orchestration framework dependency — smaller
attack surface, independent SDK updates.

## RF-03: 3-phase reconciliation algorithm

Card–account reconciliation uses:

1. **Temporal window** ± 45 days between the card settlement and
   candidate underlying expenses.
2. **Contiguous sliding window** with gap ≤ 5 days between adjacent
   transactions, O(n²) in the candidate window size.
3. **Boundary subset sum** with k = 10 transactions, ~10⁶ operations
   maximum.

Only the settlement debit is left out of the totals, by its type (card
settlement, like internal transfers); the card's detail lines are the real
spending and count. The reconciled flag marks both sides of a match and hides
nothing.

**Status: beta.** Edge cases (multi-currency, partial settlements, mid-month
issuer changes) are still being refined.

## RF-04: internal transfer detection

Symbolic-amount matching with time window and owner-name permutations:

- Window: pair must occur within ± 7 days.
- Sign: amounts must be opposite (one debit, one credit) and equal in
  absolute value.
- Owner match: the counterpart name in one transaction matches any
  permutation of the configured owner names. Permutations cover Italian
  bank exports that use "Surname Firstname" instead of "Firstname Surname".

**Status: beta.** Edge cases (intermediary holding accounts, currency
conversion fees) are still being refined.

## Calendar versioning

From 2026.10.0 versions are calendar versions `YYYY.M.N`: year, month without
a leading zero, release of the month from 0. Earlier releases (up to 0.3.5)
used Semantic Versioning.

- A personal finance app released as a desktop package has no public API
  whose compatibility a major number would promise; the date says how old
  the installed copy is, which is what a user needs to know.
- Three plain numbers stay valid for every package format: MSIX takes four
  numeric parts up to 65535, RPM forbids dashes, Homebrew and winget compare
  numbers. No suffixes (no `-beta`, no `-rc1`) and no leading zero, which is
  not valid semver and would break the Docker image tags that read the version
  as semver.
- The update check compares the numbers as a tuple, so 2026.10.0 is newer
  than 0.3.5 and existing installs are offered the upgrade.
- The launch stage (alpha, beta) is not part of the version: it is said in the
  release notes.
