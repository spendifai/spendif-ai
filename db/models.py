"""SQLAlchemy ORM models (RF-07).

Tables:
  import_batch            – one record per imported file
  document_schema         – parsing template (Flow 2 → Flow 1 promotion)
  transaction             – canonical transaction with all fields
  reconciliation_link     – card_settlement ↔ card_tx N:M
  internal_transfer_link  – giroconto pairs
  category_rule           – user-defined override rules (RF-05)
  user_settings           – persistent user preferences (locale, language)
  taxonomy_category       – category definitions (expense / income)
  taxonomy_subcategory    – subcategory definitions (FK → taxonomy_category)
  account                 – user-defined bank accounts (stable dedup key)
  description_rule        – bulk description replacement rules (raw_description → cleaned)
  budget_target           – per-category % budget targets (A-02)
  nsi_tag_mapping         – OSM tag → (category, subcategory) for C-08-cascade NSI bypass
  update_event            - one row per app version this install has run (OS + install method)
"""
from __future__ import annotations

import hashlib
import os
import pathlib
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
    inspect,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, relationship, validates

from core.normalizer import counterpart_case, tidy_text
from core.rule_key import exact_key

# The DB URL is read once at import time from the SPENDIFAI_DB env var so
# that every call site that imports `get_engine()` without arguments lands
# on the same database the app initialized. Without this, helpers like
# `_log_usage_to_db` would silently target `./ledger.db` in the cwd while
# the app uses `~/.spendifai/db.sqlite` set via SPENDIFAI_DB by the bundle
# launcher, producing "no such table" errors against a phantom file.
DB_URL = os.environ.get("SPENDIFAI_DB", "sqlite:///ledger.db")


class Base(DeclarativeBase):
    pass


# One rule for "the same counterpart" in SQL too (spec 21): case, accents,
# punctuation and initials written apart do not count. Registered on every
# SQLite connection of the process, so queries compare as Python does.
@event.listens_for(Engine, "connect")
def _register_exact_key(dbapi_connection, _record) -> None:
    create_function = getattr(dbapi_connection, "create_function", None)
    if create_function is not None:
        create_function("exact_key", 1, exact_key, deterministic=True)


def same_counterpart(column, text: str | None):
    """SQL condition: *column* is the same counterpart as *text*."""
    return func.exact_key(column) == exact_key(text)


def written_counterpart(text: str | None) -> str | None:
    """How a counterpart is written, the one rule for every writer (spec 21):
    import, payment intermediaries, the review page, the user's own
    text. Tidied, every word with a capital initial ("Google Cloud", founder
    2026-10-21); empty becomes None."""
    if text is None:
        return None
    return counterpart_case(tidy_text(text)) or None


def get_engine(db_url: str = DB_URL):
    return create_engine(db_url, connect_args={"check_same_thread": False})


DEFAULT_USER_SETTINGS = {
    "date_display_format": "%d/%m/%Y",
    "amount_decimal_sep": ",",
    "amount_thousands_sep": ".",
    "description_language": "it",
    "country": "",  # ISO 3166-1 alpha-2 (e.g. "IT", "DE"). Empty = not set.
    "llm_backend": "local_llama_cpp",
    "llama_cpp_model_path": "",
    "llama_cpp_n_gpu_layers": "-1",
    "ollama_base_url": "http://localhost:11434",
    "ollama_model": "gemma3:12b",
    "openai_api_key": "",
    "openai_model": "gpt-4o-mini",
    "anthropic_api_key": "",
    "anthropic_model": "claude-haiku-4-5-20251001",
    "compat_base_url": "",
    "compat_api_key": "",
    "compat_model": "",
    "use_owner_names_giroconto": "false",
    "import_test_mode": "false",
    "contexts": '["Quotidianità", "Lavoro", "Vacanza"]',
    "giroconto_mode": "neutral",
    "max_transaction_amount": "1000000",
    "force_schema_import": "false",  # I-04: skip schema review, always auto-import
    # Update check. Enabled by default: an alpha tester who never learns a new
    # build exists is a worse outcome than one request to api.github.com at
    # launch. The privacy notice declares it and Settings turns it off.
    "update_check_enabled": "true",
    "update_latest_known": "",       # newest version seen on GitHub, "" = never checked
    "update_last_checked_at": "",    # ISO-8601 UTC of the last successful check
    # Categorizer-specific backend (legacy keys — `cat_*` is the old 2-slot
    # API kept for backward compatibility. New code reads `categorizer_*`
    # first and falls back to `cat_*` if empty. Will be removed once all
    # users have migrated to the 4-slot model in `_config_from_settings`.)
    "cat_llm_backend": "",
    "cat_ollama_base_url": "",
    "cat_ollama_model": "",
    "cat_openai_api_key": "",
    "cat_openai_model": "",
    "cat_anthropic_api_key": "",
    "cat_anthropic_model": "",
    "cat_compat_base_url": "",
    "cat_compat_api_key": "",
    "cat_compat_model": "",
    "cat_llama_cpp_model_path": "",
    "cat_llama_cpp_n_gpu_layers": "-1",
    "cat_llama_cpp_n_ctx": "0",
    # ── 4-slot phase-specific backends (AI-90) ────────────────────────────
    # One backend per LLM prompt-family in the pipeline:
    #   classifier   → core/classifier.py     (single-shot, structural reasoning)
    #   cleaner      → core/description_cleaner.py (batched, NER-like)
    #   categorizer  → core/categorizer.py    (batched, classification)
    #   footer       → core/normalizer.py footer_detect (rare, structural)
    # When a phase-specific key is empty the runtime falls back to the
    # main `llm_backend` (and its parameters), so existing installs keep
    # working with their current single-model setup until the user opts
    # into per-phase specialisation via Settings.
    # API keys (openai, anthropic, compat) and base URLs (ollama, compat)
    # are NOT duplicated per phase — they are account-level and reused
    # from the main backend automatically.
    "classifier_llm_backend": "",
    "classifier_llama_cpp_model_path": "",
    "classifier_llama_cpp_n_gpu_layers": "-1",
    "classifier_llama_cpp_n_ctx": "0",
    "classifier_ollama_model": "",
    "classifier_openai_model": "",
    "classifier_anthropic_model": "",
    "classifier_compat_model": "",
    "cleaner_llm_backend": "",
    "cleaner_llama_cpp_model_path": "",
    "cleaner_llama_cpp_n_gpu_layers": "-1",
    "cleaner_llama_cpp_n_ctx": "0",
    "cleaner_ollama_model": "",
    "cleaner_openai_model": "",
    "cleaner_anthropic_model": "",
    "cleaner_compat_model": "",
    "categorizer_llm_backend": "",
    "categorizer_llama_cpp_model_path": "",
    "categorizer_llama_cpp_n_gpu_layers": "-1",
    "categorizer_llama_cpp_n_ctx": "0",
    "categorizer_ollama_model": "",
    "categorizer_openai_model": "",
    "categorizer_anthropic_model": "",
    "categorizer_compat_model": "",
    "footer_llm_backend": "",
    "footer_llama_cpp_model_path": "",
    "footer_llama_cpp_n_gpu_layers": "-1",
    "footer_llama_cpp_n_ctx": "0",
    "footer_ollama_model": "",
    "footer_openai_model": "",
    "footer_anthropic_model": "",
    "footer_compat_model": "",
    # NOTE: onboarding_done is NOT in defaults — it's managed by
    # _migrate_set_onboarding_done_for_existing_users() and SettingsService.
}


