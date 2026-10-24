# Transaction Classification Guide

> How Spendif.ai turns your bank transactions into categorised data with minimal manual effort.

---

## 1. Import

Upload your transactions file (CSV, XLSX, XLS) by dragging it into the upload area.

- Spendif.ai recognises the file schema automatically and computes a **confidence score** (0-100%).
- If confidence is >= 80%, import proceeds without user intervention.
- Transactions are categorised by AI (LLM) and any existing deterministic rules.

---

## 2. Review

Transactions where the AI was not confident are flagged for review.

- Uncertain transactions are marked with a **warning** flag (needs review)
- For each one you can correct **category**, **subcategory** and **context** from dropdown menus
- To tell Spendif.ai "this category is right", keep it or pick another one: the choice stays yours. The **Validated** tick says something else: that the movement is real (see "Checked and categorized" below)
- You can also create a rule directly from the Review page, just like in the Ledger

---

## 3. Ledger — The command centre

Full view of all imported transactions, with filters for date, account, category and type.

**Direct editing:** change category, subcategory and context directly in the grid. Each change updates `category_source` to "manual".

**Checked by you:** tick *Checked by you* once you have verified the movement is real, that is the money really went out or came in. It is not about the category and it does not carry over to the movements of future imports: it is done one movement at a time, here only. Saving is immediate and the warning flag is automatically cleared.

**Create a rule from a transaction:**

1. Select **1 row** using the selection column (📏) in the ledger grid. If you select more than one row, the app shows a red error: *"You can only create and apply one rule at a time"*.
2. A **"Create rule"** form appears, pre-filled with:
   - **Pattern**: extracted from the transaction's counterpart (e.g. `ESSELUNGA`)
   - **Type**: *Contains text* (default). Available options: *Contains text*, *Exact match*, *Advanced expression*
   - **Category/Subcategory/Context**: copied from the selected transaction
3. A **preview** shows how many transactions will be matched by the rule
4. If the rule already exists, a yellow warning appears and the button changes to **"Edit rule and apply"**
5. Click **"Create rule and apply"** (or **"Edit rule and apply"**):
   - The rule is saved to the database
   - It is applied **retroactively** to all matching transactions that have not been validated yet
   - A toast confirms "Rule created" or "Rule updated"

---

## 4. Automatic rules

Rules created (from the Ledger, Review or Rules page) are automatically applied to future imports.

- **Priority**: more specific rules have higher priority. First match wins.
- **Match types**: *Exact match*, *Contains text*, *Advanced expression* (shown in the "Type" column of the rules grid).
- **Rules page**: manage, edit, delete existing rules. Use the "Run all rules" button to apply them in bulk to your entire history.

---

## 5. The virtuous cycle

```
Import --> AI categorises --> User reviews --> Create rule --> Next import: 0 interventions
```

The more you use Spendif.ai, the less manual work. Every rule you create reduces the number of transactions to review on the next import. Goal: **zero pain**.

---

## 5b. Auto-learning from history

In addition to rules, Spendif.ai automatically learns from the categories you decide. Every time you pick a category by hand, or one of your rules assigns it, the system records the association between the counterpart and the category.

### How it works

1. **Association table**: Spendif.ai groups all transactions with a category set by you by description + category + subcategory and counts occurrences.
2. **Homogeneity** — For each counterpart, it calculates how "stable" the classification is (from 0 to 1). If ESSELUNGA is *always* Food, homogeneity = 1.00. If AMAZON is spread across Technology, Food, Clothing, homogeneity is low.
3. **Automatic decision** — On the next import, before calling the AI, Spendif.ai checks history:
   - **Confidence >= 0.90** — assigns directly (source: History), no review needed
   - **Confidence 0.50–0.89** — suggests the category (source: History), but flags it for review
   - **Confidence < 0.50** — calls the AI as usual

### In practice

