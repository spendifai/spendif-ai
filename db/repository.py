"""Idempotent CRUD operations (RF-06, RF-07).

All write operations are upsert-style to guarantee idempotency.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from core.categorizer import CategoryRule as CoreCategoryRule
from core.rule_key import STARTS_WITH, exact_key, same_text, starts_with
from core.schemas import DocumentSchema
from db.models import (
    AUTOMATIC_CATEGORY_SOURCES,
    INTERNAL_TX_TYPES,
    same_counterpart,
    CategoryCorrection,
    CategoryRule,
    DEFAULT_USER_SETTINGS,
    DescriptionRule,
    DocumentSchemaModel,
    ImportBatch,
    ImportJob,
    PaymentIntermediary,
    InternalTransferLink,
    TransferDestination,
    HeldMovement,
    LlmUsageLog,
    NsiTagMapping,
    ReconciliationLink,
    Transaction,
    USER_CATEGORY_SOURCES,
    UserSettings,
)
from support.logging import setup_logging

logger = setup_logging()

# Sentinel per distinguere "non passato" da None in update_category_rule.context
_SENTINEL = object()


# ── ImportBatch ───────────────────────────────────────────────────────────────

def get_import_batch(session: Session, sha256: str) -> Optional[ImportBatch]:
    return session.query(ImportBatch).filter_by(sha256=sha256).first()


def get_all_batch_hashes(session: Session) -> set[str]:
    return {row.sha256 for row in session.query(ImportBatch.sha256).all()}


def get_import_history(session: Session, limit: int = 100) -> list[dict]:
    """Return import batches ordered by most recent first, with live transaction counts."""
    from sqlalchemy import func
    rows = (
        session.query(
            ImportBatch.id,
            ImportBatch.filename,
            ImportBatch.account_label,
            ImportBatch.imported_at,
            ImportBatch.status,
            func.count(Transaction.id).label("n_transactions"),
        )
        .outerjoin(Transaction, Transaction.batch_id == ImportBatch.id)
        .group_by(ImportBatch.id)
        .order_by(ImportBatch.imported_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "filename": r.filename,
            "account_label": r.account_label or "",
            "imported_at": r.imported_at,
            "status": r.status or "completed",
            "n_transactions": r.n_transactions,
        }
        for r in rows
    ]


def cancel_import_batch(session: Session, batch_id: int) -> int:
    """Hard-delete all transactions belonging to this batch, mark batch as cancelled.

    Returns the number of transactions deleted.
    """
    count = session.query(Transaction).filter(Transaction.batch_id == batch_id).count()
    if count > 0:
        session.query(Transaction).filter(Transaction.batch_id == batch_id).delete(
            synchronize_session="fetch"
        )
    batch = session.get(ImportBatch, batch_id)
    if batch:
        batch.status = "cancelled"
        batch.n_transactions = 0
    session.commit()
    return count


def create_import_batch(
    session: Session,
    sha256: str,
    filename: str,
    flow_used: str = "unknown",
    n_transactions: int = 0,
    errors: Optional[str] = None,
    account_label: Optional[str] = None,
) -> ImportBatch:
    existing = session.query(ImportBatch).filter_by(sha256=sha256).first()
    if existing:
        # Reimport di un batch precedentemente annullato: riattivare il batch
        # con i nuovi metadati, così l'UI mostra di nuovo il bottone "Annulla"
        # e il count è coerente con le tx effettivamente attaccate.
        if existing.status == "cancelled":
            existing.status = "completed"
            existing.n_transactions = n_transactions
            existing.filename = filename
            existing.flow_used = flow_used
            existing.errors = errors
            existing.account_label = account_label
            session.flush()
        return existing
    batch = ImportBatch(
        sha256=sha256,
        filename=filename,
        flow_used=flow_used,
        n_transactions=n_transactions,
        errors=errors,
        account_label=account_label,
        status="completed",
    )
    session.add(batch)
    session.flush()
    return batch


# ── DocumentSchema ────────────────────────────────────────────────────────────

def get_all_transfer_keyword_patterns(session: Session) -> list[str]:
    """Return the union of all internal_transfer_patterns across every known schema."""
    rows = session.query(DocumentSchemaModel).all()
    patterns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for p in json.loads(row.internal_transfer_patterns or "[]"):
            if p and p not in seen:
                patterns.append(p)
                seen.add(p)
    return patterns


def get_document_schema(session: Session, source_identifier: str) -> Optional[DocumentSchema]:
    row = session.query(DocumentSchemaModel).filter_by(source_identifier=source_identifier).first()
    if row is None:
        return None
    return _row_to_schema(row)


def find_schema_by_header_sha256(session: Session, header_sha256: str) -> Optional[DocumentSchema]:
    """Look up a saved schema by the SHA256 of the file's first rows.
    Returns the most recently updated schema if multiple match (shouldn't happen in practice)."""
    row = (
        session.query(DocumentSchemaModel)
        .filter_by(header_sha256=header_sha256)
        .order_by(DocumentSchemaModel.updated_at.desc().nullslast())
        .first()
    )
    if row is None:
        return None
    return _row_to_schema(row)


def delete_document_schema(session: Session, source_identifier: str) -> bool:
    """Delete a single cached schema by source_identifier. Returns True if a row was deleted."""
    count = (
        session.query(DocumentSchemaModel)
        .filter_by(source_identifier=source_identifier)
        .delete()
    )
    session.flush()
    return count > 0


def delete_all_schemas(session: Session) -> int:
    """Delete all cached document schemas. Returns the number of rows deleted."""
    count = session.query(DocumentSchemaModel).delete()
    session.flush()
    return count


def upsert_document_schema(session: Session, schema: DocumentSchema) -> DocumentSchemaModel:
    row = session.query(DocumentSchemaModel).filter_by(
        source_identifier=schema.source_identifier
    ).first()

    if row is None:
        row = DocumentSchemaModel(source_identifier=schema.source_identifier)
        session.add(row)

    row.doc_type = schema.doc_type.value if hasattr(schema.doc_type, 'value') else schema.doc_type
    row.date_col = schema.date_col
    row.date_accounting_col = schema.date_accounting_col
    row.amount_col = schema.amount_col
    row.debit_col = schema.debit_col
    row.credit_col = schema.credit_col
    row.description_col = schema.description_col
    row.description_cols = json.dumps(schema.description_cols) if schema.description_cols else "[]"
    row.currency_col = schema.currency_col
    row.default_currency = schema.default_currency
    row.date_format = schema.date_format
    row.sign_convention = schema.sign_convention.value if hasattr(schema.sign_convention, 'value') else schema.sign_convention
    row.is_zero_sum = schema.is_zero_sum
    row.invert_sign = schema.invert_sign
    # Never cleared by a later import: the seal outlives the classification
    # that happened to run today.
    if getattr(schema, "user_confirmed", False):
        row.user_confirmed = True
    row.internal_transfer_patterns = json.dumps(schema.internal_transfer_patterns)
    row.footer_patterns = json.dumps(getattr(schema, 'footer_patterns', []) or [])
    row.has_borders = getattr(schema, 'has_borders', False)
    row.account_label = schema.account_label
    row.encoding = schema.encoding
    row.sheet_name = schema.sheet_name
    row.skip_rows = schema.skip_rows
    row.delimiter = schema.delimiter
    row.confidence = schema.confidence.value if hasattr(schema.confidence, 'value') else schema.confidence
    row.confidence_score = getattr(schema, 'confidence_score', None)
    if schema.header_sha256:
        row.header_sha256 = schema.header_sha256

    session.flush()
    return row


def _row_to_schema(row: DocumentSchemaModel) -> DocumentSchema:
    from core.models import DocumentType, SignConvention, Confidence
    return DocumentSchema(
        doc_type=DocumentType(row.doc_type),
        date_col=row.date_col or "",
        date_accounting_col=row.date_accounting_col,
        amount_col=row.amount_col or "",
        debit_col=row.debit_col,
        credit_col=row.credit_col,
        description_col=row.description_col,
        description_cols=json.loads(getattr(row, "description_cols", None) or "[]"),
        currency_col=row.currency_col,
        default_currency=row.default_currency or "EUR",
        date_format=row.date_format or "%d/%m/%Y",
        sign_convention=SignConvention(row.sign_convention or "signed_single"),
        is_zero_sum=bool(row.is_zero_sum),
        invert_sign=bool(row.invert_sign) if row.invert_sign is not None else False,
        user_confirmed=bool(getattr(row, "user_confirmed", False)),
        internal_transfer_patterns=json.loads(row.internal_transfer_patterns or "[]"),
        footer_patterns=json.loads(getattr(row, 'footer_patterns', None) or "[]"),
        has_borders=bool(getattr(row, 'has_borders', False)),
        account_label=row.account_label or "",
        encoding=row.encoding or "utf-8",
        sheet_name=row.sheet_name,
        skip_rows=row.skip_rows or 0,
        delimiter=row.delimiter,
        confidence=Confidence(row.confidence or "low"),
        confidence_score=getattr(row, 'confidence_score', None) or 0.0,
        source_identifier=row.source_identifier,
        header_sha256=getattr(row, 'header_sha256', None),
    )


def update_footer_patterns(session: Session, source_identifier: str, new_patterns: list[str]) -> None:
    """Merge new footer patterns into the existing set for a schema (dedup, order-preserving)."""
    row = session.query(DocumentSchemaModel).filter_by(source_identifier=source_identifier).first()
    if row is None:
        return
    existing = json.loads(getattr(row, 'footer_patterns', None) or "[]")
    merged = list(dict.fromkeys(existing + new_patterns))  # dedup preserving order
    row.footer_patterns = json.dumps(merged)
    session.flush()


# ── Transaction ───────────────────────────────────────────────────────────────

def upsert_transaction(session: Session, tx: dict, batch_id: Optional[int] = None) -> Transaction:
    """Insert or skip (idempotent) based on transaction id (SHA-256[:24])."""
    existing = session.get(Transaction, tx["id"])
    if existing is not None:
        return existing  # already imported, skip

    amount = tx["amount"]
    if isinstance(amount, Decimal):
        amount_val = float(amount)
    else:
        amount_val = float(str(amount))

    row = Transaction(
        id=tx["id"],
        batch_id=batch_id,
        date=tx["date"].isoformat() if hasattr(tx["date"], "isoformat") else str(tx["date"]),
        date_accounting=tx.get("date_accounting").isoformat() if tx.get("date_accounting") and hasattr(tx["date_accounting"], "isoformat") else tx.get("date_accounting"),
        amount=amount_val,
        currency=tx.get("currency", "EUR"),
        # Written by the model's validator (written_counterpart, spec 21):
        # never forced to upper case, comparisons ignore case.
        description=tx.get("description"),
        source_file=tx.get("source_file", ""),
        doc_type=tx.get("doc_type", ""),
        account_label=tx.get("account_label", ""),
        tx_type=tx.get("tx_type", "unknown"),
        category=tx.get("category"),
        subcategory=tx.get("subcategory"),
        category_confidence=tx.get("category_confidence"),
        category_source=tx.get("category_source"),
        category_model=tx.get("category_model"),
        model_category=tx.get("model_category"),
        model_subcategory=tx.get("model_subcategory"),
        model_confidence=tx.get("model_confidence"),
        model_source=tx.get("model_source"),
        reconciled=bool(tx.get("reconciled", False)),
        to_review=bool(tx.get("to_review", False)),
        transfer_pair_id=tx.get("transfer_pair_id"),
        transfer_confidence=tx.get("transfer_confidence"),
        raw_description=tx.get("raw_description"),
        raw_amount=tx.get("raw_amount"),
        human_validated=bool(tx.get("human_validated", False)),
    )
    session.add(row)
    return row


def _iso(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)[:10]


# Fields of a movement kept by HeldMovement.payload: what upsert_transaction takes.
_HELD_FIELDS = (
    "id", "date", "date_accounting", "amount", "currency", "description", "source_file",
    "doc_type", "account_label", "tx_type", "category", "subcategory",
    "category_confidence", "category_source", "category_model", "reconciled",
    "to_review", "transfer_pair_id", "transfer_confidence", "raw_description",
    "raw_amount", "human_validated",
    # The import's own proposal and its source, kept until the movement is due.
    "model_category", "model_subcategory", "model_confidence", "model_source",
)


def hold_movement(session: Session, tx: dict, batch_id: Optional[int], reason: str) -> None:
    """Set a movement aside in full (see HeldMovement). Idempotent on its id."""
    if session.get(HeldMovement, tx["id"]) is not None:
        return
    payload = {k: tx.get(k) for k in _HELD_FIELDS if k in tx}
    session.add(HeldMovement(
        id=tx["id"],
        payload=json.dumps(payload, default=str),
        date=_iso(tx["date"]),
        account_label=tx.get("account_label"),
        amount=Decimal(str(tx.get("amount") or 0)),
        description=tx.get("description") or tx.get("raw_description"),
        source_file=tx.get("source_file"),
        reason=reason,
        batch_id=batch_id,
    ))


def get_held_movements(session: Session) -> list[HeldMovement]:
    return session.query(HeldMovement).order_by(HeldMovement.date).all()


def release_held_movement(session: Session, held_id: str, new_date: str) -> bool:
    """The movement enters the ledger with ``new_date``. False when the same
    movement is already there (nothing inserted twice); the held copy goes
    either way."""
    held = session.get(HeldMovement, held_id)
    if held is None:
        return False
    tx = json.loads(held.payload)
    tx["date"] = new_date
    inserted = session.get(Transaction, tx["id"]) is None
    upsert_transaction(session, tx, batch_id=held.batch_id)
    session.delete(held)
    return inserted


def discard_held_movement(session: Session, held_id: str) -> None:
    held = session.get(HeldMovement, held_id)
    if held is not None:
        session.delete(held)


def hold_future_transactions(session: Session, today: str) -> int:
    """Move the movements already in the ledger with a date after ``today``
    to HeldMovement: imported before the check existed."""
    txs = session.query(Transaction).filter(Transaction.date > today).all()
    if not txs:
        return 0
    ids = [t.id for t in txs]
    for t in txs:
        hold_movement(session, {k: getattr(t, k) for k in _HELD_FIELDS},
                      batch_id=t.batch_id, reason="date_future")
    session.query(ReconciliationLink).filter(
        (ReconciliationLink.settlement_id.in_(ids)) | (ReconciliationLink.detail_id.in_(ids))
    ).delete(synchronize_session=False)
    session.query(InternalTransferLink).filter(
        (InternalTransferLink.out_id.in_(ids)) | (InternalTransferLink.in_id.in_(ids))
    ).delete(synchronize_session=False)
    for t in txs:
        session.delete(t)
    return len(txs)


def get_existing_tx_ids(session: Session, tx_ids: list[str]) -> set[str]:
    """Return the subset of tx_ids that already exist in the DB."""
    if not tx_ids:
        return set()
    rows = session.query(Transaction.id).filter(Transaction.id.in_(tx_ids)).all()
    return {row.id for row in rows}


# ── Coherent categorization (spec 22) ─────────────────────────────────────────

def known_counterpart_categories(session: Session) -> dict[tuple[str, str], tuple[str, str]]:
    """{(counterpart key, direction): (category, subcategory)} from the ledger.

    The most frequent category of each counterpart, so the model is not
    asked again about a counterpart it (or the user) already categorized
    (spec 22). Choices made by hand on one movement are one-off exceptions
    and do not count; neither do movements without a category, internal
    transfers, or the fallback category ("Altro" is no example: the
    counterpart goes back to the model). When some movements of the
    counterpart were checked by the user, their category was confirmed with
    the check (spec 16, principle 7): only those count.
    """
    from collections import Counter, defaultdict
    from core.rule_key import exact_key
    fallback = {tuple(v) for v in get_fallback_categories(session).values()}
    fallback_cats = {c for c, _ in fallback}
    counts: dict[tuple[str, str], Counter] = defaultdict(Counter)
    checked: dict[tuple[str, str], Counter] = defaultdict(Counter)
    rows = (
        session.query(Transaction.description, Transaction.amount,
                      Transaction.category, Transaction.subcategory,
                      Transaction.human_validated)
        .filter(
            Transaction.category.isnot(None), Transaction.category != "",
            Transaction.category_source.is_distinct_from("manual"),
            Transaction.tx_type.notin_(("internal_out", "internal_in")),
        )
        .all()
    )
    for desc, amount, cat, sub, validated in rows:
        if cat in fallback_cats:
            continue
        key = exact_key(desc)
        if not key:
            continue
        direction = "out" if float(amount or 0) < 0 else "in"
        counts[(key, direction)][(cat, sub or "")] += 1
        if validated:
            checked[(key, direction)][(cat, sub or "")] += 1
    return {k: (checked.get(k) or c).most_common(1)[0][0] for k, c in counts.items()}


def rewrite_with_model_answers(session: Session, transactions: list[dict]) -> int:
    """After a file is saved: each counterpart the model answered in it with
    a category other than the fallback rewrites its automatic past (spec 22,
    5bis). One answer per counterpart and direction. Returns how many
    movements changed."""
    from core.rule_key import exact_key
    fallback_cats = {c for c, _ in get_fallback_categories(session).values()}
    answers: dict[tuple, tuple] = {}
    for tx in transactions:
        if tx.get("category_source") != "llm" or not tx.get("category"):
            continue
        if tx["category"] in fallback_cats or tx.get("tx_type") in INTERNAL_TX_TYPES:
            continue
        key = exact_key(tx.get("description") or "")
        if not key:
            continue
        negative = float(tx.get("amount") or 0) < 0
        answers.setdefault((key, negative), (tx["description"], tx["category"], tx.get("subcategory") or ""))
    n = 0
    for (_key, negative), (desc, cat, sub) in answers.items():
        n += rewrite_counterpart_category(session, desc, negative, cat, sub)
    return n


def rewrite_counterpart_category(session: Session, description: str, negative: bool,
                                 category: str, subcategory: str,
                                 exclude_ids: set | None = None) -> int:
    """The model's answer for a counterpart holds for its past too (spec 22,
    5bis): every saved movement of the counterpart in that direction with an
    automatic category (llm, nsi, history, "Altro" included) takes it. Never
    touched: the user's rules and choices, checked movements, multi-category
    counterparts, giroconti, and accepted counterparts, whose category the
    user kept as it was (spec 16, 3.4). Returns how many changed."""
    direction = "out" if negative else "in"
    if any(r.direction in (None, direction) and r.matches(description)
           for r in get_category_rules(session, include_accepted=True) if r.not_show):
        return 0
    q = session.query(Transaction).filter(
        same_counterpart(Transaction.description, description),
        Transaction.amount < 0 if negative else Transaction.amount >= 0,
        Transaction.category_source.in_(AUTOMATIC_CATEGORY_SOURCES),
        Transaction.human_validated.isnot(True),
        Transaction.tx_type.notin_(INTERNAL_TX_TYPES),
    )
    n = 0
    for tx in q:
        if exclude_ids and tx.id in exclude_ids:
            continue
        if (tx.category, tx.subcategory or "") == (category, subcategory or ""):
            continue
        tx.category, tx.subcategory = category, subcategory
        tx.category_source = "llm"
        n += 1
    if n:
        session.flush()
    return n


def known_signatures(session: Session) -> dict[str, str]:
    """{raw signature: counterpart} from the ledger (spec 22).

    One signature, one counterpart: the oldest movement decides, so a
    signature never gets two. Internal transfers do not count.
    """
    from core.normalizer import raw_signature
    out: dict[str, str] = {}
    rows = (
        session.query(Transaction.raw_description, Transaction.description)
        .filter(
            Transaction.raw_description.isnot(None),
            Transaction.description.isnot(None), Transaction.description != "",
            Transaction.tx_type.notin_(("internal_out", "internal_in")),
        )
        .order_by(Transaction.created_at, Transaction.id)
        .all()
    )
    for raw, desc in rows:
        sig = raw_signature(raw)
        if sig and sig not in out:
            out[sig] = desc
    return out


# ── Payment intermediaries (spec 20) ─────────────────────────────────────────

def get_intermediaries(session: Session) -> list[PaymentIntermediary]:
    return session.query(PaymentIntermediary).order_by(PaymentIntermediary.prefix).all()


def active_intermediary_prefixes(session: Session) -> set[str]:
    return {r.prefix for r in session.query(PaymentIntermediary.prefix)
            .filter(PaymentIntermediary.active.is_(True))}


def add_intermediaries(session: Session, prefixes, origin: str) -> list[str]:
    """Add the prefixes not in the table yet, active. Existing rows are never
    touched: a deactivated one stays deactivated. Returns the added ones."""
    existing = {r.prefix for r in session.query(PaymentIntermediary.prefix)}
    added = [p for p in dict.fromkeys(prefixes) if p and p not in existing]
    for p in added:
        session.add(PaymentIntermediary(prefix=p, origin=origin, active=True))
    if added:
        session.flush()
    return added


def raw_descriptions(session: Session) -> list[str]:
    """Every saved raw description (the Intermediaries page counts on them)."""
    return [r[0] for r in session.query(Transaction.raw_description)
            .filter(Transaction.raw_description.isnot(None))]


def raw_descriptions_with_asterisk(session: Session) -> list[str]:
    """Saved raw descriptions in the "PREFIX *REST" form candidates."""
    return [r[0] for r in session.query(Transaction.raw_description)
            .filter(Transaction.raw_description.like("%*%"))]


def set_intermediary_active(session: Session, prefix: str, active: bool) -> bool:
    row = session.get(PaymentIntermediary, prefix)
    if row is None:
        return False
    row.active = active
    session.flush()
    return True


def rewrite_intermediary_descriptions(session: Session, active: set[str]) -> list[str]:
    """Write the merchant on saved movements whose description still shows an
    active intermediary ("PAYPAL", "PAYPAL *EASYPARK"). Returns their ids."""
    from core.intermediaries import is_dirty
    if not active:
        return []
    changed = []
    # No filter on the asterisk: an intermediary is found anywhere (spec 20).
    for tx in session.query(Transaction).filter(Transaction.raw_description.isnot(None)):
        merchant = is_dirty(tx.description, tx.raw_description, active)
        if merchant and merchant != tx.description:
            tx.description = merchant
            changed.append(tx.id)
    if changed:
        session.flush()
    return changed


def apply_rules_to_transactions(session: Session, tx_ids: list[str],
                                user_rules: list[CoreCategoryRule]) -> int:
    """Apply the category rules to the given movements only; a category
    chosen by hand stays (spec 18). Returns how many changed."""
    if not tx_ids or not user_rules:
        return 0
    rules = _rules_in_order(user_rules)
    fallbacks = get_fallback_categories(session)
    n = 0
    for tx in session.query(Transaction).filter(Transaction.id.in_(tx_ids)):
        # A choice by hand and a checked movement (its category confirmed,
        # spec 16 principle 7) are the user's: no rule changes them.
        if tx.category_source == "manual" or tx.human_validated:
            continue
        for rule in rules:
            if rule.matches(tx.description or "", tx.doc_type, tx.amount):
                if rule.not_run:
                    pass  # decided, never run: no other rule takes it either
                elif rule.is_multi_category:
                    n += _make_multi_category(tx, fallbacks)
                elif rule.is_giroconto:
                    n += bool(_make_giroconto(tx))
                elif tx.tx_type in INTERNAL_TX_TYPES:
                    pass  # a giroconto has no category
                else:
                    tx.category, tx.subcategory = rule.category, rule.subcategory
                    if rule.context:
                        tx.context = rule.context
                    tx.category_source, tx.category_model = "rule", None
                    tx.category_confidence, tx.to_review = "high", False
                    n += 1
                break
    if n:
        session.flush()
    return n


def get_movements_from_other_files(
    session: Session, account_label: str, dates: list[str], source_file: str,
) -> list[dict]:
    """Saved movements of the account on those dates that came from another file.

    The candidates for recognising a movement exported in two formats
    (core.dedup.find_twins). A movement from the same file is never a
    candidate: two identical rows of one file are two movements.
    """
    from sqlalchemy import func
    if not dates:
        return []
    rows = (
        session.query(Transaction, ImportBatch.filename)
        .outerjoin(ImportBatch, ImportBatch.id == Transaction.batch_id)
        .filter(
            func.lower(Transaction.account_label) == (account_label or "").lower(),
            Transaction.date.in_(sorted(set(dates))),
        )
        .all()
    )
    return [
        {"id": tx.id, "account_label": tx.account_label, "date": tx.date,
         "amount": tx.amount, "raw_description": tx.raw_description,
         "description": tx.description, "filename": filename or tx.source_file}
        for tx, filename in rows
        if (filename or tx.source_file) != source_file
    ]


def _compute_consistency(session: Session, description: str) -> float | None:
    """% of categorized transactions with same description that agree on modal category."""
    from collections import Counter
    rows = (
        session.query(Transaction.category)
        .filter(
            same_counterpart(Transaction.description, description),
            Transaction.category.isnot(None),
        )
        .all()
    )
    if not rows:
        return None
    counts = Counter(r[0] for r in rows)
    modal_count = counts.most_common(1)[0][1]
    return round(modal_count / len(rows) * 100, 1)


def update_transaction_category(
    session: Session,
    tx_id: str,
    category: str,
    subcategory: str,
    origin: str = "unknown",
    source: str = "manual",
) -> bool:
    """Set the category of one movement on the user's behalf.

    `source` says how the user decided: "manual" for this movement only,
    "rule" when the category comes from a rule applied to the movements
    already in the ledger. Recording a rule application as manual makes a
    counterpart look "mixed" as soon as the next import adds rule-matched
    movements, even when the category is the same. Either way the movement
    counts as categorized by the user and a changed category is logged as a
    correction of the previous source. It does not count as checked: choosing
    how the money was spent says nothing on whether it was spent.
    """
    from datetime import datetime, timezone
    if source not in ("manual", "rule"):
        raise ValueError(f"unknown category source: {source}")
    tx = session.get(Transaction, tx_id)
    if tx is None:
        return False
    old_cat = tx.category
    old_sub = tx.subcategory
    old_src = tx.category_source
    old_model = tx.category_model
    old_conf = tx.category_confidence
    category_changed = old_cat != category or old_sub != subcategory
    if category_changed and old_src in ("rule", *AUTOMATIC_CATEGORY_SOURCES):
        consistency = _compute_consistency(session, tx.description or "")
        correction = CategoryCorrection(
            tx_id=tx_id,
            original_category=old_cat,
            original_subcategory=old_sub,
            original_source=old_src,
            original_model=old_model,
            original_confidence=old_conf,
            new_category=category,
            new_subcategory=subcategory,
            consistency_at_correction=consistency,
            correction_origin=origin,
            corrected_at=datetime.now(timezone.utc),
        )
        session.add(correction)
    tx.category = category
    tx.subcategory = subcategory
    tx.category_confidence = "high"
    tx.category_source = source
    # category_model stays: it names the model of the proposal, which the
    # model_* columns keep beside the user's decision.
    tx.to_review = False
    return True


def get_correction_benchmark(session: Session) -> list[dict]:
    """Live implicit benchmark: per-model corrections vs total LLM categorizations.

    Returns one dict per model with:
      model, total_categorized, total_corrections, implicit_accuracy,
      high_conf_errors, avg_consistency_at_error
    """
    from collections import Counter, defaultdict

    # Still-LLM categorizations (not yet corrected by user)
    still_llm_rows = (
        session.query(Transaction.category_model)
        .filter(
            Transaction.category_source == "llm",
            Transaction.category_model.isnot(None),
        )
        .all()
    )
    total_by_model: Counter = Counter(r[0] for r in still_llm_rows)

    # Corrections where original source was llm
    corr_rows = (
        session.query(CategoryCorrection)
        .filter(CategoryCorrection.original_source == "llm")
        .all()
    )

    corrections: dict[str, list] = defaultdict(list)
    for c in corr_rows:
        if c.original_model:
            corrections[c.original_model].append(c)

    all_models = set(total_by_model.keys()) | set(corrections.keys())
    results = []
    for model in sorted(all_models):
        corr_list = corrections.get(model, [])
        n_corr = len(corr_list)
        # total = still-LLM + already-corrected (corrected txs left the 'llm' source)
        n_total = total_by_model.get(model, 0) + n_corr
        high_conf = sum(1 for c in corr_list if c.original_confidence == "high")
        consistency_vals = [
            c.consistency_at_correction
            for c in corr_list
            if c.consistency_at_correction is not None
        ]
        avg_cons = round(sum(consistency_vals) / len(consistency_vals), 1) if consistency_vals else None
        implicit_acc = round((1 - n_corr / n_total) * 100, 1) if n_total > 0 else None
        results.append(
            {
                "model": model,
                "total_categorized": n_total,
                "total_corrections": n_corr,
                "implicit_accuracy": implicit_acc,
                "high_conf_errors": high_conf,
                "avg_consistency_at_error": avg_cons,
            }
        )
    return results


def toggle_transaction_giroconto(session: Session, tx_id: str) -> tuple[bool, str]:
    """Toggle a transaction's tx_type between giroconto and expense/income.

    If currently internal_out / internal_in → revert to expense or income based on sign.
    Otherwise → mark as internal_out (negative amount) or internal_in (positive amount).

    Returns (ok, new_tx_type).
    """
    tx = session.get(Transaction, tx_id)
    if tx is None:
        return False, ""
    internal = {"internal_out", "internal_in"}
    if tx.tx_type in internal:
        leave_giroconto(session, tx)
    else:
        # Mark as giroconto
        tx.tx_type = "internal_in" if float(tx.amount or 0) >= 0 else "internal_out"
    return True, tx.tx_type


def leave_giroconto(session: Session, tx: Transaction) -> None:
    """A giroconto turns back into income or spending by its sign.

    Setting the type gives back the product's proposal from the import, to
    review (Transaction._giroconto_has_no_category). Without a proposal the
    movement takes the fallback category of its direction, to review: it
    never stays without one (founder, 2026-10-23).
    """
    tx.tx_type = "income" if float(tx.amount or 0) >= 0 else "expense"
    if not tx.category:
        tx.category, tx.subcategory = get_fallback_categories(session)[tx.tx_type]
        tx.category_source = "llm"
        tx.category_confidence = "low"
        tx.to_review = True


def update_transaction_context(session: Session, tx_id: str, context: str | None) -> bool:
    """Set or clear the context of a transaction. Returns True if found."""
    tx = session.get(Transaction, tx_id)
    if tx is None:
        return False
    tx.context = context or None
    session.flush()
    return True


def update_transaction_description(session: Session, tx_id: str, description: str | None) -> bool:
    """The user's own counterpart for one movement. Written like every
    counterpart (the column's validator); the bank's text (raw_description)
    is never touched, and nothing is recategorized: the user decided. Not a
    check that the money was spent, so human_validated stays as it is.
    Returns True if the counterpart changed."""
    tx = session.get(Transaction, tx_id)
    if tx is None or not (description or "").strip():
        return False
    before = tx.description
    tx.description = description
    if tx.description == before:
        return False
    session.flush()
    return True


def get_similar_transactions(
    session: Session, description: str, exclude_id: str = "", threshold: float = 0.35
) -> list[Transaction]:
    """Return transactions whose description is similar to the given one.

    Uses Jaccard similarity on word tokens (case-insensitive).
    Only transactions with a non-empty description are considered.
    """
    if not description:
        return []
    ref_tokens = set(description.lower().split())
    if not ref_tokens:
        return []
    txs = session.query(Transaction).filter(
        Transaction.description.isnot(None),
        Transaction.description != "",
        Transaction.id != exclude_id,
    ).all()
    result = []
    for tx in txs:
        tokens = set((tx.description or "").lower().split())
        if not tokens:
            continue
        similarity = len(ref_tokens & tokens) / len(ref_tokens | tokens)
        if similarity >= threshold:
            result.append(tx)
    return result


def bulk_set_giroconto_by_description(
    session: Session, description: str, make_giroconto: bool, exclude_id: str = ""
) -> int:
    """Set giroconto status for all transactions matching description.

    make_giroconto=True  → internal_in / internal_out based on sign
    make_giroconto=False → income / expense based on sign

    Returns count of transactions actually changed.
    """
    txs = session.query(Transaction).filter(same_counterpart(Transaction.description, description)).all()
    internal = {"internal_out", "internal_in"}
    updated = 0
    for tx in txs:
        if tx.id == exclude_id:
            continue
        is_internal = tx.tx_type in internal
        if make_giroconto and not is_internal:
            tx.tx_type = "internal_in" if float(tx.amount or 0) >= 0 else "internal_out"
            updated += 1
        elif not make_giroconto and is_internal:
            leave_giroconto(session, tx)
            updated += 1
    if updated:
        session.flush()
    return updated


def get_transactions(
    session: Session,
    filters: Optional[dict] = None,
    limit: Optional[int] = None,
    offset: int = 0,
) -> list[Transaction]:
    q = session.query(Transaction)
    if filters:
        if "tx_type" in filters:
            q = q.filter(Transaction.tx_type == filters["tx_type"])
        if "category" in filters:
            q = q.filter(Transaction.category == filters["category"])
        if "date_from" in filters:
            q = q.filter(Transaction.date >= filters["date_from"])
        if "date_to" in filters:
            q = q.filter(Transaction.date <= filters["date_to"])
        if "to_review" in filters:
            q = q.filter(Transaction.to_review == filters["to_review"])
        if "category_source" in filters:
            q = q.filter(Transaction.category_source == filters["category_source"])
        if "account_label" in filters:
            q = q.filter(Transaction.account_label == filters["account_label"])
        if "description" in filters:
            _term = f"%{filters['description']}%"
            from sqlalchemy import or_
            q = q.filter(
                or_(
                    Transaction.description.ilike(_term),
                    Transaction.raw_description.ilike(_term),
                )
            )
        if "subcategory" in filters:
            q = q.filter(Transaction.subcategory == filters["subcategory"])
        if "categories" in filters:
            q = q.filter(Transaction.category.in_(filters["categories"]))
        if "subcategories" in filters:
            q = q.filter(Transaction.subcategory.in_(filters["subcategories"]))
        if "context" in filters:
            if filters["context"] is None:
                q = q.filter(Transaction.context.is_(None))
            else:
                q = q.filter(Transaction.context == filters["context"])
        if "contexts" in filters:
            q = q.filter(Transaction.context.in_(filters["contexts"]))
        if "exclude_tx_types" in filters:
            q = q.filter(Transaction.tx_type.notin_(filters["exclude_tx_types"]))
    q = q.order_by(Transaction.date.desc())
    if offset:
        q = q.offset(offset)
    if limit:
        q = q.limit(limit)
    return q.all()


# ── ReconciliationLink ────────────────────────────────────────────────────────

def create_reconciliation_link(
    session: Session,
    settlement_id: str,
    detail_id: str,
    delta: float = 0.0,
    method: str = "",
) -> ReconciliationLink:
    existing = (
        session.query(ReconciliationLink)
        .filter_by(settlement_id=settlement_id, detail_id=detail_id)
        .first()
    )
    if existing:
        return existing
    link = ReconciliationLink(
        settlement_id=settlement_id,
        detail_id=detail_id,
        delta=delta,
        method=method,
    )
    session.add(link)
    return link


# ── InternalTransferLink ──────────────────────────────────────────────────────

def create_transfer_link(
    session: Session,
    out_id: str,
    in_id: str,
    confidence: str,
    keyword_matched: bool,
) -> InternalTransferLink:
    existing = (
        session.query(InternalTransferLink)
        .filter_by(out_id=out_id, in_id=in_id)
        .first()
    )
    if existing:
        return existing
    link = InternalTransferLink(
        out_id=out_id,
        in_id=in_id,
        confidence=confidence,
        keyword_matched=keyword_matched,
    )
    session.add(link)
    return link


def get_unpaired_movements(
    session: Session, date_from: str | None = None, date_to: str | None = None,
    ids: set[str] | None = None,
) -> list[Transaction]:
    """Movements without a transfer pair, card purchases left out, optionally
    between two ISO dates (both included) or among ``ids``."""
    q = session.query(Transaction).filter(
        Transaction.transfer_pair_id.is_(None),
        Transaction.tx_type != "card_tx",
    )
    if date_from:
        q = q.filter(Transaction.date >= date_from)
    if date_to:
        q = q.filter(Transaction.date <= date_to)
    if ids is not None:
        q = q.filter(Transaction.id.in_(ids))
    return q.all()


def set_transfer_pair(
    session: Session, out_tx: Transaction, in_tx: Transaction,
    confidence: str, make_internal: bool,
) -> None:
    """Join two movements as the two sides of one transfer. ``make_internal``
    turns both into internal_out / internal_in; otherwise the pair is only
    proposed and the movements keep their type until the user confirms."""
    pair_id = f"x_{out_tx.id}_{in_tx.id}"
    for tx in (out_tx, in_tx):
        tx.transfer_pair_id = pair_id
        tx.transfer_confidence = confidence
    if make_internal:
        out_tx.tx_type = "internal_out"
        in_tx.tx_type = "internal_in"
    create_transfer_link(
        session, out_id=out_tx.id, in_id=in_tx.id,
        confidence=confidence, keyword_matched=False,
    )


def get_rejected_transfer_pairs(session: Session) -> set[tuple[str, str]]:
    """(out_id, in_id) of the pairs the user said are not one transfer."""
    rows = session.query(InternalTransferLink).filter(
        InternalTransferLink.user_decision == "no").all()
    return {(r.out_id, r.in_id) for r in rows}


def get_open_transfer_links(session: Session) -> list[InternalTransferLink]:
    """Pairs still standing: answered "yes" or not answered yet."""
    return session.query(InternalTransferLink).filter(
        (InternalTransferLink.user_decision.is_(None))
        | (InternalTransferLink.user_decision == "yes")).all()


def decide_transfer_pair(session: Session, out_id: str, in_id: str, decision: str) -> None:
    """The user's answer on a pair. "yes": both movements become the two sides
    of a transfer. "no": the pair is undone and kept only on the link, so it is
    never proposed again; each movement keeps the type the import gave it."""
    link = session.query(InternalTransferLink).filter_by(out_id=out_id, in_id=in_id).first()
    if link is None:
        return
    link.user_decision = decision
    out_tx, in_tx = session.get(Transaction, out_id), session.get(Transaction, in_id)
    if decision == "yes":
        if out_tx is not None:
            out_tx.tx_type = "internal_out"
        if in_tx is not None:
            in_tx.tx_type = "internal_in"
    else:
        for tx in (out_tx, in_tx):
            if tx is not None:
                tx.transfer_pair_id = None
                tx.transfer_confidence = None


def get_one_sided_transfers(session: Session, date_from: str | None = None) -> list[Transaction]:
    """Movements read as transfers whose other side was not found."""
    q = session.query(Transaction).filter(
        Transaction.tx_type.in_(("internal_out", "internal_in")),
        Transaction.transfer_pair_id.is_(None),
    )
    if date_from:
        q = q.filter(Transaction.date >= date_from)
    return q.all()


def get_transfer_destinations(session: Session) -> dict[str, str]:
    return {r.key: r.answer for r in session.query(TransferDestination).all()}


def set_transfer_destination(session: Session, key: str, answer: str) -> None:
    row = session.get(TransferDestination, key)
    if row is None:
        session.add(TransferDestination(key=key, answer=answer))
    else:
        row.answer = answer


# ── CategoryRule ──────────────────────────────────────────────────────────────

def get_category_rules(session: Session, include_accepted: bool = False) -> list[CoreCategoryRule]:
    """The rules to run and to list. Accepted counterparts (not_show) are
    left out: they are neither run nor shown, and count only where the
    question is whether a counterpart is done (include_accepted). A
    multi-category rule is listed; the rule engine does not run it (not_run)."""
    q = session.query(CategoryRule)
    if not include_accepted:
        q = q.filter(CategoryRule.not_show.isnot(True))
    rows = q.order_by(CategoryRule.priority.desc()).all()
    return [
        CoreCategoryRule(
            id=row.id,
            pattern=row.pattern,
            match_type=row.match_type,
            category=row.category,
            subcategory=row.subcategory,
            context=row.context or None,
            doc_type=row.doc_type,
            priority=row.priority or 0,
            is_giroconto=bool(row.is_giroconto),
            is_multi_category=bool(row.is_multi_category),
            not_show=bool(row.not_show),
            not_run=bool(row.not_run),
            direction=row.direction,
        )
        for row in rows
    ]


def _rules_in_order(user_rules: list[CoreCategoryRule]) -> list[CoreCategoryRule]:
    """Multi-category rules first (spec 24: they win on their counterpart),
    then by priority, highest first."""
    return sorted(user_rules, key=lambda r: (not r.is_multi_category, -(r.priority or 0)))


def _make_multi_category(tx: Transaction, fallbacks: dict[str, tuple[str, str]]) -> bool:
    """A movement of a multi-category counterpart (spec 24): the generic
    category of its direction, to see one by one. A choice made by hand
    stays. True if changed."""
    if tx.category_source == "manual" or tx.human_validated:
        return False
    cat, sub = fallbacks["expense"] if float(tx.amount or 0) < 0 else fallbacks["income"]
    changed = (tx.category, tx.subcategory, tx.category_source) != (cat, sub, "multi")
    tx.category, tx.subcategory = cat, sub
    tx.category_source, tx.category_confidence, tx.to_review = "multi", "low", True
    return changed


def _make_giroconto(tx: Transaction) -> bool:
    """Turn a movement into a giroconto, out or in by its sign. True if changed."""
    if tx.tx_type in ("internal_out", "internal_in"):
        return False
    tx.tx_type = "internal_in" if float(tx.amount or 0) >= 0 else "internal_out"
    return True


def apply_rules_to_review_transactions(
    session: Session,
    user_rules: list[CoreCategoryRule],
) -> int:
    """Apply category rules (highest priority first) to all to_review=True transactions.

    For each transaction, the first matching rule wins.
    Matched transactions get their category/subcategory updated and to_review cleared.

    Returns the number of transactions updated.
    """
    if not user_rules:
        return 0

    txs = session.query(Transaction).filter(Transaction.to_review.is_(True)).all()
    if not txs:
        return 0

    rules = _rules_in_order(user_rules)
    fallbacks = get_fallback_categories(session)
    updated = 0
    for tx in txs:
        desc = tx.description or ""
        for rule in rules:
            if rule.matches(desc, tx.doc_type, tx.amount):
                if rule.not_run:
                    break  # decided, never run: no other rule takes it either
                if rule.is_multi_category:
                    updated += _make_multi_category(tx, fallbacks)
                    break
                if rule.is_giroconto:
                    _make_giroconto(tx)
                    tx.to_review = False
                    updated += 1
                    break
                if tx.tx_type in INTERNAL_TX_TYPES or tx.human_validated:
                    break  # a giroconto has no category; a checked one keeps its own
                tx.category = rule.category
                tx.subcategory = rule.subcategory
                if rule.context:
                    tx.context = rule.context
                tx.category_source = "rule"
                tx.category_model = None
                tx.category_confidence = "high"
                tx.to_review = False
                # human_validated NOT reset: it says the money was spent, not how
                updated += 1
                break
    if updated:
        session.flush()
    return updated


def apply_all_rules_to_all_transactions(
    session: Session,
    user_rules: list[CoreCategoryRule],
) -> tuple[int, int]:
    """Apply category rules to ALL transactions (not just to_review).

    Rules are evaluated in descending priority order; first match wins.
    Transactions with no matching rule are left unchanged, and so is a
    category chosen by hand: it is a one-off exception to the rule.

    Returns (n_matched, n_cleared_review) where:
      - n_matched        = transactions whose category was set/changed by a rule
      - n_cleared_review = subset of those that also had to_review cleared
    """
    if not user_rules:
        return 0, 0

    rules = _rules_in_order(user_rules)
    fallbacks = get_fallback_categories(session)
    txs = session.query(Transaction).all()

    n_matched = 0
    n_cleared = 0
    for tx in txs:
        desc = tx.description or ""
        for rule in rules:
            if rule.matches(desc, tx.doc_type, tx.amount):
                if rule.not_run:
                    break  # decided, never run: no other rule takes it either
                if rule.is_multi_category:
                    n_matched += _make_multi_category(tx, fallbacks)
                    break
                if rule.is_giroconto:
                    if _make_giroconto(tx):
                        n_matched += 1
                    if tx.to_review:
                        tx.to_review = False
                        n_cleared += 1
                    break
                if (tx.category_source == "manual" or tx.human_validated
                        or tx.tx_type in INTERNAL_TX_TYPES):
                    break  # the user's choice or check stays; a giroconto has no category
                tx.category            = rule.category
                tx.subcategory         = rule.subcategory
                if rule.context:
                    tx.context = rule.context
                tx.category_source     = "rule"
                tx.category_model      = None
                tx.category_confidence = "high"
                # human_validated NOT reset: it says the money was spent, not how
                if tx.to_review:
                    tx.to_review = False
                    n_cleared += 1
                n_matched += 1
                break

    if n_matched:
        session.flush()
    return n_matched, n_cleared


def category_direction(session: Session, category: Optional[str]) -> Optional[str]:
    """"out" for a spending category, "in" for an income one (spec 18)."""
    from db.models import TaxonomyCategory
    if not category:
        return None
    types = {t for (t,) in session.query(TaxonomyCategory.type).filter(TaxonomyCategory.name == category)}
    if types == {"expense"}:
        return "out"
    if types == {"income"}:
        return "in"
    return None


def pattern_directions(session: Session, pattern: str, match_type: str) -> set[str]:
    """The directions of the movements a pattern covers: "out", "in" or both."""
    return {"out" if float(tx.amount or 0) < 0 else "in"
            for tx in get_transactions_by_rule_pattern(session, pattern, match_type)}


def create_category_rule(
    session: Session,
    pattern: str,
    match_type: str,
    category: str,
    subcategory: Optional[str] = None,
    context: Optional[str] = None,
    doc_type: Optional[str] = None,
    priority: int = 0,
    is_giroconto: bool = False,
    is_multi_category: bool = False,
    direction: Optional[str] = None,
    not_show: bool = False,
    not_run: bool = False,
) -> tuple[CategoryRule, bool]:
    """Create or update a category rule.

    Every rule has a direction, "out" or "in", and holds only for the
    movements in it (spec 18, founder 2026-10-21). Without *direction*: the
    category's type tells it; for a giroconto or multi-category rule, the
    movements the pattern covers, and when they go both ways one rule per
    direction is written (two visible decisions, never one for both).

    If a rule with the same pattern + match_type + direction already exists
    it is updated in place. Returns (rule, created): the first rule written.
    """
    if direction is None:
        direction = category_direction(session, category) if category else None
    if direction is None:
        found = pattern_directions(session, pattern, match_type)
        if len(found) > 1:
            first = None
            for d in sorted(found, reverse=True):  # "out" first
                r = create_category_rule(session, pattern, match_type, category, subcategory,
                                         context, doc_type, priority, is_giroconto,
                                         is_multi_category, direction=d,
                                         not_show=not_show, not_run=not_run)
                first = first or r
            return first
        direction = found.pop() if found else "out"
    # Pattern is stored verbatim (matching is case-insensitive at compare time:
    # see categorizer.matches / get_transactions_by_rule_pattern). The upsert
    # lookup is case-insensitive for contains/exact so "coop"/"COOP" dedup to one.
    from sqlalchemy import func

    if match_type in ("contains", "exact"):
        same_pattern = func.upper(CategoryRule.pattern) == pattern.upper()
    else:
        same_pattern = CategoryRule.pattern == pattern
    existing = (
        session.query(CategoryRule)
        .filter(same_pattern, CategoryRule.match_type == match_type,
                CategoryRule.direction == direction)
        .first()
    )
    if existing is not None:
        existing.category = category
        existing.subcategory = subcategory
        existing.context = context or None
        existing.priority = priority
        existing.is_giroconto = is_giroconto
        existing.is_multi_category = is_multi_category
        # Writing a rule the user can see makes an accepted counterpart a real
        # rule; accepting never hides a rule the user already has. A
        # multi-category rule is never run, whoever writes it (spec 24).
        existing.not_show = bool(not_show and existing.not_show)
        existing.not_run = bool(is_multi_category or (not_run and existing.not_run))
        session.flush()
        return existing, False

    rule = CategoryRule(
        pattern=pattern,
        match_type=match_type,
        category=category,
        subcategory=subcategory,
        context=context or None,
        doc_type=doc_type,
        priority=priority,
        is_giroconto=is_giroconto,
        is_multi_category=is_multi_category,
        direction=direction,
        not_show=not_show,
        not_run=bool(not_run or is_multi_category),
    )
    session.add(rule)
    session.flush()
    return rule, True


def update_category_rule(
    session: Session,
    rule_id: int,
    pattern: Optional[str] = None,
    match_type: Optional[str] = None,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    context: Optional[str] = _SENTINEL,   # type: ignore[assignment]
    priority: Optional[int] = None,
    is_giroconto: Optional[bool] = None,
    is_multi_category: Optional[bool] = None,
) -> bool:
    rule = session.get(CategoryRule, rule_id)
    if rule is None:
        return False
    if pattern is not None:
        # Stored verbatim; rule matching is case-insensitive at compare time.
        rule.pattern = pattern
    if match_type is not None:
        rule.match_type = match_type
    if category is not None:
        rule.category = category
    if subcategory is not None:
        rule.subcategory = subcategory
    if context is not _SENTINEL:
        # None means "clear context"; any string sets it
        rule.context = context or None
    if priority is not None:
        rule.priority = priority
    if is_giroconto is not None:
        rule.is_giroconto = is_giroconto
    if is_multi_category is not None:
        rule.is_multi_category = is_multi_category
        rule.not_run = bool(is_multi_category or rule.not_show)
    session.flush()
    return True


def delete_category_rule(session: Session, rule_id: int) -> bool:
    rule = session.get(CategoryRule, rule_id)
    if rule is None:
        return False
    session.delete(rule)
    session.flush()
    return True


def get_transactions_by_rule_pattern(
    session: Session,
    pattern: str,
    match_type: str,
    direction: Optional[str] = None,
) -> list[Transaction]:
    """Return transactions whose description matches the rule pattern, in
    the rule's *direction* ("out", "in") when given (spec 18)."""
    import re
    q = session.query(Transaction)
    if direction == "out":
        q = q.filter(Transaction.amount < 0)
    elif direction == "in":
        q = q.filter(Transaction.amount >= 0)
    txs = q.all()
    result = []
    pat_lower = pattern.lower()
    for tx in txs:
        desc = (tx.description or "").lower()
        if match_type == "exact" and same_text(tx.description, pattern):
            result.append(tx)
        elif match_type == "contains" and pat_lower in desc:
            result.append(tx)
        elif match_type == STARTS_WITH and starts_with(tx.description, pattern):
            result.append(tx)
        elif match_type == "regex":
            try:
                if re.search(pattern, tx.description or "", re.IGNORECASE):
                    result.append(tx)
            except re.error:
                pass
    return result


def set_giroconto_by_rule_pattern(
    session: Session, pattern: str, match_type: str, make_giroconto: bool,
    direction: Optional[str] = None,
) -> int:
    """Mark (or unmark) as giroconto every movement the rule pattern matches.

    Unmarking gives back income or expense by sign. Returns how many changed.
    """
    changed = 0
    for tx in get_transactions_by_rule_pattern(session, pattern, match_type, direction):
        if make_giroconto:
            changed += _make_giroconto(tx)
        elif tx.tx_type in ("internal_out", "internal_in"):
            leave_giroconto(session, tx)
            changed += 1
    if changed:
        session.flush()
    return changed


def set_multi_category(session: Session, description: str, multi: bool) -> int:
    """Mark a counterpart as a place where everything is bought, or not (spec 24).

    On: an exact rule without category, applied to the movements already in
    the ledger (generic category, to see one by one; choices made by hand
    stay). Off: the rule goes, and the counterpart is one to decide again.
    Returns how many movements changed.
    """
    rule = (
        session.query(CategoryRule)
        .filter(CategoryRule.is_multi_category.is_(True), CategoryRule.match_type == "exact")
        .all()
    )
    existing = next((r for r in rule if same_text(r.pattern, description)), None)
    if not multi:
        if existing is not None:
            session.delete(existing)
            session.flush()
        return 0
    if existing is None:
        # One rule per direction the counterpart has (spec 18).
        create_category_rule(session, description, "exact", "", "", priority=0,
                             is_multi_category=True)
    # Applied once, when the user decides; the rule is never run after
    # (spec 24 par. 6). Choices by hand and checked movements stay.
    fallbacks = get_fallback_categories(session)
    n = sum(_make_multi_category(tx, fallbacks) for tx in session.query(Transaction).filter(
        same_counterpart(Transaction.description, description)))
    session.flush()
    return n


def delete_giroconto_rule(session: Session, pattern: str, match_type: str) -> bool:
    """Delete the giroconto rules with this pattern, in both directions
    (unmarking a counterpart undoes both). True if any was deleted."""
    from sqlalchemy import func
    rules = (
        session.query(CategoryRule)
        .filter(
            func.upper(CategoryRule.pattern) == pattern.upper(),
            CategoryRule.match_type == match_type,
            CategoryRule.is_giroconto.is_(True),
        )
        .all()
    )
    for rule in rules:
        session.delete(rule)
    session.flush()
    return bool(rules)


# ── Persistence of ImportResult ───────────────────────────────────────────────

def persist_import_result(session: Session, result) -> None:
    """Persist a complete ImportResult to the database."""
    from core.orchestrator import ImportResult

    if result.skipped_duplicate:
        logger.info(f"persist_import_result: skipping duplicate {result.source_name}")
        return

    # Derive account_label from the first transaction (if any)
    _account_label = None
    if result.transactions:
        _account_label = result.transactions[0].get("account_label")

    batch = create_import_batch(
        session=session,
        sha256=result.batch_sha256,
        filename=result.source_name,
        flow_used=result.flow_used,
        n_transactions=len(result.transactions),
        errors="; ".join(result.errors) if result.errors else None,
        account_label=_account_label,
    )

    if result.doc_schema:
        upsert_document_schema(session, result.doc_schema)

    # A movement dated after today cannot be right: it is set aside in full,
    # out of the ledger, until the user fixes the date.
    today = date.today().isoformat()
    held: set[str] = set()
    tx_id_map: dict[str, Transaction] = {}
    for tx in result.transactions:
        if _iso(tx["date"]) > today:
            hold_movement(session, tx, batch_id=batch.id, reason="date_future")
            held.add(tx["id"])
            continue
        row = upsert_transaction(session, tx, batch_id=batch.id)
        tx_id_map[tx["id"]] = row
    result.held_count = len(held)

    for rec in result.reconciliations:
        if rec["settlement_id"] in held:
            continue
        for detail_id in rec["matched_ids"]:
            if detail_id in held:
                continue
            create_reconciliation_link(
                session=session,
                settlement_id=rec["settlement_id"],
                detail_id=detail_id,
                delta=float(rec.get("delta", 0)),
                method=rec.get("method", ""),
            )
            # Mark settlement and detail as reconciled
            if rec["settlement_id"] in tx_id_map:
                tx_id_map[rec["settlement_id"]].reconciled = True
            if detail_id in tx_id_map:
                tx_id_map[detail_id].reconciled = True

    for link in result.transfer_links:
        if link["out_id"] in held or link["in_id"] in held:
            continue
        create_transfer_link(
            session=session,
            out_id=link["out_id"],
            in_id=link["in_id"],
            confidence=link.get("confidence", "medium"),
            keyword_matched=bool(link.get("keyword_matched", False)),
        )

    session.commit()
    logger.info(
        f"persist_import_result: committed batch {result.batch_sha256[:8]}… "
        f"({len(result.transactions)} transactions, "
        f"{len(result.reconciliations)} reconciliations, "
        f"{len(result.transfer_links)} transfer links)"
    )


# ── ImportJob ─────────────────────────────────────────────────────────────────

def reset_stale_jobs(session: Session) -> int:
    """Mark any 'running' ImportJob as 'error' (interrupted by app restart).

    Call once at app startup so stale jobs from a previous process do not block
    the upload page or show a phantom progress bar.
    Returns the number of jobs reset.
    """
    from datetime import datetime, timezone
    stale = session.query(ImportJob).filter(ImportJob.status == "running").all()
    for job in stale:
        job.status = "error"
        job.status_message = "⚠️ Importazione interrotta (app riavviata)"
        job.completed_at = datetime.now(timezone.utc)
    session.commit()
    return len(stale)


def create_import_job(session: Session, n_files: int, n_rows: int | None = None) -> ImportJob:
    from datetime import datetime, timezone
    job = ImportJob(status="running", progress=0.0, n_files=n_files, n_rows=n_rows,
                    started_at=datetime.now(timezone.utc))
    session.add(job)
    session.flush()
    session.commit()  # commit immediately so other sessions/threads can see it
    return job


def get_import_timings(session: Session, limit: int = 10) -> list[tuple[int, float]]:
    """(movements, seconds) of the latest completed imports, newest first.

    The time goes to the movements that reach the model: rows already in the
    ledger are skipped before it and cost almost nothing. Counting the rows
    read made an import full of duplicates look fast (2026-10-20: 826 rows in
    13 minutes, 466 of them duplicates, gave 0.9 s a row against 2.2 for the
    rows actually processed). Jobs that created no movement are left out.
    """
    jobs = (session.query(ImportJob)
            .filter(ImportJob.status == "completed",
                    ImportJob.completed_at.isnot(None),
                    ImportJob.started_at.isnot(None))
            .order_by(ImportJob.id.desc())
            .limit(limit * 3)
            .all())
    out: list[tuple[int, float]] = []
    for j in jobs:
        rows = j.n_transactions or 0
        secs = (j.completed_at - j.started_at).total_seconds()
        if rows > 0 and secs > 0:
            out.append((rows, secs))
        if len(out) == limit:
            break
    return out


def update_import_job(session: Session, job_id: int, **kwargs) -> None:
    job = session.get(ImportJob, job_id)
    if job is None:
        return
    from datetime import datetime, timezone

    for k, v in kwargs.items():
        setattr(job, k, v)
    # Every update is a sign of life, whatever it changes.
    job.last_ping = datetime.now(timezone.utc)
    session.flush()
    session.commit()


def add_import_job_transactions(session: Session, job_id: int, n_new: int) -> Optional[int]:
    """Add movements imported later (after a schema review) to a finished job.

    Returns the job's new total, or None when the job no longer exists.
    """
    job = session.get(ImportJob, job_id)
    if job is None:
        return None
    job.n_transactions = (job.n_transactions or 0) + n_new
    session.flush()
    session.commit()
    return job.n_transactions


def get_latest_import_job(session: Session) -> Optional[ImportJob]:
    return session.query(ImportJob).order_by(ImportJob.id.desc()).first()


def get_last_import_period(session: Session) -> Optional[tuple[str, str]]:
    """(first, last) ISO date of the movements of the last finished import.

    The batches are the ones written while that import ran. None when there
    is no finished import or it brought no movement.
    """
    from sqlalchemy import func
    job = (
        session.query(ImportJob)
        .filter(ImportJob.status == "completed", ImportJob.completed_at.isnot(None))
        .order_by(ImportJob.id.desc())
        .first()
    )
    if job is None or job.started_at is None:
        return None
    batch_ids = [
        b.id for b in session.query(ImportBatch.id).filter(
            ImportBatch.imported_at >= job.started_at,
            ImportBatch.imported_at <= job.completed_at,
        )
    ]
    if not batch_ids:
        return None
    first, last = (
        session.query(func.min(Transaction.date), func.max(Transaction.date))
        .filter(Transaction.batch_id.in_(batch_ids))
        .one()
    )
    return (first, last) if first and last else None


# ── UserSettings ──────────────────────────────────────────────────────────────

def get_user_setting(session: Session, key: str, default: Optional[str] = None) -> Optional[str]:
    row = session.get(UserSettings, key)
    if row is None:
        return default or DEFAULT_USER_SETTINGS.get(key)
    return row.value


def set_user_setting(session: Session, key: str, value: str) -> None:
    row = session.get(UserSettings, key)
    if row is None:
        row = UserSettings(key=key, value=value)
        session.add(row)
    else:
        row.value = value
    session.flush()


def get_all_user_settings(session: Session) -> dict[str, str]:
    rows = session.query(UserSettings).all()
    result = dict(DEFAULT_USER_SETTINGS)  # start with defaults
    result.update({r.key: r.value for r in rows if r.value is not None})
    return result


# ── Taxonomy ───────────────────────────────────────────────────────────────────

def get_taxonomy_config(session: Session):
    """Build a TaxonomyConfig from the DB taxonomy tables."""
    from core.categorizer import TaxonomyConfig
    from db.models import TaxonomyCategory

    cats = (
        session.query(TaxonomyCategory)
        .order_by(TaxonomyCategory.type, TaxonomyCategory.sort_order)
        .all()
    )
    expenses: dict[str, list[str]] = {}
    income: dict[str, list[str]] = {}
    for cat in cats:
        subs = [s.name for s in sorted(cat.subcategories, key=lambda x: x.sort_order)]
        if cat.type == "expense":
            expenses[cat.name] = subs
        else:
            income[cat.name] = subs

    # Ensure fallback categories always exist
    if not expenses:
        expenses = {"Altro": ["Spese non classificate"]}
    if not income:
        income = {"Altro entrate": ["Entrate non classificate"]}
    return TaxonomyConfig(expenses=expenses, income=income)


def get_taxonomy_categories(session: Session, type_filter: Optional[str] = None):
    """Return TaxonomyCategory rows, optionally filtered by type ('expense'/'income').

    Subcategories are eagerly loaded so they remain accessible after the session closes.
    """
    from db.models import TaxonomyCategory
    from sqlalchemy.orm import joinedload
    q = session.query(TaxonomyCategory).options(joinedload(TaxonomyCategory.subcategories))
    if type_filter:
        q = q.filter_by(type=type_filter)
    return q.order_by(TaxonomyCategory.sort_order).all()


def create_taxonomy_category(session: Session, name: str, type_: str) -> "TaxonomyCategory":
    from db.models import TaxonomyCategory
    from sqlalchemy import func
    max_order = session.query(func.max(TaxonomyCategory.sort_order)).filter_by(type=type_).scalar() or 0
    from core.palette import next_color
    # A category is born with its colour (spec 25).
    used = [c for (c,) in session.query(TaxonomyCategory.color).filter(
        TaxonomyCategory.type == type_, TaxonomyCategory.color.isnot(None),
        TaxonomyCategory.is_fallback.isnot(True))]
    cat = TaxonomyCategory(name=name.strip(), type=type_, sort_order=max_order + 1,
                           color=next_color(type_, used))
    session.add(cat)
    session.flush()
    return cat


def set_taxonomy_category_color(session: Session, cat_id: int, color: str) -> bool:
    """The user's colour for a category (spec 25): never overwritten."""
    import re
    from db.models import TaxonomyCategory
    cat = session.get(TaxonomyCategory, cat_id)
    if cat is None or not re.fullmatch(r"#[0-9a-fA-F]{6}", color or ""):
        return False
    cat.color = color.lower()
    session.flush()
    return True


def update_taxonomy_category(session: Session, cat_id: int, name: str) -> bool:
    from db.models import TaxonomyCategory
    cat = session.get(TaxonomyCategory, cat_id)
    if cat is None:
        return False
    cat.name = name.strip()
    session.flush()
    return True


def delete_taxonomy_category(session: Session, cat_id: int) -> bool:
    from db.models import TaxonomyCategory
    cat = session.get(TaxonomyCategory, cat_id)
    if cat is None:
        return False
    session.delete(cat)
    session.flush()
    return True


def create_taxonomy_subcategory(session: Session, cat_id: int, name: str) -> "TaxonomySubcategory":
    from db.models import TaxonomySubcategory
    from sqlalchemy import func
    max_order = session.query(func.max(TaxonomySubcategory.sort_order)).filter_by(category_id=cat_id).scalar() or 0
    sub = TaxonomySubcategory(category_id=cat_id, name=name.strip(), sort_order=max_order + 1)
    session.add(sub)
    session.flush()
    return sub


def update_taxonomy_subcategory(session: Session, sub_id: int, name: str) -> bool:
    from db.models import TaxonomySubcategory
    sub = session.get(TaxonomySubcategory, sub_id)
    if sub is None:
        return False
    sub.name = name.strip()
    session.flush()
    return True


def delete_taxonomy_subcategory(session: Session, sub_id: int) -> bool:
    from db.models import TaxonomySubcategory
    sub = session.get(TaxonomySubcategory, sub_id)
    if sub is None:
        return False
    session.delete(sub)
    session.flush()
    return True


# ── Classification tracking ──────────────────────────────────────────────────

def validate_transaction(session: Session, tx_id: str) -> bool:
    """Mark a transaction as human-validated and clear to_review flag."""
    from datetime import datetime, timezone
    tx = session.get(Transaction, tx_id)
    if tx is None:
        return False
    tx.human_validated = True
    tx.validated_at = datetime.now(timezone.utc)
    tx.to_review = False
    session.flush()
    return True


def unvalidate_transaction(session: Session, tx_id: str) -> bool:
    """Remove human-validated flag from a transaction."""
    tx = session.get(Transaction, tx_id)
    if tx is None:
        return False
    tx.human_validated = False
    tx.validated_at = None
    session.flush()
    return True


def get_fallback_categories(session: Session) -> dict[str, tuple[str, str]]:
    """Return fallback category names for expense and income, read from taxonomy.

    Returns dict like {"expense": ("Altro", "Spese non classificate"), "income": ("Altro entrate", "Entrate non classificate")}

    The subcategory is the last of the fallback category: the generic one
    closes the list in every language ("Vendita beni usati", "Regalo
    ricevuto", "Entrate non classificate"); the first would file every
    unclassified income as a sale of used goods.
    """
    from db.models import TaxonomyCategory, TaxonomySubcategory
    fallbacks: dict[str, tuple[str, str]] = {}
    for cat in session.query(TaxonomyCategory).filter(TaxonomyCategory.is_fallback == True).all():  # noqa: E712
        subs = (session.query(TaxonomySubcategory)
                .filter(TaxonomySubcategory.category_id == cat.id)
                .order_by(TaxonomySubcategory.sort_order.desc(), TaxonomySubcategory.id.desc())
                .first())
        sub_name = subs.name if subs else ""
        fallbacks[cat.type] = (cat.name, sub_name)
    # Hardcoded fallback if DB has no fallback categories (fresh install before seed)
    if "expense" not in fallbacks:
        fallbacks["expense"] = ("Altro", "Spese non classificate")
    if "income" not in fallbacks:
        fallbacks["income"] = ("Altro entrate", "Entrate non classificate")
    return fallbacks


# ── NsiTagMapping CRUD (C-08-cascade) ────────────────────────────────────────

def get_nsi_tag_mapping(session: Session) -> dict[str, tuple[str, str]]:
    """Load the full OSM tag → (category, subcategory) mapping from DB."""
    rows = session.query(NsiTagMapping).all()
    return {row.osm_tag: (row.category, row.subcategory) for row in rows}


def get_nsi_tag_mapping_hash(session: Session) -> Optional[str]:
    """Return the taxonomy_hash stored in nsi_tag_mapping, or None if table is empty."""
    row = session.query(NsiTagMapping).first()
    return row.taxonomy_hash if row else None


def upsert_nsi_tag_mapping_bulk(session: Session, rows: list[dict]) -> None:
    """Bulk INSERT OR REPLACE into nsi_tag_mapping.

    Each dict must have keys: osm_tag, category, subcategory, taxonomy_hash, updated_at.
    """
    from sqlalchemy import text as _text
    from sqlalchemy.orm import Session as _Session
    # Use raw SQL for bulk efficiency
    for row in rows:
        session.execute(_text(
            "INSERT OR REPLACE INTO nsi_tag_mapping "
            "(osm_tag, category, subcategory, taxonomy_hash, updated_at) "
            "VALUES (:osm_tag, :category, :subcategory, :taxonomy_hash, :updated_at)"
        ), row)
    session.flush()


def clear_nsi_tag_mapping(session: Session) -> int:
    """Delete all rows from nsi_tag_mapping. Returns the number of rows deleted."""
    count = session.query(NsiTagMapping).delete()
    session.flush()
    return count


# ── Account CRUD ──────────────────────────────────────────────────────────────

def get_accounts(session: Session) -> list:
    from db.models import Account
    return session.query(Account).order_by(Account.name).all()


def create_account(
    session: Session, name: str, bank_name: str = "", account_type: str | None = None,
    usage: str | None = None,
) -> object:
    from db.models import Account
    from sqlalchemy.exc import IntegrityError
    acc = Account(
        name=name.strip(),
        bank_name=bank_name.strip() or None,
        account_type=account_type,
        usage=usage,
    )
    session.add(acc)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ValueError(f"Conto '{name}' già esistente")
    return acc


def set_account_closed(session: Session, name: str, closed_on: str | None) -> object:
    """Close the account ``name`` on ``closed_on`` (ISO date), or reopen it with None.

    An account seen only in the movements has no row yet: closing it creates
    the row, so the Check List knows about it.
    """
    from db.models import Account
    acc = session.query(Account).filter(Account.name == name).one_or_none()
    if acc is None:
        if closed_on is None:
            return None
        acc = Account(name=name.strip())
        session.add(acc)
    acc.closed_on = closed_on
    session.flush()
    return acc


def get_account_usage(session: Session, name: str | None) -> str | None:
    """How the person uses the account called ``name``, or None if never said."""
    from db.models import Account
    if not name or not name.strip():
        return None
    acc = session.query(Account).filter(Account.name == name.strip()).one_or_none()
    return getattr(acc, "usage", None) if acc is not None else None


def set_account_usage(session: Session, name: str, usage: str | None) -> object:
    """Record how the account ``name`` is used. Like closing, it creates the row
    for an account seen only in the movements."""
    from db.models import Account
    acc = session.query(Account).filter(Account.name == name).one_or_none()
    if acc is None:
        acc = Account(name=name.strip())
        session.add(acc)
    acc.usage = usage
    session.flush()
    return acc


def delete_account(session: Session, account_id: int) -> bool:
    from db.models import Account
    acc = session.get(Account, account_id)
    if acc is None:
        return False
    session.delete(acc)
    session.flush()
    return True


def rename_account(
    session: Session,
    account_id: int,
    new_name: str,
    new_bank_name: str | None = None,
    new_account_type: str | None = _SENTINEL,
    new_usage: str | None = _SENTINEL,
) -> int:
    """Rename an account, recalculate all transaction IDs, and cascade to related tables.

    When the account_label changes, every transaction ID must be recomputed because
    the ID hash includes the account_label.  All FK references in reconciliation_link
    and internal_transfer_link are updated atomically.

    Returns the number of transactions updated, or -1 if account not found.
    Raises ValueError if the new name collides with an existing account.
    """
    from db.models import Account, InternalTransferLink, ReconciliationLink, Transaction
    from sqlalchemy.exc import IntegrityError
    from core.normalizer import compute_transaction_id

    acc = session.get(Account, account_id)
    if acc is None:
        return -1
    old_name = acc.name
    stripped_new = new_name.strip()
    name_changed = old_name != stripped_new
    if (not name_changed and new_bank_name is None and new_account_type is _SENTINEL
            and new_usage is _SENTINEL):
        return 0  # nothing to do
    acc.name = stripped_new
    if new_bank_name is not None:
        acc.bank_name = new_bank_name.strip() or None
    if new_account_type is not _SENTINEL:
        acc.account_type = new_account_type
    if new_usage is not _SENTINEL:
        acc.usage = new_usage
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ValueError(f"Conto '{new_name}' già esistente")

    if not name_changed:
        # Only bank_name changed — no tx_id recalculation needed
        session.flush()
        return 0

    # Fetch all transactions for the old account_label
    txs = (
        session.query(Transaction)
        .filter(Transaction.account_label == old_name)
        .all()
    )
    if not txs:
        # Update document schemas even if no transactions exist
        _update_schemas_account_label(session, old_name, acc.name)
        session.flush()
        return 0

    # Snapshot the fields needed for id recomputation before any mutations
    tx_data = []
    for tx in txs:
        tx_data.append({
            "old_id": tx.id,
            "source_file": tx.source_file or "",
            "date": tx.date.isoformat() if hasattr(tx.date, 'isoformat') else str(tx.date or ""),
            "amount_key": str(Decimal(str(tx.amount or 0)).normalize()),
            "desc_key": (tx.raw_description or tx.description or "").strip(),
        })

    # Build old_id → new_id mapping
    id_mapping: dict[str, str] = {}
    for td in tx_data:
        new_id = compute_transaction_id(
            td["source_file"], td["date"], td["amount_key"], td["desc_key"],
            account_label=acc.name,
        )
        id_mapping[td["old_id"]] = new_id

    # Evict all affected Transaction objects from the identity map so raw SQL
    # updates don't conflict with stale ORM state.
    for tx in txs:
        session.expunge(tx)

    # Update FK references in related tables BEFORE changing PKs
    from sqlalchemy import text as _text
    for old_id, new_id in id_mapping.items():
        if old_id == new_id:
            continue
        session.execute(
            _text('UPDATE reconciliation_link SET settlement_id = :new WHERE settlement_id = :old'),
            {"new": new_id, "old": old_id},
        )
        session.execute(
            _text('UPDATE reconciliation_link SET detail_id = :new WHERE detail_id = :old'),
            {"new": new_id, "old": old_id},
        )
        session.execute(
            _text('UPDATE internal_transfer_link SET out_id = :new WHERE out_id = :old'),
            {"new": new_id, "old": old_id},
        )
        session.execute(
            _text('UPDATE internal_transfer_link SET in_id = :new WHERE in_id = :old'),
            {"new": new_id, "old": old_id},
        )

    # Update transaction PKs and account_label using raw SQL (can't update PK via ORM)
    for old_id, new_id in id_mapping.items():
        session.execute(
            _text('UPDATE "transaction" SET id = :new_id, account_label = :label WHERE id = :old_id'),
            {"new_id": new_id, "old_id": old_id, "label": acc.name},
        )

    # Update document schemas that reference the old account_label
    _update_schemas_account_label(session, old_name, acc.name)

    session.flush()
    return len(tx_data)


def _update_schemas_account_label(session: Session, old_label: str, new_label: str) -> int:
    """Update all DocumentSchemaModel rows that reference the old account_label."""
    return (
        session.query(DocumentSchemaModel)
        .filter(DocumentSchemaModel.account_label == old_label)
        .update({DocumentSchemaModel.account_label: new_label})
    )


# ── DescriptionRule ───────────────────────────────────────────────────────────

OWN_NAME = "signature"  # match_type of a user's own name (spec 27)


def get_description_rules(session: Session) -> list[DescriptionRule]:
    """The Review page's bulk replacements; the own names have their list."""
    return (session.query(DescriptionRule)
            .filter(DescriptionRule.match_type != OWN_NAME)
            .order_by(DescriptionRule.id).all())


def _same_key_movements(session: Session, key: tuple[str, str]) -> list[Transaction]:
    """The saved movements with this own-name key: same raw signature and
    either the same amount to the cent (key[1] an amount) or the same sign
    (key[1] "out" or "in", spec 27: an installment whose amount changes)."""
    from decimal import Decimal
    from core.normalizer import own_name_direction_key, own_name_key
    if key[1] in ("out", "in"):
        q = session.query(Transaction).filter(
            Transaction.amount < 0 if key[1] == "out" else Transaction.amount >= 0)
        return [tx for tx in q if own_name_direction_key(tx.raw_description, tx.amount) == key]
    value = Decimal(key[1])
    near = session.query(Transaction).filter(
        Transaction.amount >= value - Decimal("0.005"),
        Transaction.amount <= value + Decimal("0.005"),
    ).all()
    return [tx for tx in near if own_name_key(tx.raw_description, tx.amount) == key]


def _own_name_rule_key(r: DescriptionRule) -> tuple[str, str]:
    from decimal import Decimal
    if r.amount is None:
        return (r.raw_pattern, r.direction or "out")
    return (r.raw_pattern, str(Decimal(str(r.amount)).quantize(Decimal("0.01"))))


def own_names(session: Session) -> dict[tuple[str, str], str]:
    """{(raw signature, signed amount or "out"/"in"): own name} for the
    import (spec 27)."""
    return {_own_name_rule_key(r): r.cleaned_description
            for r in session.query(DescriptionRule).filter(DescriptionRule.match_type == OWN_NAME)}


def same_movements(session: Session, tx_id: str) -> list[str]:
    """Ids of the other saved movements equal to this one for an own name:
    same bank text without the words holding digits, same amount and sign."""
    from core.normalizer import own_name_key
    tx = session.get(Transaction, tx_id)
    key = own_name_key(tx.raw_description, tx.amount) if tx else None
    if key is None:
        return []
    return [t.id for t in _same_key_movements(session, key) if t.id != tx_id]


def same_text_other_amounts(session: Session, tx_id: str) -> list[Transaction]:
    """The saved movements with the same bank text and sign as this one but
    another amount: an installment that changes (spec 27)."""
    from core.normalizer import own_name_direction_key, own_name_key
    tx = session.get(Transaction, tx_id)
    key = own_name_direction_key(tx.raw_description, tx.amount) if tx else None
    if key is None:
        return []
    exact = own_name_key(tx.raw_description, tx.amount)
    return [t for t in _same_key_movements(session, key)
            if t.id != tx_id and own_name_key(t.raw_description, t.amount) != exact]


def create_own_name(session: Session, tx_id: str, name: str,
                    any_amount: bool = False) -> tuple[Optional[DescriptionRule], list[str]]:
    """The user's own name for the movements like this one, past and future
    (spec 27): a rule (raw signature, signed amount) -> name, or with
    *any_amount* (raw signature, sign) -> name for an installment that
    changes; every saved movement with that key renamed. The bank's text is
    never touched. Returns (rule, ids renamed); (None, []) without a key."""
    from decimal import Decimal
    from core.normalizer import own_name_direction_key, own_name_key
    tx = session.get(Transaction, tx_id)
    if tx is None or not (name or "").strip():
        return None, []
    key = (own_name_direction_key if any_amount else own_name_key)(tx.raw_description, tx.amount)
    if key is None:
        return None, []
    q = session.query(DescriptionRule).filter(
        DescriptionRule.match_type == OWN_NAME, DescriptionRule.raw_pattern == key[0])
    if any_amount:
        q = q.filter(DescriptionRule.amount.is_(None), DescriptionRule.direction == key[1])
        fields = {"amount": None, "direction": key[1]}
    else:
        q = q.filter(DescriptionRule.amount == Decimal(key[1]))
        fields = {"amount": Decimal(key[1]), "direction": None}
    rule = q.first()
    if rule is None:
        rule = DescriptionRule(raw_pattern=key[0], match_type=OWN_NAME,
                               cleaned_description=name, **fields)
        session.add(rule)
    renamed = []
    for t in _same_key_movements(session, key):
        t.description = name
        renamed.append(t.id)
    rule.cleaned_description = session.get(Transaction, tx_id).description or name.strip()
    session.flush()
    return rule, renamed


def spread_own_name_category(session: Session, tx_id: str, renamed: list[str],
                             category_rule: Optional[bool] = None) -> int:
    """The movements just given an own name take the category of the one
    the user renamed (spec 27). When that category is the user's (a choice
    by hand, a rule, a checked movement) it becomes a category rule on the
    name, so the next imports get it too; otherwise it is copied as it is.
    The user's own choices and checked movements keep theirs. Returns how
    many changed. A giroconto spreads the same way (founder, 2026-10-21): the
    renamed movements become giroconti too, and a giroconto rule on the name
    in the movement's direction does it at the next imports."""
    ref = session.get(Transaction, tx_id)
    if ref is None:
        return 0
    others = [i for i in renamed if i != tx_id]
    if ref.tx_type in INTERNAL_TX_TYPES:
        create_category_rule(session, pattern=ref.description, match_type="exact",
                             category="", subcategory="", priority=10, is_giroconto=True,
                             direction="out" if float(ref.amount or 0) < 0 else "in")
        return apply_rules_to_transactions(session, others, get_category_rules(session))
    if not ref.category:
        return 0
    # *category_rule*: the user's answer to "fix the category for this name
    # too"; without an answer, a rule only when the category is already the
    # user's.
    decided = ref.category_source in USER_CATEGORY_SOURCES or bool(ref.human_validated)
    if category_rule if category_rule is not None else decided:
        create_category_rule(session, pattern=ref.description, match_type="exact",
                             category=ref.category, subcategory=ref.subcategory or "",
                             priority=10)
        return apply_rules_to_transactions(
            session, [i for i in renamed if i != tx_id], get_category_rules(session))
    n = 0
    for tx in session.query(Transaction).filter(Transaction.id.in_(renamed)):
        if tx.id == tx_id or tx.category_source == "manual" or tx.human_validated:
            continue
        if tx.tx_type in INTERNAL_TX_TYPES:
            continue
        tx.category, tx.subcategory = ref.category, ref.subcategory
        tx.category_source = ref.category_source
        n += 1
    session.flush()
    return n


def get_own_names(session: Session) -> list[dict]:
    """The own names for the Rules page, with an example of the bank's text
    and how many movements carry them. amount is None for a changing one."""
    out = []
    for r in (session.query(DescriptionRule).filter(DescriptionRule.match_type == OWN_NAME)
              .order_by(DescriptionRule.cleaned_description)):
        movements = _same_key_movements(session, _own_name_rule_key(r))
        out.append({
            "id": r.id, "name": r.cleaned_description, "signature": r.raw_pattern,
            "amount": float(r.amount) if r.amount is not None else None,
            "direction": r.direction, "n": len(movements),
            "example": movements[0].raw_description if movements else "",
        })
    return out


def create_description_rule(
    session: Session,
    raw_pattern: str,
    match_type: str,
    cleaned_description: str,
) -> tuple[DescriptionRule, bool]:
    """Upsert a description rule on (raw_pattern, match_type).

    Returns (rule, created) where created=False means an existing rule was updated.
    """
    existing = (
        session.query(DescriptionRule)
        .filter(
            DescriptionRule.raw_pattern == raw_pattern,
            DescriptionRule.match_type == match_type,
        )
        .first()
    )
    if existing is not None:
        existing.cleaned_description = cleaned_description
        session.flush()
        return existing, False

    rule = DescriptionRule(
        raw_pattern=raw_pattern,
        match_type=match_type,
        cleaned_description=cleaned_description,
    )
    session.add(rule)
    session.flush()
    return rule, True


def delete_description_rule(session: Session, rule_id: int) -> bool:
    rule = session.get(DescriptionRule, rule_id)
    if rule is None:
        return False
    session.delete(rule)
    session.flush()
    return True


def delete_transactions_by_filter(
    session: Session,
    filters: dict,
) -> int:
    """Delete transactions matching *filters* and return the count of deleted rows.

    Uses the same filter keys as ``get_transactions()``.
    Cascades: also removes linked ReconciliationLink and InternalTransferLink rows
    to avoid dangling foreign-key references.
    """
    from sqlalchemy import or_

    q = session.query(Transaction)
    if "tx_type" in filters:
        q = q.filter(Transaction.tx_type == filters["tx_type"])
    if "category" in filters:
        q = q.filter(Transaction.category == filters["category"])
    if "date_from" in filters:
        q = q.filter(Transaction.date >= filters["date_from"])
    if "date_to" in filters:
        q = q.filter(Transaction.date <= filters["date_to"])
    if "account_label" in filters:
        q = q.filter(Transaction.account_label == filters["account_label"])
    if "description" in filters:
        _term = f"%{filters['description']}%"
        q = q.filter(
            or_(
                Transaction.description.ilike(_term),
                Transaction.raw_description.ilike(_term),
            )
        )
    if "subcategory" in filters:
        q = q.filter(Transaction.subcategory == filters["subcategory"])
    if "context" in filters:
        q = q.filter(Transaction.context == filters["context"])
    if "exclude_tx_types" in filters:
        q = q.filter(Transaction.tx_type.notin_(filters["exclude_tx_types"]))

    txs = q.all()
    if not txs:
        return 0

    ids = [tx.id for tx in txs]

    # Remove cascade links first (avoid FK violations)
    session.query(ReconciliationLink).filter(
        (ReconciliationLink.settlement_id.in_(ids)) |
        (ReconciliationLink.detail_id.in_(ids))
    ).delete(synchronize_session=False)
    session.query(InternalTransferLink).filter(
        (InternalTransferLink.out_id.in_(ids)) |
        (InternalTransferLink.in_id.in_(ids))
    ).delete(synchronize_session=False)

    for tx in txs:
        session.delete(tx)

    return len(txs)


def get_cross_account_duplicates(session: Session) -> list[list[Transaction]]:
    """Return groups of transactions that share (date, raw_description, amount)
    but belong to different account_labels.

    Each element of the returned list is a group of ≥ 2 transactions that are
    likely the same real-world movement imported from multiple bank exports.
    Groups are sorted by date descending.
    """
    from sqlalchemy import func, tuple_

    # Find (date, raw_description, amount) keys that appear in > 1 account
    subq = (
        session.query(
            Transaction.date,
            Transaction.raw_description,
            Transaction.amount,
        )
        .filter(Transaction.raw_description.isnot(None))
        .group_by(Transaction.date, Transaction.raw_description, Transaction.amount)
        .having(func.count(Transaction.account_label.distinct()) > 1)
        .subquery()
    )

    # Fetch all transactions matching those keys
    duplicates = (
        session.query(Transaction)
        .filter(
            tuple_(Transaction.date, Transaction.raw_description, Transaction.amount)
            .in_(session.query(subq.c.date, subq.c.raw_description, subq.c.amount))
        )
        .order_by(Transaction.date.desc(), Transaction.raw_description, Transaction.amount)
        .all()
    )

    # Group by key
    from collections import defaultdict
    groups: dict[tuple, list[Transaction]] = defaultdict(list)
    for tx in duplicates:
        groups[(tx.date, tx.raw_description, str(tx.amount))].append(tx)

    return [g for g in groups.values() if len(g) >= 2]


def get_transactions_by_raw_pattern(
    session: Session,
    raw_pattern: str,
    match_type: str,
) -> list[Transaction]:
    """Return transactions whose raw_description matches the given pattern."""
    import re
    txs = session.query(Transaction).filter(
        Transaction.raw_description.isnot(None),
    ).all()
    result = []
    pat_lower = raw_pattern.lower()
    for tx in txs:
        raw = (tx.raw_description or "").lower()
        if match_type == "exact" and raw == pat_lower:
            result.append(tx)
        elif match_type == "contains" and pat_lower in raw:
            result.append(tx)
        elif match_type == "regex":
            try:
                if re.search(raw_pattern, tx.raw_description or "", re.IGNORECASE):
                    result.append(tx)
            except re.error:
                pass
    return result


# ── TaxonomyDefault ───────────────────────────────────────────────────────────

def get_default_taxonomy_languages(session: Session) -> list[str]:
    """Return the list of language codes present in taxonomy_default, in insertion order."""
    from sqlalchemy import text as _text
    rows = session.execute(
        _text("SELECT language FROM taxonomy_default GROUP BY language ORDER BY MIN(id)")
    ).fetchall()
    return [r[0] for r in rows]


def apply_taxonomy_overrides(
    session: Session,
    renames: dict[str, str],
    deletions: list[str],
    sub_renames: dict[tuple[str, str], str] | None = None,
    sub_deletions: list[tuple[str, str]] | None = None,
) -> None:
    """Apply onboarding-wizard taxonomy overrides on top of a seeded taxonomy.

    Category-level:
      * ``renames`` maps original category name → new name.
      * ``deletions`` lists original category names to drop (subcategories
        cascade via the FK).

    Subcategory-level:
      * ``sub_renames`` maps ``(parent_original, sub_original)`` → new sub name.
      * ``sub_deletions`` lists ``(parent_original, sub_original)`` to drop.

    Order: category deletions first (cascades to subcategories of the deleted
    parents), category renames second, then subcategory deletions, then
    subcategory renames. This keeps the lookups deterministic — subcategory
    edits scope themselves by the ORIGINAL parent name even after a parent
    rename has been applied.
    """
    from db.models import TaxonomyCategory, TaxonomySubcategory

    # Resolve original parent name → current TaxonomyCategory rows BEFORE any
    # change, so subcategory edits below can still target by original parent
    # even after category renames.
    parent_by_original = {
        row.name: row
        for row in session.query(TaxonomyCategory).all()
    }

    # ── 1. Category deletions (deletion wins over rename on same name)
    if deletions:
        deleted = set(deletions)
        session.query(TaxonomyCategory).filter(
            TaxonomyCategory.name.in_(deleted)
        ).delete(synchronize_session=False)
        # Keep the local map in sync so we don't try to edit subcats of a
        # category that's already gone.
        for name in list(parent_by_original):
            if name in deleted:
                parent_by_original.pop(name)

    # ── 2. Category renames
    for original, new in (renames or {}).items():
        if not new or new == original:
            continue
        if original not in parent_by_original:
            continue
        session.query(TaxonomyCategory).filter(
            TaxonomyCategory.name == original
        ).update({"name": new}, synchronize_session=False)

    # ── 3. Subcategory deletions
    for parent_orig, sub_orig in (sub_deletions or []):
        cat = parent_by_original.get(parent_orig)
        if cat is None:
            continue  # parent was deleted — nothing to clean up
        session.query(TaxonomySubcategory).filter(
            TaxonomySubcategory.category_id == cat.id,
            TaxonomySubcategory.name == sub_orig,
        ).delete(synchronize_session=False)

    # ── 4. Subcategory renames
    for (parent_orig, sub_orig), sub_new in (sub_renames or {}).items():
        if not sub_new or sub_new == sub_orig:
            continue
        cat = parent_by_original.get(parent_orig)
        if cat is None:
            continue
        session.query(TaxonomySubcategory).filter(
            TaxonomySubcategory.category_id == cat.id,
            TaxonomySubcategory.name == sub_orig,
        ).update({"name": sub_new}, synchronize_session=False)

    session.commit()


def seed_user_taxonomy_from_default(session: Session, language: str) -> int:
    """Replace the user taxonomy with the built-in template for *language*.

    Clears taxonomy_category (cascades to taxonomy_subcategory) then copies
    rows from taxonomy_default.  Returns the number of categories inserted.
    """
    from sqlalchemy import text as _text
    from db.models import TaxonomyCategory, TaxonomySubcategory

    # Clear existing user taxonomy
    session.query(TaxonomySubcategory).delete(synchronize_session=False)
    session.query(TaxonomyCategory).delete(synchronize_session=False)
    session.flush()

    query = _text(
        'SELECT type, category, subcategory, sort_order_cat, sort_order_sub, is_fallback '
        'FROM taxonomy_default WHERE language = :lang AND subcategory IS NOT NULL '
        'ORDER BY sort_order_cat, sort_order_sub'
    )
    rows = session.execute(query, {"lang": language}).fetchall()

    if not rows:
        # Language not found — fall back to 'it'
        rows = session.execute(query, {"lang": "it"}).fetchall()

    cat_map: dict[tuple[str, str], TaxonomyCategory] = {}
    for row in rows:
        type_key, cat_name, sub_name, sort_cat, sort_sub, is_fallback = row
        key = (type_key, cat_name)
        if key not in cat_map:
            cat = TaxonomyCategory(name=cat_name, type=type_key, sort_order=sort_cat,
                                   is_fallback=bool(is_fallback))
            session.add(cat)
            session.flush()  # get cat.id
            cat_map[key] = cat
        sub = TaxonomySubcategory(
            category_id=cat_map[key].id,
            name=sub_name,
            sort_order=sort_sub,
        )
        session.add(sub)

    session.flush()
    from db.models import fill_category_colors
    fill_category_colors(session.connection())  # spec 25
    session.commit()
    return len(cat_map)


# ── Spending aggregation (A-01) ──────────────────────────────────────────────

def get_spending_aggregation(
    session: Session,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    account_ids: Optional[list[str]] = None,
    exclude_internal: bool = True,
) -> list[dict]:
    """GROUP BY context, category with SUM(amount).

    Returns a list of dicts with keys:
        context, category, subcategory, total_amount, tx_count
    Excludes card_settlement and aggregate_debit always.
    When *exclude_internal* is True, also excludes internal_in/internal_out.
    """
    from sqlalchemy import func

    q = session.query(
        func.coalesce(Transaction.context, "—").label("context"),
        func.coalesce(Transaction.category, "Altro").label("category"),
        func.coalesce(Transaction.subcategory, "").label("subcategory"),
        func.sum(Transaction.amount).label("total_amount"),
        func.count(Transaction.id).label("tx_count"),
    )

    # Always exclude non-real tx types
    excluded = ["card_settlement", "aggregate_debit"]
    if exclude_internal:
        excluded += ["internal_in", "internal_out"]
    q = q.filter(Transaction.tx_type.notin_(excluded))

    if date_from:
        q = q.filter(Transaction.date >= date_from)
    if date_to:
        q = q.filter(Transaction.date <= date_to)
    if account_ids:
        q = q.filter(Transaction.account_label.in_(account_ids))

    q = q.group_by(
        func.coalesce(Transaction.context, "—"),
        func.coalesce(Transaction.category, "Altro"),
        func.coalesce(Transaction.subcategory, ""),
    ).order_by(
        func.coalesce(Transaction.context, "—"),
        func.coalesce(Transaction.category, "Altro"),
    )

    rows = q.all()
    return [
        {
            "context": r.context,
            "category": r.category,
            "subcategory": r.subcategory,
            "total_amount": float(r.total_amount),
            "tx_count": r.tx_count,
        }
        for r in rows
    ]


def get_monthly_spending(
    session: Session,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    account_ids: Optional[list[str]] = None,
    exclude_internal: bool = True,
) -> list[dict]:
    """Monthly totals by category for trend charts.

    Returns list of dicts: month, category, total_amount
    """
    from sqlalchemy import func

    q = session.query(
        func.strftime("%Y-%m", Transaction.date).label("month"),
        func.coalesce(Transaction.category, "Altro").label("category"),
        func.sum(Transaction.amount).label("total_amount"),
    )

    excluded = ["card_settlement", "aggregate_debit"]
    if exclude_internal:
        excluded += ["internal_in", "internal_out"]
    q = q.filter(Transaction.tx_type.notin_(excluded))

    if date_from:
        q = q.filter(Transaction.date >= date_from)
    if date_to:
        q = q.filter(Transaction.date <= date_to)
    if account_ids:
        q = q.filter(Transaction.account_label.in_(account_ids))

    q = q.group_by(
        func.strftime("%Y-%m", Transaction.date),
        func.coalesce(Transaction.category, "Altro"),
    ).order_by(
        func.strftime("%Y-%m", Transaction.date),
    )

    rows = q.all()
    return [
        {
            "month": r.month,
            "category": r.category,
            "total_amount": float(r.total_amount),
        }
        for r in rows
    ]


# ── BudgetTarget (A-02) ─────────────────────────────────────────────────────

def get_budget_targets(session: Session) -> list["BudgetTarget"]:
    """Return all budget targets ordered by category name."""
    from db.models import BudgetTarget
    return session.query(BudgetTarget).order_by(BudgetTarget.category).all()


def upsert_budget_target(session: Session, category: str, target_pct: Decimal) -> "BudgetTarget":
    """Insert or update a budget target for a category (unique on category)."""
    from db.models import BudgetTarget
    existing = session.query(BudgetTarget).filter_by(category=category).first()
    if existing:
        existing.target_pct = target_pct
        session.flush()
        return existing
    bt = BudgetTarget(category=category, target_pct=target_pct)
    session.add(bt)
    session.flush()
    return bt


def delete_budget_target(session: Session, target_id: int) -> bool:
    """Delete a budget target by id. Returns True if deleted."""
    from db.models import BudgetTarget
    bt = session.get(BudgetTarget, target_id)
    if bt is None:
        return False
    session.delete(bt)
    session.flush()
    return True


def delete_budget_target_by_category(session: Session, category: str) -> bool:
    """Delete a budget target by category name. Returns True if deleted."""
    from db.models import BudgetTarget
    bt = session.query(BudgetTarget).filter_by(category=category).first()
    if bt is None:
        return False
    session.delete(bt)
    session.flush()
    return True


def get_period_totals(
    session: Session,
    date_from: str,
    date_to: str,
    exclude_internal: bool = True,
) -> dict:
    """Get total income and expenses for a date range, plus per-category expense breakdown.

    Returns dict with keys:
        total_income, total_expenses, by_category (list of {category, amount})
    Excludes card_settlement, aggregate_debit, and optionally internal transfers.
    """
    from sqlalchemy import func, case

    excluded = ["card_settlement", "aggregate_debit"]
    if exclude_internal:
        excluded += ["internal_in", "internal_out"]

    # Total income (amount > 0) and total expenses (amount < 0)
    totals = session.query(
        func.coalesce(
            func.sum(case((Transaction.amount > 0, Transaction.amount))),
            0
        ).label("total_income"),
        func.coalesce(
            func.sum(case((Transaction.amount < 0, Transaction.amount))),
            0
        ).label("total_expenses"),
    ).filter(
        Transaction.tx_type.notin_(excluded),
        Transaction.date >= date_from,
        Transaction.date <= date_to,
    ).first()

    total_income = float(totals.total_income) if totals else 0.0
    total_expenses = abs(float(totals.total_expenses)) if totals else 0.0

    # Per-category expense breakdown (only expenses, amount < 0)
    cat_rows = session.query(
        func.coalesce(Transaction.category, "Altro").label("category"),
        func.sum(Transaction.amount).label("amount"),
    ).filter(
        Transaction.tx_type.notin_(excluded),
        Transaction.date >= date_from,
        Transaction.date <= date_to,
        Transaction.amount < 0,
    ).group_by(
        func.coalesce(Transaction.category, "Altro"),
    ).order_by(
        func.sum(Transaction.amount),
    ).all()

    by_category = [
        {"category": r.category, "amount": abs(float(r.amount))}
        for r in cat_rows
    ]

    return {
        "total_income": total_income,
        "total_expenses": total_expenses,
        "by_category": by_category,
    }


# ── LLM Usage Log ────────────────────────────────────────────────────────────

def log_llm_usage(
    session: Session,
    *,
    backend: str,
    model_id: str,
    caller: str,
    step: str | None = None,
    source_name: str | None = None,
    batch_size: int | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    n_ctx: int | None = None,
    duration_ms: int | None = None,
) -> None:
    """Persist a single LLM call's token usage."""
    session.add(LlmUsageLog(
        backend=backend,
        model_id=model_id,
        caller=caller,
        step=step,
        source_name=source_name,
        batch_size=batch_size,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        n_ctx=n_ctx,
        duration_ms=duration_ms,
    ))
    session.commit()


def get_token_usage_stats(
    session: Session,
    backend: str | None = None,
    model_id: str | None = None,
    caller: str | None = None,
) -> list[dict]:
    """Return token usage statistics grouped by (backend, model_id, caller).

    Each row contains: count, mean, std, p50, p95, p99 for prompt_tokens,
    completion_tokens, and total_tokens, plus CI 95% bounds.
    """
    import math

    query = session.query(LlmUsageLog)
    if backend:
        query = query.filter(LlmUsageLog.backend == backend)
    if model_id:
        query = query.filter(LlmUsageLog.model_id == model_id)
    if caller:
        query = query.filter(LlmUsageLog.caller == caller)

    rows = query.all()
    if not rows:
        return []

    # Group by (backend, model_id, caller)
    groups: dict[tuple, list[LlmUsageLog]] = {}
    for r in rows:
        key = (r.backend, r.model_id, r.caller)
        groups.setdefault(key, []).append(r)

    results = []
    for (be, mid, cal), entries in groups.items():
        n = len(entries)
        for metric in ("prompt_tokens", "completion_tokens", "total_tokens"):
            values = sorted(getattr(e, metric) or 0 for e in entries)
            mean = sum(values) / n
            variance = sum((v - mean) ** 2 for v in values) / n if n > 1 else 0.0
            std = math.sqrt(variance)
            ci_half = 1.96 * std / math.sqrt(n) if n > 1 else 0.0

            results.append({
                "backend": be,
                "model_id": mid,
                "caller": cal,
                "metric": metric,
                "count": n,
                "mean": round(mean, 1),
                "std": round(std, 1),
                "p50": values[n // 2],
                "p95": values[int(n * 0.95)] if n >= 20 else values[-1],
                "p99": values[int(n * 0.99)] if n >= 100 else values[-1],
                "ci95_lower": round(mean - ci_half, 1),
                "ci95_upper": round(mean + ci_half, 1),
            })

    return results


def get_adaptive_n_ctx_cap(
    session: Session,
    model_id: str,
    min_observations: int = 1000,
) -> int | None:
    """Max CI-95% upper-bound of total_tokens across all caller/step combos.

    Groups LlmUsageLog rows by (caller, step) for the given *model_id*.
    Only groups with at least *min_observations* rows are considered.
    Returns the maximum CI-95% upper-bound (rounded up to the next 1024
    multiple) so that a single context window can cover the worst-case
    caller.  Returns ``None`` when no group qualifies.
    """
    import math

    rows = (
        session.query(LlmUsageLog)
        .filter(
            LlmUsageLog.model_id == model_id,
            LlmUsageLog.total_tokens.isnot(None),
            LlmUsageLog.total_tokens > 0,
        )
        .all()
    )
    if not rows:
        return None

    # Group by (caller, step)
    groups: dict[tuple[str, str | None], list[int]] = {}
    for r in rows:
        key = (r.caller, r.step)
        groups.setdefault(key, []).append(r.total_tokens)

    max_upper = 0.0
    found = False
    for _key, values in groups.items():
        n = len(values)
        if n < min_observations:
            continue
        found = True
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        std = math.sqrt(variance)
        ci_upper = mean + 1.96 * std / math.sqrt(n)
        if ci_upper > max_upper:
            max_upper = ci_upper

    if not found:
        return None

    # Round up to next 1024 multiple, enforce floor of 2048
    cap = max(int(math.ceil(max_upper / 1024)) * 1024, 2048)
    return cap


def get_counterpart_stats(
    session: Session,
    tx_types: tuple[str, ...] = ("expense", "income"),
    date_from: str | None = None,
    date_to: str | None = None,
    negative_only: bool = False,
    positive_only: bool = False,
) -> list[dict]:
    """Aggregate transactions by description to produce per-counterpart stats.

    Returns a list of dicts with keys:
      description, tx_count, avg_amount, total_amount, modal_category,
      modal_subcategory, variability_pct, source_mode, categorized_by_user,
      is_giroconto, categories, uncategorized_count

    Movements the user already checked (human_validated) are left out, of
    the list and of the figures: like a counterpart where one buys a bit of
    everything, they are not asked about again (founder, 2026-10-23).

    `categorized_by_user` is True when one of the user's rules covers the
    counterpart, or when every movement of it got its category from the
    user (a rule or a choice by hand). A rule decides for the movements of
    every import, so a counterpart with a rule is never asked again.

    `categories` lists (category, movements) most used first;
    `uncategorized_count` is how many movements still carry a category the
    user did not decide, so a counterpart already worked through shows only
    what the last import added.

    With `date_from`/`date_to` (ISO dates, inclusive) the figures cover the
    movements of that period only, and counterparts without movements in it
    are left out. With `negative_only` only money going out counts, with
    `positive_only` only money coming in: a card statement's movements are
    all `card_tx`, purchases and refunds alike.

    is_giroconto is True when most of the counterpart's movements are internal
    transfers (only possible when tx_types includes internal_out/internal_in).
    """
    from collections import Counter, defaultdict

    rows = (
        session.query(
            Transaction.description,
            Transaction.amount,
            Transaction.tx_type,
            Transaction.category,
            Transaction.subcategory,
            Transaction.category_source,
            Transaction.date,
        )
        .filter(
            Transaction.description.isnot(None),
            Transaction.description != "",
            Transaction.tx_type.in_(tx_types),
            Transaction.human_validated.isnot(True),
            *([Transaction.amount < 0] if negative_only else []),
            *([Transaction.amount > 0] if positive_only else []),
        )
        .all()
    )

    # One counterpart whatever the case, accents, punctuation or initials
    # ("Coop", "COOP", "S.R.L."/"SRL": spec 21). The first-seen
    # original writing is kept for display.
    groups: dict[str, list] = defaultdict(list)
    display: dict[str, str] = {}
    for row in rows:
        key = exact_key(row.description) or (row.description or "")
        groups[key].append(row)
        display.setdefault(key, row.description)

    def _in_period(row) -> bool:
        d = row.date or ""
        return (date_from is None or d >= date_from) and (date_to is None or d <= date_to)

    # An accepted counterpart is done, like one with a rule (spec 16, 3.4).
    rules = get_category_rules(session, include_accepted=True)
    stats = []
    for key, all_txs in groups.items():
        txs = [t for t in all_txs if _in_period(t)]
        if not txs:
            continue
        desc = display[key]
        tx_count = len(txs)
        total_amount = sum(abs(float(t.amount or 0)) for t in txs)
        avg_amount = total_amount / tx_count
        # Which categories fit: spending, income, or both when the
        # counterpart has movements in the two directions.
        _signs = {float(t.amount or 0) < 0 for t in txs}
        direction = "mixed" if len(_signs) > 1 else ("expense" if True in _signs else "income")

        cat_counts: Counter = Counter(t.category for t in txs if t.category)
        if cat_counts:
            modal_cat, modal_count = cat_counts.most_common(1)[0]
        else:
            modal_cat, modal_count = "", 0
        sub_counts: Counter = Counter(
            t.subcategory for t in txs if t.category == modal_cat and t.subcategory
        )
        modal_sub = sub_counts.most_common(1)[0][0] if sub_counts else ""
        variability_pct = (modal_count / tx_count * 100) if tx_count else 0.0

        sources = {t.category_source for t in txs if t.category_source}
        if len(sources) == 1:
            source_mode = next(iter(sources))
        elif sources:
            source_mode = "mixed"
        else:
            source_mode = "unknown"

        uncategorized_count = sum(
            1 for t in txs if t.category_source not in USER_CATEGORY_SOURCES
        )
        # A rule covers the counterpart only in its direction (spec 18).
        _dirs = {"out" if float(t.amount or 0) < 0 else "in" for t in txs}
        _covers = [r for r in rules if r.matches(desc) and (r.direction is None or r.direction in _dirs)]
        categorized_by_user = uncategorized_count == 0 or bool(_covers)
        n_internal = sum(1 for t in txs if t.tx_type in ("internal_out", "internal_in"))
        # Multi-category (spec 24): decided by its rule; its movements not yet
        # chosen by hand are the ones to see one by one.
        is_multi = any(r.is_multi_category for r in _covers)
        to_see = sum(1 for t in txs if t.category_source == "multi")

        stats.append(
            {
                "description": desc,
                "tx_count": tx_count,
                "avg_amount": avg_amount,
                "total_amount": total_amount,
                "direction": direction,
                "modal_category": modal_cat,
                "modal_subcategory": modal_sub,
                "variability_pct": variability_pct,
                "source_mode": source_mode,
                "categorized_by_user": categorized_by_user,
                "is_giroconto": n_internal * 2 > tx_count,
                "is_multi_category": is_multi,
                "to_see_one_by_one": to_see,
                "categories": cat_counts.most_common(),
                "uncategorized_count": uncategorized_count,
            }
        )

    return stats


# ── The direction of one import, settled by the person who owns the money ────

_FLIPPED_TX_TYPE = {
    "expense": "income",
    "income": "expense",
    "internal_out": "internal_in",
    "internal_in": "internal_out",
}


def invert_batch_amounts(session: Session, batch_sha256: str) -> int:
    """Turn one import's amounts the other way round, and say how many moved.

    The direction of the amounts is a convention of the file, not a property
    of each row: a statement does not write some of its expenses positive and
    others negative. So this flips the whole import in one go rather than
    asking about rows one at a time.

    tx_type travels with the amount. Leaving it behind would produce rows
    labelled an expense holding a positive number, which every later
    reading - totals, budgets, the report - would then disagree about.
    """
    batch = get_import_batch(session, batch_sha256)
    if batch is None:
        return 0

    rows = session.query(Transaction).filter(Transaction.batch_id == batch.id).all()
    for row in rows:
        if row.amount is not None:
            row.amount = -row.amount
        if row.tx_type in _FLIPPED_TX_TYPE:
            row.tx_type = _FLIPPED_TX_TYPE[row.tx_type]

    session.flush()
    return len(rows)