def _schema_hash() -> str:
    """Return SHA-256 hex digest of this module's source file."""
    src = pathlib.Path(__file__).read_bytes()
    return hashlib.sha256(src).hexdigest()


def _schema_hash_path(engine) -> pathlib.Path:
    """Return the path to the .schema_hash file next to the database."""
    url = str(engine.url)
    # sqlite:///ledger.db  or  sqlite:////abs/path/ledger.db
    if url.startswith("sqlite"):
        db_path = url.split("///", 1)[-1]
        if not db_path or db_path == ":memory:":
            return pathlib.Path("")  # in-memory — no caching
        return pathlib.Path(db_path).resolve().parent / ".schema_hash"
    return pathlib.Path("")


def _db_has_tables(engine) -> bool:
    """True if the engine's database has at least one user table.

    Used to invalidate the .schema_hash fast-path when the DB file was
    deleted (or recreated empty) while the hash cache lingered.
    """
    try:
        return bool(inspect(engine).get_table_names())
    except Exception:
        return False


def create_tables(engine=None):
    if engine is None:
        engine = get_engine()

    # ── fast-path: skip migrations if models.py hasn't changed ───────
    # Guard: only trust the hash cache if the DB actually has tables.
    # If the DB file was deleted (or is empty) while .schema_hash lingered,
    # skipping create_all leaves an empty schema → runtime "no such table".
    current_hash = _schema_hash()
    hash_file = _schema_hash_path(engine)
    if hash_file.name and hash_file.is_file() and _db_has_tables(engine):
        try:
            saved = hash_file.read_text().strip()
            if saved == current_hash:
                return engine          # schema up-to-date, nothing to do
        except OSError:
            pass  # unreadable — fall through to full migration

    # ── full migration path ──────────────────────────────────────────
    try:
        Base.metadata.create_all(engine, checkfirst=True)
    except Exception as exc:
        if "already exists" not in str(exc).lower():
            raise
    _migrate_add_raw_columns(engine)
    _migrate_add_user_settings(engine)
    _migrate_add_import_job(engine)
    _migrate_add_invert_sign(engine)
    _migrate_add_user_confirmed(engine)
    _migrate_add_taxonomy_default(engine)   # must run before _migrate_add_taxonomy
    _migrate_add_taxonomy(engine)
    _migrate_add_accounts(engine)
    _migrate_add_description_cols(engine)
    _migrate_add_context(engine)
    _migrate_add_description_rules(engine)
    _migrate_add_rule_context(engine)
    _migrate_add_rule_giroconto(engine)
    _migrate_add_rule_multi_category(engine)
    _migrate_add_header_sha256(engine)
    _migrate_add_confidence_score(engine)
    _migrate_add_transaction_updated_at(engine)
    _migrate_add_classification_tracking(engine)
    _migrate_add_taxonomy_fallback(engine)
    _migrate_add_account_type(engine)
    _migrate_consolidate_account_type(engine)
    _migrate_savings_to_savings_account(engine)
    _migrate_add_import_batch_tracking(engine)
    _migrate_add_budget_target(engine)
    _migrate_add_footer_patterns(engine)
    _migrate_add_has_borders(engine)
    _migrate_add_nsi_tag_mapping(engine)
    _migrate_add_category_model(engine)
    _migrate_add_llm_usage_log(engine)
    _migrate_add_category_correction(engine)
    _migrate_add_account_closed_on(engine)
    _migrate_add_account_usage(engine)
    _migrate_add_import_job_n_rows(engine)
    _migrate_add_transaction_model_proposal(engine)
    _migrate_counterparts_written_one_way(engine)
    _migrate_add_transfer_link_decision(engine)
    _migrate_add_category_color(engine)
    _migrate_giroconti_without_category(engine)
    _migrate_set_onboarding_done_for_existing_users(engine)  # must run last
    _migrate_purge_orphan_schemas(engine)  # cleanup: remove schemas without header_sha256

    # ── persist hash so next startup skips migrations ────────────────
    if hash_file.name:
        try:
            hash_file.write_text(current_hash)
        except OSError:
            pass  # non-critical — migrations will just re-run next time

    return engine