```
You put 12 ESSELUNGA transactions in Food by hand
  -> Next import with ESSELUNGA: classified automatically, zero interventions

You put 8 AUTOGRILL in Meals & lodging + 2 in Transport by hand
  -> Next import: suggests Meals & lodging, but puts it in review

AMAZON with 5 different categories
  -> Next import: calls the AI (too heterogeneous for history)
```

> **Important:** only transactions with a category **set by you** (by hand or by one of your rules) count for history. Those categorized by the AI have uncertain classifications and are not used for auto-learning. The *Checked by you* tick plays no part: it says the expense is real, not how you spent.

---

## Category and context

Every movement answers two different questions, each with its own field:

- **Category: why did I spend this money?** The reason for the spending: eating, staying overnight, getting around, the home.
- **Context: on what occasion?** The larger purpose the spending served: Daily life, Work, Holiday.

The line between **Groceries** and **Meals & lodging > Meals** is who prepared the food: in Groceries you buy food to cook at home, in Meals you eat food someone else prepared, at a restaurant, at a bar or delivered. That is why Meals is not split into restaurant, bar and takeaway.

A hotel is always a night's stay, whether you are on holiday or on a business trip: the category stays **Meals & lodging > Hotel / B&B**, and the context says Holiday or Work. That is why no category names an occasion: a "Travel & holidays" category would mix the reason with the occasion, and a dinner on holiday would land somewhere else than an everyday dinner.

The same goes for getting around. What matters is whether you put fuel in your own vehicle or travelled in someone else's: buses, trains, planes, ferries and taxis all go in **Transport > Public transport & taxi**. Your own vehicle splits by what goes into it: **Fuel (diesel / petrol / LPG)** or **EV charging**.

The picture by context is in **Reports**: what the holiday cost, broken down by category.

---

## Indicator columns

| Indicator | Meaning |
|-----------|---------|
| Warning | Needs review (uncertain classification) |
| Checked by you | Movement verified as real by the user |
| Internal transfer | Internal transfer (transfer between own accounts) |

---

## Checked and categorized

Spendif.ai keeps two **distinct** questions for every transaction, and one never answers the other:

- **Categorized by you: how did I spend this money?** It holds for a counterpart: one of your rules decides the category of the movements already loaded and of those of future imports. It is what the whole workflow asks: a counterpart with one of your rules is never asked again.
- **Checked by you: did I really spend this money?** It holds for one movement only and is never inherited: every new charge is a new fact. It is done in the Ledger only, one movement at a time.

Changing the category of a movement does not mark it as checked, and checking it does not decide its category.

### Checked vs. classification source

Spendif.ai tracks two **distinct** pieces of information for each transaction:

| Concept | Field | Meaning |
|---------|-------|---------|
| **Checked by you** | `human_validated` | "I have seen this expense and confirm it is real (not anomalous)." It is the approval of the **expense**, not the category. |
| **Classification source** | `category_source` | "Who assigned the latest category." Values: AI, Rule, Manual, History. |
| **Classification model** | `category_model` | "Which specific LLM model categorized this." E.g. `gemma-2-2b-it-Q4_K_M`. Empty when source = Rule or Manual. |

**Key rule:** the two fields are independent. When a rule, the AI or you reclassify a transaction, the source changes but the check **is not touched**. Only an explicit click on the *Checked by you* box sets or clears it.

**In practice:**
- You tick *Checked by you* on a transaction classified by AI as "Food" -> `checked=True`, `source=AI`
- Then you create a rule that reclassifies it as "Supermarket" -> `checked=True` (unchanged), `source=Rule`
- The check stays because you had already confirmed the expense was real; the category is now set by you because it comes from one of your rules

---

## Classification source badges

| Badge | Meaning |
|-------|---------|
| AI | Categorised by artificial intelligence |
| Rule | Categorised by deterministic rule |
| Manual | Manually modified by the user |
| History | Categorised from the history of your choices (auto-learning) |

---

For operational details on each page, see the [User Guide](guida_utente.en.md).