def _migrate_add_user_settings(engine) -> None:
    """Create user_settings table and seed defaults if not already present."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS user_settings '
            '(key VARCHAR(64) PRIMARY KEY, value VARCHAR(255))'
        ))
        conn.commit()
        # Seed defaults only if the table was just created (no rows yet)
        row = conn.execute(_text("SELECT COUNT(*) FROM user_settings")).scalar()
        if row == 0:
            for k, v in DEFAULT_USER_SETTINGS.items():
                conn.execute(
                    _text("INSERT INTO user_settings (key, value) VALUES (:k, :v)"),
                    {"k": k, "v": v},
                )
            conn.commit()
        else:
            # Insert any missing keys (for existing DBs upgrading)
            for k, v in DEFAULT_USER_SETTINGS.items():
                conn.execute(
                    _text("INSERT OR IGNORE INTO user_settings (key, value) VALUES (:k, :v)"),
                    {"k": k, "v": v},
                )
            conn.commit()


def _migrate_add_raw_columns(engine) -> None:
    """Add raw_description / raw_amount to existing DBs (idempotent).

    Uses try/except instead of PRAGMA inspection to stay compatible with
    both SQLAlchemy 1.x and 2.x row-access semantics.
    """
    from sqlalchemy import text as _text
    stmts = [
        'ALTER TABLE "transaction" ADD COLUMN raw_description TEXT',
        'ALTER TABLE "transaction" ADD COLUMN raw_amount TEXT',
    ]
    with engine.connect() as conn:
        for stmt in stmts:
            try:
                conn.execute(_text(stmt))
                conn.commit()
            except Exception as exc:
                if "duplicate column name" in str(exc).lower():
                    pass  # column already exists, nothing to do
                else:
                    raise


def get_session(engine=None) -> Session:
    from sqlalchemy.orm import sessionmaker
    if engine is None:
        engine = get_engine()
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


# ── Tables ────────────────────────────────────────────────────────────────────

class ImportBatch(Base):
    __tablename__ = "import_batch"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sha256 = Column(String(64), unique=True, nullable=False, index=True)
    filename = Column(String(512), nullable=False)
    account_label = Column(String(256), nullable=True)
    imported_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    flow_used = Column(String(10))  # "flow1" | "flow2"
    n_transactions = Column(Integer, default=0)
    status = Column(String(16), default="completed")  # completed | cancelled
    errors = Column(Text)

    transactions = relationship("Transaction", back_populates="batch")


class DocumentSchemaModel(Base):
    __tablename__ = "document_schema"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_identifier = Column(String(512), unique=True, nullable=False, index=True)
    doc_type = Column(String(32), nullable=False)
    date_col = Column(String(128))
    date_accounting_col = Column(String(128))
    amount_col = Column(String(128))
    debit_col = Column(String(128))
    credit_col = Column(String(128))
    description_col = Column(String(128))
    description_cols = Column(Text)  # JSON array of column names for multi-col concat
    currency_col = Column(String(128))
    default_currency = Column(String(3), default="EUR")
    date_format = Column(String(64))
    sign_convention = Column(String(32))
    is_zero_sum = Column(Boolean, default=False)
    invert_sign = Column(Boolean, default=False)
    internal_transfer_patterns = Column(Text)  # JSON array
    footer_patterns = Column(Text)  # JSON array of learned footer text patterns
    has_borders = Column(Boolean, default=False)  # True if XLSX format uses bordered table
    account_label = Column(String(256))
    encoding = Column(String(32), default="utf-8")
    sheet_name = Column(String(256))
    skip_rows = Column(Integer, default=0)
    delimiter = Column(String(4))
    confidence = Column(String(10))
    confidence_score = Column(Float, nullable=True)  # 0.0-1.0 deterministic score
    header_sha256 = Column(String(64), nullable=True, index=True)
    # The seal. Set when the person answered the question about the direction
    # of the amounts for this format. Once set, the answer is applied in
    # silence for ever after: asking twice is how a product teaches people
    # that their answers do not stick.
    user_confirmed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, onupdate=lambda: datetime.now(timezone.utc))


# Two questions about a movement, never answered by the same flag:
# - categorized by the user (how the money was spent): the category comes from
#   one of the user's rules or was chosen by hand. A rule answers it for the
#   counterpart's past and future movements alike.
# - checked by the user (the money was really spent): human_validated, set only
#   by confirming the movement itself in the registry, one by one. It is never
#   inherited and never set by a category change.
USER_CATEGORY_SOURCES = ("manual", "rule")

# Categories the product proposed on its own: defined with the sources in
# core.models, kept on the movement in the model_* columns.
from core.models import AUTOMATIC_CATEGORY_SOURCES  # noqa: E402,F401


INTERNAL_TX_TYPES = ("internal_out", "internal_in")


class Transaction(Base):
    __tablename__ = "transaction"

    id = Column(String(24), primary_key=True)  # SHA-256[:24]
    batch_id = Column(Integer, ForeignKey("import_batch.id"), nullable=True)
    date = Column(String(10), nullable=False)       # ISO 8601 YYYY-MM-DD
    date_accounting = Column(String(10))
    amount = Column(Numeric(precision=18, scale=4), nullable=False)
    currency = Column(String(3), default="EUR")
    description = Column(Text)
    source_file = Column(String(512))
    doc_type = Column(String(32))
    account_label = Column(String(256))
    tx_type = Column(String(32))
    category = Column(String(128))
    subcategory = Column(String(128))
    category_confidence = Column(String(10))
    category_source = Column(String(10))            # "manual" | "rule" | "llm" | future: "history"
    category_model = Column(String(128))             # specific LLM model used (e.g. "gemma-2-2b-it-Q4_K_M")
    # The product's own proposal at import, never changed afterwards (see
    # AUTOMATIC_CATEGORY_SOURCES); empty when a user's rule decided at import.
    model_category = Column(String(128), nullable=True)
    model_subcategory = Column(String(128), nullable=True)
    model_confidence = Column(String(10), nullable=True)
    reconciled = Column(Boolean, default=False)
    to_review = Column(Boolean, default=False)
    transfer_pair_id = Column(String(64))
    transfer_confidence = Column(String(10))
    raw_description = Column(Text, nullable=True)   # original text before normalize_description
    raw_amount = Column(String(64), nullable=True)  # original string from source file
    context = Column(String(64), nullable=True)     # user-defined life context (e.g. Vacanza, Lavoro)
    # Checked by the user: the money was really spent. Not about the category.
    human_validated = Column(Boolean, default=False)
    validated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, onupdate=lambda: datetime.now(timezone.utc))

    @validates("description")
    def _written_counterpart(self, _key, value):
        # Every write of the counterpart, whatever the code path.
        return written_counterpart(value)

    @validates("tx_type")
    def _giroconto_has_no_category(self, _key, value):
        """A giroconto moves money between the user's own accounts: it has no
        category. Whatever turns a movement into a giroconto (by hand, rule,
        transfer pair) empties its category; turning it back gives it the
        product's proposal from the import, to review, or none. Only real
        changes count: building a movement sets the type with no old one."""
        old = self.tx_type
        if old is None or old == value:
            return value
        if value in INTERNAL_TX_TYPES and old not in INTERNAL_TX_TYPES:
            self.category = self.subcategory = None
            self.category_source = self.category_confidence = self.category_model = None
        elif old in INTERNAL_TX_TYPES and value not in INTERNAL_TX_TYPES and self.category is None:
            if self.model_category:
                self.category, self.subcategory = self.model_category, self.model_subcategory
                self.category_source = "llm"
                self.category_confidence = self.model_confidence or "low"
            self.to_review = True
        return value

    batch = relationship("ImportBatch", back_populates="transactions")


class ReconciliationLink(Base):
    __tablename__ = "reconciliation_link"

    id = Column(Integer, primary_key=True, autoincrement=True)
    settlement_id = Column(String(24), ForeignKey("transaction.id"), nullable=False)
    detail_id = Column(String(24), ForeignKey("transaction.id"), nullable=False)
    delta = Column(Numeric(precision=18, scale=4), default=0)
    method = Column(String(32))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("settlement_id", "detail_id"),)


class InternalTransferLink(Base):
    __tablename__ = "internal_transfer_link"

    id = Column(Integer, primary_key=True, autoincrement=True)
    out_id = Column(String(24), ForeignKey("transaction.id"), nullable=False)
    in_id = Column(String(24), ForeignKey("transaction.id"), nullable=False)
    confidence = Column(String(10))
    keyword_matched = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # The user's answer on the pair: "yes" (a transfer) or "no" (kept apart,
    # never proposed again). None: not answered yet.
    user_decision = Column(String(8), nullable=True)

    __table_args__ = (UniqueConstraint("out_id", "in_id"),)


class HeldMovement(Base):
    """A movement kept out of the ledger until the user fixes it.

    Today: a date after the day of the import, which cannot be right (day and
    month swapped, a value date, a footer line). The movement went through the
    whole import (description, category, sign) and waits here in full
    (``payload``, the fields ``upsert_transaction`` takes); with a corrected
    date it enters the ledger as is. Nothing else reads this table: held
    movements count nowhere.
    """
    __tablename__ = "held_movement"

    id = Column(String(24), primary_key=True)          # the movement's own id
    payload = Column(Text, nullable=False)             # JSON of the movement
    date = Column(String(10), nullable=False)          # the date as read
    account_label = Column(String(256))
    amount = Column(Numeric(precision=18, scale=4))
    description = Column(Text)
    source_file = Column(String(512))
    reason = Column(String(16), nullable=False)        # "date_future"
    batch_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class TransferDestination(Base):
    """Where a one-sided transfer went, answered once per beneficiary.

    key: the IBAN in the description when there is one, otherwise the cleaned
    description. answer: "unloaded" (an account of mine not loaded yet),
    "untracked" (an account of mine I do not follow), "other_person" (not a
    transfer: spending or income).
    """
    __tablename__ = "transfer_destination"

    key = Column(String(256), primary_key=True)
    answer = Column(String(16), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class PaymentIntermediary(Base):
    """A transparent payment intermediary (PayPal, SumUp...): the counterpart
    of a movement paid through it is the merchant after the asterisk.

    Spec 20. ``origin``: "seed" (shipped with the app), "learned" (followed
    by two or more different merchants in the user's data), "user" (added in
    the Intermediaries page). ``active`` False is the user's decision and is
    never turned back on by a seed update or by learning.
    """
    __tablename__ = "payment_intermediary"

    prefix = Column(String(64), primary_key=True)
    origin = Column(String(10), nullable=False, default="seed")
    active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class CategoryRule(Base):
    __tablename__ = "category_rule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pattern = Column(Text, nullable=False)
    match_type = Column(String(11), nullable=False)  # contains | regex | exact | starts_with
    category = Column(String(128), nullable=False)
    subcategory = Column(String(128))
    context = Column(String(64))               # optional — None means "don't set"
    doc_type = Column(String(32))
    priority = Column(Integer, default=0)
    # A giroconto rule marks the movement as an internal transfer instead of
    # giving it a category; category stays empty.
    is_giroconto = Column(Boolean, default=False, nullable=False, server_default="0")
    # A multi-category rule (spec 24): the counterpart is a place where
    # everything is bought; its movements take the generic category and are
    # seen one by one. Category stays empty.
    is_multi_category = Column(Boolean, default=False, nullable=False, server_default="0")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DescriptionRule(Base):
    """Bulk description replacement rule.

    When a transaction's raw_description matches raw_pattern (using match_type),
    its description field is replaced with cleaned_description.
    """
    __tablename__ = "description_rule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    raw_pattern = Column(Text, nullable=False)          # pattern to match in raw_description
    match_type = Column(String(10), nullable=False)      # exact | contains | regex
    cleaned_description = Column(Text, nullable=False)  # replacement description
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("raw_pattern", "match_type"),)


class UserSettings(Base):
    __tablename__ = "user_settings"

    key = Column(String(64), primary_key=True)
    value = Column(String(255), nullable=True)


class UpdateEvent(Base):
    """One row per version this installation has actually run.

    Written at startup by services/update_service.py, and only when the running
    version differs from the newest row. A row per launch would grow without
    bound and would answer nothing that a row per version does not.

    WHY os_name and install_method are stored per row instead of being resolved
    when the table is read: both can change underneath the same database. A user
    who moves from the git checkout to the Homebrew cask, or who restores
    ~/.spendifai onto a different machine, would otherwise see their whole
    history relabelled with today's answer. During the 1-to-1 alpha this table
    is also the only way to answer "which build is this tester actually on",
    because the app sends nothing anywhere: the tester reads it off their own
    machine and tells us.
    """

    __tablename__ = "update_event"

    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(String(32), nullable=False)
    previous_version = Column(String(32), nullable=True)
    # install | upgrade | downgrade
    event = Column(String(16), nullable=False)
    # homebrew | dmg | git | deb | rpm | msix | winget | unknown
    install_method = Column(String(16), nullable=False, default="unknown")
    # darwin | windows | linux
    os_name = Column(String(16), nullable=False)
    os_version = Column(String(64), nullable=True)
    arch = Column(String(16), nullable=True)
    detected_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ImportJob(Base):
    __tablename__ = "import_job"

    id = Column(Integer, primary_key=True, autoincrement=True)
    status = Column(String(16), default="running")   # running | completed | error
    progress = Column(Numeric(5, 4), default=0.0)
    status_message = Column(Text)
    detail_message = Column(Text)
    n_transactions = Column(Integer, default=0)
    n_files = Column(Integer, default=0)
    # Rows read from the job's files, duplicates included: what the time is
    # spent on, so seconds per row are computed on it. Null on older jobs.
    n_rows = Column(Integer, nullable=True)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime)
    # ── Phase timing (AI-88) — milliseconds spent in each pipeline phase
    # across all files of this job. Cumulative across the job's files
    # (one job often imports several files in a row, the value is the
    # sum). Null until at least one file is finalised.
    ms_header_detection = Column(Integer)
    ms_classifying      = Column(Integer)
    ms_footer_detection = Column(Integer)
    ms_extracting       = Column(Integer)
    ms_cleaning         = Column(Integer)
    ms_categorizing     = Column(Integer)


class TaxonomyCategory(Base):
    __tablename__ = "taxonomy_category"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    type = Column(String(8), nullable=False)   # "expense" | "income"
    sort_order = Column(Integer, default=0)
    is_fallback = Column(Boolean, default=False)
    # The category's own colour in every chart (spec 25): written once when
    # the category is born, then only by the user. The fallback ignores it.
    color = Column(String(7))

    subcategories = relationship(
        "TaxonomySubcategory",
        back_populates="category",
        cascade="all, delete-orphan",
        order_by="TaxonomySubcategory.sort_order",
    )


class TaxonomySubcategory(Base):
    __tablename__ = "taxonomy_subcategory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category_id = Column(Integer, ForeignKey("taxonomy_category.id"), nullable=False)
    name = Column(String(128), nullable=False)
    sort_order = Column(Integer, default=0)

    category = relationship("TaxonomyCategory", back_populates="subcategories")


class TaxonomyDefault(Base):
    """Built-in taxonomy templates, one row per (language, type, category, subcategory).

    These rows are seeded from db.taxonomy_defaults.TAXONOMY_DEFAULTS and never
    modified by the user.  On onboarding the user picks a language and the rows
    are copied into taxonomy_category / taxonomy_subcategory (user-editable).
    """
    __tablename__ = "taxonomy_default"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    language       = Column(String(8),   nullable=False)   # "it" | "en" | "fr" | "de" | "es"
    type           = Column(String(8),   nullable=False)   # "expense" | "income"
    category       = Column(String(128), nullable=False)
    subcategory    = Column(String(128), nullable=True)     # NULL for category-level rows
    sort_order_cat = Column(Integer, default=0)
    sort_order_sub = Column(Integer, default=0)
    is_fallback = Column(Boolean, default=False)


VALID_ACCOUNT_TYPES = frozenset({
    "bank_account", "credit_card", "debit_card", "prepaid_card",
    "savings_account", "cash",
})


class Account(Base):
    """User-defined bank account.  Provides a stable dedup key (name) that is
    independent of the filename used when importing the file."""
    __tablename__ = "account"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), unique=True, nullable=False)   # e.g. "Conto POPSO", "Carta CartaSI"
    bank_name = Column(String(256))                           # optional free-text bank name
    account_type = Column(String(32), nullable=True)          # bank_account | credit_card | debit_card | prepaid_card | savings_account | cash
    # How the person uses the account: "payments" (ci paghi) or "in_and_out"
    # (ci entrano e escono soldi). Empty = never said. It decides only when the
    # import asks about the direction of the amounts, never the direction.
    usage = Column(String(16), nullable=True)
    # ISO date (YYYY-MM-DD) after which no movements are expected: the account
    # was closed. Empty = active. The Check List stops asking for it.
    closed_on = Column(String(10), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class BudgetTarget(Base):
    """Monthly budget target as % of total expenses for a category (A-02)."""
    __tablename__ = "budget_target"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(128), nullable=False, unique=True)
    target_pct = Column(Numeric(5, 2), nullable=False)
    period_type = Column(String(16), default="monthly")       # monthly (future: quarterly, yearly)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, onupdate=lambda: datetime.now(timezone.utc))


class LlmUsageLog(Base):
    """Per-call LLM token usage log for statistical analysis of context utilization."""
    __tablename__ = "llm_usage_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    backend = Column(String(30), nullable=False)       # local_llama_cpp, ollama, openai, claude
    model_id = Column(String(120), nullable=False)
    caller = Column(String(30), nullable=False)        # categorizer, classifier, description_cleaner, normalizer
    step = Column(String(60))                          # e.g. "step1_identity", "batch_expense", "footer_detect"
    source_name = Column(String(256))                  # file name or context identifier
    batch_size = Column(Integer)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    n_ctx = Column(Integer)                            # context window (local backends only)
    duration_ms = Column(Integer)                      # inference time


class NsiTagMapping(Base):
    """OSM tag → (category, subcategory) mapping for C-08-cascade NSI bypass.

    Built once via NsiTaxonomyService.build() and invalidated when the user
    taxonomy changes (detected by SHA-256 hash comparison).
    """
    __tablename__ = "nsi_tag_mapping"

    osm_tag = Column(String(128), primary_key=True)   # e.g. "shop=supermarket"
    category = Column(String(128), nullable=False)
    subcategory = Column(String(128), nullable=False)
    taxonomy_hash = Column(String(64), nullable=False)  # SHA-256 of taxonomy at build time
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class CategoryCorrection(Base):
    """User correction log — one row every time the user changes a category.

    Captures the original LLM/rule assignment alongside two quality signals:
      - original_confidence: the model's self-reported certainty on that tx
      - consistency_at_correction: % of same-description txs that agreed on
        the same category at the moment of correction (vendor-level coherence)

    Together these let us compute a live implicit benchmark:
      accuracy ≈ 1 - (corrections / total_llm_categorizations)  per model
    and diagnose failure modes (high-confidence errors, inconsistent vendors).
    """
    __tablename__ = "category_correction"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tx_id = Column(String(64), nullable=False, index=True)
    original_category = Column(String(128))
    original_subcategory = Column(String(128))
    original_source = Column(String(10))        # llm | rule | history
    original_model = Column(String(128))        # category_model at correction time
    original_confidence = Column(String(10))    # high | medium | low
    new_category = Column(String(128))
    new_subcategory = Column(String(128))
    consistency_at_correction = Column(Float, nullable=True)  # % modal cat, same description
    correction_origin = Column(String(20))      # ledger | counterparts | review | bulk_edit
    corrected_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def _migrate_add_import_job(engine) -> None:
    """Create import_job table if not present (idempotent) and add the
    AI-88 ms_* phase-timing columns when missing.
    """
    from sqlalchemy import inspect as _inspect
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS import_job ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'status VARCHAR(16) DEFAULT "running", '
            'progress NUMERIC(5,4) DEFAULT 0.0, '
            'status_message TEXT, '
            'detail_message TEXT, '
            'n_transactions INTEGER DEFAULT 0, '
            'n_files INTEGER DEFAULT 0, '
            'started_at DATETIME, '
            'completed_at DATETIME)'
        ))
        conn.commit()

    # Additive: add the AI-88 phase-timing columns to existing rows.
    insp = _inspect(engine)
    existing_cols = {c["name"] for c in insp.get_columns("import_job")}
    phase_cols = (
        "ms_header_detection",
        "ms_classifying",
        "ms_footer_detection",
        "ms_extracting",
        "ms_cleaning",
        "ms_categorizing",
    )
    missing = [c for c in phase_cols if c not in existing_cols]
    if missing:
        with engine.connect() as conn:
            for col in missing:
                conn.execute(_text(f"ALTER TABLE import_job ADD COLUMN {col} INTEGER"))
            conn.commit()


def _migrate_add_taxonomy_default(engine) -> None:
    """Create taxonomy_default table and seed all built-in language templates (idempotent)."""
    from sqlalchemy import text as _text
    from db.taxonomy_defaults import TAXONOMY_DEFAULTS

    with engine.connect() as conn:
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS taxonomy_default ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'language VARCHAR(8) NOT NULL, '
            'type VARCHAR(8) NOT NULL, '
            'category VARCHAR(128) NOT NULL, '
            'subcategory VARCHAR(128), '
            'sort_order_cat INTEGER DEFAULT 0, '
            'sort_order_sub INTEGER DEFAULT 0)'
        ))
        conn.execute(_text(
            'CREATE UNIQUE INDEX IF NOT EXISTS uq_taxonomy_default '
            'ON taxonomy_default(language, type, category, COALESCE(subcategory, \'\'))'
        ))
        conn.commit()

        # Seed all languages from the Python definitions (INSERT OR IGNORE = idempotent)
        for lang, data in TAXONOMY_DEFAULTS.items():
            for type_key, entries in (("expense", data["expenses"]), ("income", data["income"])):
                for sort_cat, entry in enumerate(entries):
                    cat_name = entry["category"]
                    conn.execute(_text(
                        'INSERT OR IGNORE INTO taxonomy_default '
                        '(language, type, category, subcategory, sort_order_cat, sort_order_sub) '
                        'VALUES (:lang, :type, :cat, NULL, :sc, 0)'
                    ), {"lang": lang, "type": type_key, "cat": cat_name, "sc": sort_cat})
                    for sort_sub, sub in enumerate(entry.get("subcategories", [])):
                        conn.execute(_text(
                            'INSERT OR IGNORE INTO taxonomy_default '
                            '(language, type, category, subcategory, sort_order_cat, sort_order_sub) '
                            'VALUES (:lang, :type, :cat, :sub, :sc, :ss)'
                        ), {"lang": lang, "type": type_key, "cat": cat_name,
                            "sub": sub, "sc": sort_cat, "ss": sort_sub})
        conn.commit()


def _migrate_add_taxonomy(engine) -> None:
    """Create taxonomy tables and seed from taxonomy_default (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS taxonomy_category ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'name VARCHAR(128) NOT NULL, '
            'type VARCHAR(8) NOT NULL, '
            'sort_order INTEGER DEFAULT 0)'
        ))
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS taxonomy_subcategory ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'category_id INTEGER NOT NULL REFERENCES taxonomy_category(id) ON DELETE CASCADE, '
            'name VARCHAR(128) NOT NULL, '
            'sort_order INTEGER DEFAULT 0)'
        ))
        conn.commit()

        # Deduplicate any rows that crept in via race conditions at first startup
        # (Streamlit can run app.py twice concurrently before the first commit).
        # Keep only the lowest-id row per (name, type).
        conn.execute(_text(
            "DELETE FROM taxonomy_category WHERE id NOT IN ("
            "  SELECT MIN(id) FROM taxonomy_category GROUP BY name, type"
            ")"
        ))
        conn.commit()

        # Unique index prevents future duplicates (CREATE … IF NOT EXISTS is safe
        # even when the index already exists).
        conn.execute(_text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_taxonomy_category_name_type "
            "ON taxonomy_category(name, type)"
        ))
        conn.commit()

        # Seed only if still empty (INSERT OR IGNORE makes it race-safe).
        count = conn.execute(_text("SELECT COUNT(*) FROM taxonomy_category")).scalar()
        if count == 0:
            _seed_taxonomy(conn)
            conn.commit()


def _seed_taxonomy(conn, language: str = "it") -> None:
    """Seed taxonomy_category/subcategory from taxonomy_default for *language*.

    Falls back to the Python TAXONOMY_DEFAULTS dict if taxonomy_default is empty
    (e.g. during the very first migration run before _migrate_add_taxonomy_default
    has committed its data).
    """
    from sqlalchemy import text as _text

    # Try reading from the taxonomy_default table first
    rows = conn.execute(_text(
        'SELECT type, category, subcategory, sort_order_cat, sort_order_sub '
        'FROM taxonomy_default WHERE language = :lang AND subcategory IS NOT NULL '
        'ORDER BY sort_order_cat, sort_order_sub'
    ), {"lang": language}).fetchall()

    if not rows:
        # Fallback: read from Python dict (covers the migration ordering edge case)
        from db.taxonomy_defaults import TAXONOMY_DEFAULTS
        data = TAXONOMY_DEFAULTS.get(language, TAXONOMY_DEFAULTS["it"])
        for type_key, entries in (("expense", data["expenses"]), ("income", data["income"])):
            for sort_i, entry in enumerate(entries):
                r = conn.execute(_text(
                    "INSERT OR IGNORE INTO taxonomy_category (name, type, sort_order) VALUES (:n,:t,:s)"
                ), {"n": entry["category"], "t": type_key, "s": sort_i})
                cat_id = r.lastrowid
                for sort_j, sub in enumerate(entry.get("subcategories", [])):
                    conn.execute(_text(
                        "INSERT INTO taxonomy_subcategory (category_id, name, sort_order) VALUES (:c,:n,:s)"
                    ), {"c": cat_id, "n": sub, "s": sort_j})
        return

    # Normal path: copy from taxonomy_default
    cat_id_map: dict[tuple[str, str], int] = {}
    for row in rows:
        type_key, cat_name, sub_name, sort_cat, sort_sub = row
        key = (type_key, cat_name)
        if key not in cat_id_map:
            r = conn.execute(_text(
                "INSERT OR IGNORE INTO taxonomy_category (name, type, sort_order) VALUES (:n,:t,:s)"
            ), {"n": cat_name, "t": type_key, "s": sort_cat})
            # lastrowid is 0 on IGNORE — fetch the real id
            existing = conn.execute(_text(
                "SELECT id FROM taxonomy_category WHERE name=:n AND type=:t"
            ), {"n": cat_name, "t": type_key}).scalar()
            cat_id_map[key] = existing
        conn.execute(_text(
            "INSERT INTO taxonomy_subcategory (category_id, name, sort_order) VALUES (:c,:n,:s)"
        ), {"c": cat_id_map[key], "n": sub_name, "s": sort_sub})


def _migrate_add_accounts(engine) -> None:
    """Create account table if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS account '
            '(id INTEGER PRIMARY KEY AUTOINCREMENT, '
            ' name TEXT UNIQUE NOT NULL, '
            ' bank_name TEXT, '
            ' created_at DATETIME)'
        ))
        conn.commit()


def _migrate_add_description_cols(engine) -> None:
    """Add description_cols column to document_schema if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text(
                'ALTER TABLE document_schema ADD COLUMN description_cols TEXT'
            ))
            conn.commit()
        except Exception as exc:
            if "duplicate column name" in str(exc).lower():
                pass  # column already exists
            else:
                raise


def _migrate_add_context(engine) -> None:
    """Add context column to transaction table if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text('ALTER TABLE "transaction" ADD COLUMN context VARCHAR(64)'))
            conn.commit()
        except Exception as exc:
            if "duplicate column name" in str(exc).lower():
                pass
            else:
                raise


def _migrate_add_invert_sign(engine) -> None:
    """Add invert_sign column to document_schema if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text(
                'ALTER TABLE document_schema ADD COLUMN invert_sign BOOLEAN DEFAULT 0'
            ))
            conn.commit()
        except Exception as exc:
            if "duplicate column name" in str(exc).lower():
                pass  # column already exists
            else:
                raise


def _migrate_add_user_confirmed(engine) -> None:
    """Add user_confirmed to document_schema if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text(
                'ALTER TABLE document_schema ADD COLUMN user_confirmed BOOLEAN DEFAULT 0'
            ))
            conn.commit()
        except Exception as exc:
            if "duplicate column name" in str(exc).lower():
                pass  # column already exists
            else:
                raise


def _migrate_add_description_rules(engine) -> None:
    """Create description_rule table if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS description_rule ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'raw_pattern TEXT NOT NULL, '
            'match_type VARCHAR(10) NOT NULL, '
            'cleaned_description TEXT NOT NULL, '
            'created_at DATETIME, '
            'UNIQUE(raw_pattern, match_type))'
        ))
        conn.commit()


def _migrate_add_rule_giroconto(engine) -> None:
    """Add is_giroconto column to category_rule if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text(
                'ALTER TABLE category_rule ADD COLUMN is_giroconto BOOLEAN NOT NULL DEFAULT 0'
            ))
            conn.commit()
        except Exception as exc:
            if "duplicate column name" in str(exc).lower():
                pass
            else:
                raise


def _migrate_add_rule_multi_category(engine) -> None:
    """Add is_multi_category to category_rule if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text(
                'ALTER TABLE category_rule ADD COLUMN is_multi_category BOOLEAN NOT NULL DEFAULT 0'
            ))
            conn.commit()
        except Exception as exc:
            if "duplicate column name" not in str(exc).lower():
                raise


def _migrate_add_rule_context(engine) -> None:
    """Add context column to category_rule if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text(
                'ALTER TABLE category_rule ADD COLUMN context VARCHAR(64)'
            ))
            conn.commit()
        except Exception as exc:
            if "duplicate column name" in str(exc).lower():
                pass
            else:
                raise


def _migrate_add_import_batch_tracking(engine) -> None:
    """Add account_label and status columns to import_batch if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        for col_sql in [
            'ALTER TABLE import_batch ADD COLUMN account_label VARCHAR(256)',
            'ALTER TABLE import_batch ADD COLUMN status VARCHAR(16) DEFAULT "completed"',
        ]:
            try:
                conn.execute(_text(col_sql))
                conn.commit()
            except Exception as exc:
                if "duplicate column name" in str(exc).lower():
                    pass
                else:
                    raise
        # Backfill status for existing rows that have NULL
        conn.execute(_text(
            "UPDATE import_batch SET status = 'completed' WHERE status IS NULL"
        ))
        conn.commit()


def _migrate_set_onboarding_done_for_existing_users(engine) -> None:
    """Mark onboarding as complete only when ALL prerequisites the wizard
    would have configured are already present on the DB.

    The onboarding wizard collects:
      * ``owner_names``  — required for import (transactions need owners)
      * ``ui_language``  — controls all i18n strings
      * a default taxonomy applied to the user's catalogue
      * at least one account
      * (eventually) real imported transactions

    Skipping the wizard is only safe when the user has actually used the app
    before and ALL of those signals are present. Checking ``taxonomy_category``
    alone (the previous heuristic) was wrong because the default taxonomy is
    auto-seeded on every fresh install — every new user was silently flagged
    as "already onboarded" (#AI-58).

    Conditions to skip (ALL must hold):
      1. ``ui_language`` setting is set         — language pref configured
      2. ``owner_names`` setting is non-empty  — required for import
      3. at least one ``account`` row exists   — accounts step completed
      4. ``llm_backend`` setting is non-empty  — LLM defaults configured

    Note that the presence of ``transaction`` rows is NOT a prerequisite:
    a user can legitimately complete the wizard and only later import data.

    Does nothing if ``onboarding_done`` is already set explicitly (true or
    false) — that's a deliberate user/test override, not a heuristic call.
    """
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        existing = conn.execute(_text(
            "SELECT value FROM user_settings WHERE key='onboarding_done'"
        )).scalar()
        if existing is not None:
            return  # already decided — don't touch

        def _setting(key: str) -> str | None:
            return conn.execute(_text(
                "SELECT value FROM user_settings WHERE key = :k"
            ), {"k": key}).scalar()

        owner_names = (_setting("owner_names") or "").strip()
        ui_language = (_setting("ui_language") or "").strip()
        llm_backend = (_setting("llm_backend") or "").strip()
        if not owner_names or not ui_language or not llm_backend:
            return

        try:
            acct_count = conn.execute(_text("SELECT COUNT(*) FROM account")).scalar() or 0
        except Exception:
            acct_count = 0
        if acct_count <= 0:
            return

        conn.execute(_text(
            "INSERT OR REPLACE INTO user_settings (key, value) "
            "VALUES ('onboarding_done', 'true')"
        ))
        conn.commit()


def _migrate_add_header_sha256(engine) -> None:
    """Add header_sha256 column to document_schema if not already present."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text("ALTER TABLE document_schema ADD COLUMN header_sha256 VARCHAR(64)"))
            conn.execute(_text("CREATE INDEX IF NOT EXISTS ix_document_schema_header_sha256 ON document_schema (header_sha256)"))
            conn.commit()
        except Exception:
            pass  # column already exists


def _migrate_counterparts_written_one_way(engine) -> None:
    """Rewrite the saved counterparts the way every new one is written
    (written_counterpart, spec 21): before 2026-10-21 the import saved
    them in upper case. Only the rows that differ are touched; idempotent."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            rows = conn.execute(_text(
                'SELECT id, description FROM "transaction" WHERE description IS NOT NULL'
            )).fetchall()
        except Exception:
            return  # no transaction table yet
        changed = [(written_counterpart(d), i) for i, d in rows if written_counterpart(d) != d]
        for new, tx_id in changed:
            conn.execute(_text('UPDATE "transaction" SET description = :d WHERE id = :i'),
                         {"d": new, "i": tx_id})
        if changed:
            conn.commit()


def _migrate_giroconti_without_category(engine) -> None:
    """Empty the category of the giroconti that kept the one they had before
    becoming giroconti (before 2026-10-21). The product's proposal stays in
    model_category; idempotent."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text(
                'UPDATE "transaction" SET category = NULL, subcategory = NULL, '
                'category_source = NULL, category_confidence = NULL, category_model = NULL '
                "WHERE tx_type IN ('internal_out', 'internal_in') AND category IS NOT NULL"
            ))
            conn.commit()
        except Exception:
            pass  # no transaction table yet


def _migrate_add_category_color(engine) -> None:
    """Add taxonomy_category.color and give every category without one its
    colour, once, in sort order (spec 25)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text('ALTER TABLE taxonomy_category ADD COLUMN color VARCHAR(7)'))
            conn.commit()
        except Exception as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
        fill_category_colors(conn)
        conn.commit()


def fill_category_colors(conn) -> int:
    """Give each category without a colour the first free colour of its
    direction's palette (spec 25). Colours already set, by the product
    or by the user, are never touched. Returns how many were assigned."""
    from sqlalchemy import text as _text
    from core.palette import next_color
    rows = conn.execute(_text(
        "SELECT id, type, color FROM taxonomy_category "
        "WHERE COALESCE(is_fallback, 0) = 0 ORDER BY type, sort_order, id"
    )).fetchall()
    used: dict[str, list[str]] = {}
    for _, type_, color in rows:
        if color:
            used.setdefault(type_, []).append(color)
    assigned = 0
    for cat_id, type_, color in rows:
        if color:
            continue
        new = next_color(type_, used.get(type_, []))
        used.setdefault(type_, []).append(new)
        conn.execute(_text("UPDATE taxonomy_category SET color = :c WHERE id = :i"),
                     {"c": new, "i": cat_id})
        assigned += 1
    return assigned


def _migrate_add_transaction_model_proposal(engine) -> None:
    """Add the columns of the product's proposal to transaction if missing."""
    from sqlalchemy import text as _text
    for column, kind in (("model_category", "VARCHAR(128)"),
                         ("model_subcategory", "VARCHAR(128)"),
                         ("model_confidence", "VARCHAR(10)")):
        with engine.connect() as conn:
            try:
                conn.execute(_text(f'ALTER TABLE "transaction" ADD COLUMN {column} {kind}'))
                conn.commit()
            except Exception:
                pass  # column already exists


def _migrate_add_import_job_n_rows(engine) -> None:
    """Add n_rows column to import_job if not already present."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text("ALTER TABLE import_job ADD COLUMN n_rows INTEGER"))
            conn.commit()
        except Exception:
            pass  # column already exists


def _migrate_add_transfer_link_decision(engine) -> None:
    """Add user_decision column to internal_transfer_link if not already present."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text(
                "ALTER TABLE internal_transfer_link ADD COLUMN user_decision VARCHAR(8)"))
            conn.commit()
        except Exception:
            pass  # column already exists


def _migrate_add_account_closed_on(engine) -> None:
    """Add closed_on column to account if not already present."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text("ALTER TABLE account ADD COLUMN closed_on VARCHAR(10)"))
            conn.commit()
        except Exception:
            pass  # column already exists


def _migrate_add_confidence_score(engine) -> None:
    """Add confidence_score column to document_schema if not already present."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text("ALTER TABLE document_schema ADD COLUMN confidence_score FLOAT"))
            conn.commit()
        except Exception:
            pass  # column already exists


def _migrate_add_transaction_updated_at(engine) -> None:
    """Add updated_at column to transaction table if not already present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text('ALTER TABLE "transaction" ADD COLUMN updated_at DATETIME'))
            conn.commit()
        except Exception as exc:
            if "duplicate column name" in str(exc).lower():
                pass  # column already exists
            else:
                raise


def _migrate_add_classification_tracking(engine) -> None:
    """Add human_validated and validated_at to transaction (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        for col_sql in [
            'ALTER TABLE "transaction" ADD COLUMN human_validated BOOLEAN DEFAULT 0',
            'ALTER TABLE "transaction" ADD COLUMN validated_at DATETIME',
        ]:
            try:
                conn.execute(_text(col_sql))
                conn.commit()
            except Exception as exc:
                if "duplicate column name" in str(exc).lower():
                    pass
                else:
                    raise


def _migrate_add_account_type(engine) -> None:
    """Add account_type column to account table if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text(
                'ALTER TABLE account ADD COLUMN account_type VARCHAR(32)'
            ))
            conn.commit()
        except Exception as exc:
            if "duplicate column name" in str(exc).lower():
                pass  # column already exists
            else:
                raise


def _migrate_add_taxonomy_fallback(engine) -> None:
    """Add is_fallback to taxonomy_category and taxonomy_default, mark Altro as fallback (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        for table in ["taxonomy_category", "taxonomy_default"]:
            try:
                conn.execute(_text(f'ALTER TABLE {table} ADD COLUMN is_fallback BOOLEAN DEFAULT 0'))
                conn.commit()
            except Exception as exc:
                if "duplicate column name" in str(exc).lower():
                    pass
                else:
                    raise
        # Mark the fallback categories of every language, as the built-in
        # taxonomies define them (before 2026-10-21 only it/en were marked,
        # so fr/de/es had no fallback after onboarding).
        from db.taxonomy_defaults import TAXONOMY_DEFAULTS
        for data in TAXONOMY_DEFAULTS.values():
            for type_key, section in (("expense", "expenses"), ("income", "income")):
                for entry in data.get(section, []):
                    if entry.get("is_fallback"):
                        params = {"n": entry["category"], "t": type_key}
                        conn.execute(_text('UPDATE taxonomy_category SET is_fallback = 1 '
                                           'WHERE name = :n AND type = :t'), params)
                        conn.execute(_text('UPDATE taxonomy_default SET is_fallback = 1 '
                                           'WHERE category = :n AND type = :t'), params)
        conn.commit()


def _migrate_add_account_usage(engine) -> None:
    """Add the usage column to account (idempotent). Left empty on purpose:
    the old account_type defaulted to bank_account for every account, so it
    says nothing about how an account is used and is not carried over."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text("ALTER TABLE account ADD COLUMN usage VARCHAR(16)"))
            conn.commit()
        except Exception as exc:
            if "duplicate column name" not in str(exc).lower():
                raise


def _migrate_consolidate_account_type(engine) -> None:
    """Revert 'card' back to 'debit_card' (undo previous merge, idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            "UPDATE account SET account_type = 'debit_card' "
            "WHERE account_type = 'card'"
        ))
        conn.commit()


def _migrate_savings_to_savings_account(engine) -> None:
    """Rename doc_type 'savings' to 'savings_account' everywhere (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            "UPDATE document_schema SET doc_type = 'savings_account' "
            "WHERE doc_type = 'savings'"
        ))
        conn.execute(_text(
            'UPDATE "transaction" SET doc_type = \'savings_account\' '
            "WHERE doc_type = 'savings'"
        ))
        conn.commit()


def _migrate_add_footer_patterns(engine) -> None:
    """Add footer_patterns column to document_schema if not already present."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text("ALTER TABLE document_schema ADD COLUMN footer_patterns TEXT"))
            conn.commit()
        except Exception:
            pass  # column already exists


def _migrate_add_has_borders(engine) -> None:
    """Add has_borders column to document_schema if not already present."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text(
                "ALTER TABLE document_schema ADD COLUMN has_borders BOOLEAN DEFAULT 0"
            ))
            conn.commit()
        except Exception:
            pass  # column already exists


def _migrate_add_budget_target(engine) -> None:
    """Create budget_target table if not present (idempotent, A-02)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS budget_target ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'category VARCHAR(128) NOT NULL UNIQUE, '
            'target_pct NUMERIC(5,2) NOT NULL, '
            'period_type VARCHAR(16) DEFAULT "monthly", '
            'created_at DATETIME, '
            'updated_at DATETIME)'
        ))
        conn.commit()


def _migrate_add_nsi_tag_mapping(engine) -> None:
    """Create nsi_tag_mapping table if not present (idempotent, C-08-cascade)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS nsi_tag_mapping ('
            'osm_tag VARCHAR(128) PRIMARY KEY, '
            'category VARCHAR(128) NOT NULL, '
            'subcategory VARCHAR(128) NOT NULL, '
            'taxonomy_hash VARCHAR(64) NOT NULL, '
            'updated_at DATETIME)'
        ))
        conn.commit()


def _migrate_add_category_model(engine) -> None:
    """Add category_model column to track which LLM model categorized each tx."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        try:
            conn.execute(_text(
                'ALTER TABLE "transaction" ADD COLUMN category_model VARCHAR(128)'
            ))
            conn.commit()
        except Exception as exc:
            if "duplicate column name" in str(exc).lower():
                pass
            else:
                raise


def _migrate_add_category_correction(engine) -> None:
    """Create category_correction table for live implicit benchmark (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS category_correction ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'tx_id VARCHAR(64) NOT NULL, '
            'original_category VARCHAR(128), '
            'original_subcategory VARCHAR(128), '
            'original_source VARCHAR(10), '
            'original_model VARCHAR(128), '
            'original_confidence VARCHAR(10), '
            'new_category VARCHAR(128), '
            'new_subcategory VARCHAR(128), '
            'consistency_at_correction FLOAT, '
            'correction_origin VARCHAR(20), '
            'corrected_at DATETIME)'
        ))
        conn.execute(_text(
            'CREATE INDEX IF NOT EXISTS ix_category_correction_tx_id '
            'ON category_correction (tx_id)'
        ))
        conn.commit()


def _migrate_add_llm_usage_log(engine) -> None:
    """Create llm_usage_log table if not present (idempotent)."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        conn.execute(_text(
            'CREATE TABLE IF NOT EXISTS llm_usage_log ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'timestamp DATETIME, '
            'backend VARCHAR(30) NOT NULL, '
            'model_id VARCHAR(120) NOT NULL, '
            'caller VARCHAR(30) NOT NULL, '
            'step VARCHAR(60), '
            'source_name VARCHAR(256), '
            'batch_size INTEGER, '
            'prompt_tokens INTEGER DEFAULT 0, '
            'completion_tokens INTEGER DEFAULT 0, '
            'total_tokens INTEGER DEFAULT 0, '
            'n_ctx INTEGER, '
            'duration_ms INTEGER)'
        ))
        conn.commit()


def _migrate_purge_orphan_schemas(engine) -> None:
    """Delete cached document_schema rows that have no header_sha256.

    These orphans are unreachable by the fast header-hash lookup path
    and may contain stale/incorrect mappings (e.g. wrong date_format)
    that cause 100% parse failures on re-import.  Idempotent: only
    deletes rows where header_sha256 IS NULL or empty string.
    """
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        result = conn.execute(_text(
            "DELETE FROM document_schema "
            "WHERE header_sha256 IS NULL OR header_sha256 = ''"
        ))
        if result.rowcount:
            import logging
            logging.getLogger("SPENDIFY").info(
                "migrate: purged %d orphan document_schema row(s) without header_sha256",
                result.rowcount,
            )
        conn.commit()
