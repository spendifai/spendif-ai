"""ExpenseConfirmService: the ledger side of confirming an expense (spec 32).

The flow is fixed and lives in core.expense_confirm; this service gives it
its ports: the outflows to search, the taxonomy, the model's one short call,
and the writes of spec 32, 5. It never creates a movement.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import sessionmaker

from core.expense_confirm import WINDOW_DAYS, Candidate, Msg, ReceiptMatch, SiriItem, State, start, turn
from core.siri_inbox import APP_ENTRY_PREFIX, INBOX_NAME, UNCLEAR_NAME, SiriReceipt, read_line
from core.spoken_expense import new_part
from db import repository
from db.models import PendingReceipt, Transaction
from services.rule_service import RuleService
from services.transaction_service import NOT_COUNTED_TYPES, TransactionService
from support.logging import setup_logging

logger = setup_logging()

# Amounts are kept with four decimals: half a cent either side is the same cent.
_HALF_CENT = 0.005
# Below about 3B parameters the model misreads the answers that matter (spec
# 32, 4.8, measured): a 3B Q4 file is 1.9 GB, a 1.5B one 1.0 GB.
_MIN_INTENT_MODEL_BYTES = 1_500_000_000
# The labels of the measured prompt, in the user's words, and the flow's.
_INTENT_LABELS = {"conferma": "yes", "categoria_diversa": "yes_other", "rifiuta": "no",
                  "chiudi": "close", "correggi": "correct", "altro": "unclear"}
_INTENT_DESC = {"conferma": "dice di si', che e' giusto",
                "categoria_diversa": "dice che e' quel movimento ma la categoria e' sbagliata",
                "rifiuta": "dice di no, che non e' quello",
                "chiudi": "vuole smettere, chiudere per oggi",
                "correggi": "vuole cambiare la categoria della spesa appena confermata",
                "altro": "qualsiasi altra cosa, o non e' chiaro"}
_INTENT_QUESTION = {"found": "Esselunga, Alimentari / Spesa supermercato?",
                    "always": "Sempre cosi' per Esselunga?", "waiting": "Fatto."}


class SiriReport(NamedTuple):
    """What reading the Siri file did (spec 32, 4.9).

    Attributes:
        saved: lines that became a cash movement or a saved receipt.
        unclear: sentences kept to complete.
        notes: one (i18n key, values) per line, for the box above the chat.
        confirm: the expenses whose category the user still has to confirm
            (the model proposed it, or there is none), as SiriItem.
        receipts: the ids of the receipts saved, waiting for the statement:
            the page marks them new in its list.
    """
    saved: int
    unclear: int
    notes: list
    confirm: list = []
    receipts: tuple = ()


class ExpenseConfirmService:
    def __init__(self, engine, rule_writer=None, backend=None, today: date | None = None) -> None:
        """*rule_writer* writes the rule after a yes to "Sempre cosi'?" and
        returns the note to show; the page gives the one the Registry uses."""
        self.engine = engine
        self._Session = sessionmaker(bind=engine, expire_on_commit=False)
        self._rule_writer = rule_writer
        self._backend = backend
        self._today = today

    @contextmanager
    def _session(self):
        s = self._Session()
        try:
            yield s
        finally:
            s.close()

    # ── The flow ──────────────────────────────────────────────────────────────

    def start(self, siri: list | tuple = ()) -> tuple[list[Msg], State]:
        """The first turn: the categories of the expenses from Siri to
        confirm (spec 32, 4.9), then the receipts saved that now have a
        movement (4.4bis).

        Args:
            siri: SiriItem from collect_siri, the expenses whose category the
                user still has to confirm.
        """
        return start(self.receipt_matches(), siri)

    def reply(self, state: State, text: str) -> tuple[list[Msg], State]:
        return turn(state, text, self, self._today or date.today())

    @staticmethod
    def new_part(text: str, sent: list[str]) -> str:
        """The part of a dictation after the answers already sent."""
        return new_part(text, sent)

    # ── Ports ─────────────────────────────────────────────────────────────────

    def outflows(self, low: Decimal, high: Decimal, date_from: date, date_to: date,
                 checked: bool) -> list[Candidate]:
        """Outflows between two amounts (given positive) and two dates, the
        checked ones or the others. Transfers between the user's own accounts
        and card settlements are not expenses: they stay out."""
        with self._session() as s:
            q = s.query(Transaction).filter(
                Transaction.amount <= -(float(low) - _HALF_CENT),
                Transaction.amount >= -(float(high) + _HALF_CENT),
                Transaction.date >= date_from.isoformat(),
                Transaction.date <= date_to.isoformat(),
                or_(Transaction.tx_type.is_(None), Transaction.tx_type.notin_(NOT_COUNTED_TYPES)),
            )
            if checked:
                q = q.filter(Transaction.human_validated.is_(True))
            else:
                q = q.filter(Transaction.human_validated.isnot(True))
            return [Candidate(tx_id=tx.id, day=date.fromisoformat(tx.date),
                              amount=abs(Decimal(str(tx.amount))).quantize(Decimal("0.01")),
                              counterpart=tx.description or "", category=tx.category or None,
                              subcategory=tx.subcategory or "")
                    for tx in q.all()]

    def last_date(self) -> date | None:
        """The day of the latest movement in the ledger: after it, an expense
        cannot be there yet (spec 32, 4.2 C 2)."""
        with self._session() as s:
            last = s.query(func.max(Transaction.date)).scalar()
        return date.fromisoformat(last) if last else None

    def category_pairs(self) -> list[tuple[str, str]]:
        """Every expense category with each of its subcategories, in the
        taxonomy's order."""
        with self._session() as s:
            taxonomy = repository.get_taxonomy_config(s)
        return [(cat, sub) for cat, subs in taxonomy.expenses.items() for sub in (subs or [""])]

    def income_pairs(self) -> list[tuple[str, str]]:
        """Every income category with each of its subcategories, in the
        taxonomy's order: where a refund's category is chosen (spec 32, 4.10)."""
        with self._session() as s:
            taxonomy = repository.get_taxonomy_config(s)
        return [(cat, sub) for cat, subs in taxonomy.income.items() for sub in (subs or [""])]

    def default_refund_pair(self) -> tuple[str, str] | None:
        """The refund category of the Settings (setting refund_category,
        "category|subcategory"; "" for none, so the chat asks), when it is
        still one of the income ones. Never chosen: the shipped default of
        the interface language, if the user's taxonomy still has it
        (spec 32, 4.10)."""
        from db.taxonomy_defaults import REFUND_DEFAULTS
        from services.settings_service import SettingsService
        cfg = SettingsService(self.engine)
        raw = cfg.get("refund_category")
        if raw is None:
            pair = REFUND_DEFAULTS.get(cfg.get("ui_language", "it") or "it")
        else:
            pair = tuple(raw.split("|", 1)) if "|" in raw else None
        return pair if pair and pair in self.income_pairs() else None

    def _settings_unless_importing(self) -> dict | None:
        """The settings, or None while an import runs: the local model runs in
        this process, and the import comes first."""
        with self._session() as s:
            settings = repository.get_all_user_settings(s)
            job = repository.get_latest_import_job(s)
        return None if job is not None and job.status == "running" else settings

    def _categories_backend(self, settings: dict):
        """The model of the categories, the larger of the two (spec 32, 4.8)."""
        if self._backend is not None:
            return self._backend
        from core.orchestrator import _build_backend, _build_categorizer_backend
        from services.category_service import CategoryService
        config = CategoryService._config_from_settings(settings)
        return _build_categorizer_backend(config) or _build_backend(config)

    def model_intent(self, kind: str, text: str) -> str | None:
        """What an answer the words did not understand means, among the
        intents possible after the question asked (spec 32, 4.8), or None:
        model too small, import running, model failing. The flow says it
        back and waits for a yes; the text never reaches the log."""
        import json
        from pathlib import Path

        from core.expense_confirm import INTENTS
        from core.llm_backends import call_with_fallback

        logger.info(f"expense_confirm: answer not understood by the words ({kind}), asking the model")
        settings = self._settings_unless_importing()
        if settings is None:
            return None
        try:
            backend = self._categories_backend(settings)
            size = getattr(backend, "model_size_bytes", None)    # a hosted model has none: large
            if size is not None and size < _MIN_INTENT_MODEL_BYTES:
                logger.info("expense_confirm: model too small for the answers, not asked")
                return None
            allowed = [lab for lab, intent in _INTENT_LABELS.items() if intent in INTENTS[kind]]
            prompts = json.loads((Path(__file__).parent.parent / "prompts" / "answer_intent.json")
                                 .read_text(encoding="utf-8"))
            raw, _ = call_with_fallback(
                primary=backend,
                system_prompt=prompts["system"].format(
                    labels="\n".join(f"- {lab}: {_INTENT_DESC[lab]}" for lab in allowed)),
                user_prompt=prompts["user_template"].format(question=_INTENT_QUESTION[kind], answer=text),
                json_schema={"type": "object",
                             "properties": {"intento": {"type": "string", "enum": allowed}},
                             "required": ["intento"]},
                caller="expense_confirm",
                step="expense_answer",
            )
        except Exception as exc:
            logger.warning(f"expense_confirm: model failed on the answer, the words decide: {exc}")
            return None
        label = (raw or {}).get("intento") if isinstance(raw, dict) else None
        intent = _INTENT_LABELS.get(label) if label in allowed else None
        logger.info(f"expense_confirm: the model read the answer as {intent}")
        return intent

    def model_pick(self, text: str, labels: list[str]) -> int | None:
        """The index of the category the model finds for the user's words, or
        None. One short call; never during an import (the local model runs
        in this process) and never failing the flow: the words decide then."""
        import json
        from pathlib import Path

        from core.llm_backends import call_with_fallback

        settings = self._settings_unless_importing()
        if settings is None:
            logger.info("expense_confirm: import running, the category is found by the words")
            return None
        try:
            backend = self._categories_backend(settings)
            prompts = json.loads((Path(__file__).parent.parent / "prompts" / "category_from_words.json")
                                 .read_text(encoding="utf-8"))
            items = "\n".join(f"{i}: {lab}" for i, lab in enumerate(labels))
            raw, _ = call_with_fallback(
                primary=backend,
                system_prompt=prompts["system"].format(language=settings.get("ui_language", "it")),
                user_prompt=prompts["user_template"].format(text=text, items=items),
                json_schema={"type": "object", "properties": {"index": {"type": "integer"}},
                             "required": ["index"]},
                caller="expense_confirm",
                step="expense_category",
            )
        except Exception as exc:
            logger.warning(f"expense_confirm: model failed, the category is found by the words: {exc}")
            return None
        index = (raw or {}).get("index") if isinstance(raw, dict) else None
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(labels):
            return index
        return None

    def confirm(self, tx_id: str, pair: tuple[str, str] | None) -> list[dict]:
        """Spec 32, 5: the movement checked ("Controllato da te"); with a
        category, that category by hand first. Returns the "always like
        this?" questions the choice by hand opens (spec 18, 5.2)."""
        tx_svc = TransactionService(self.engine)
        if pair is not None:
            tx_svc.update_category(tx_id, pair[0], pair[1], origin="expense_confirm")
        tx_svc.validate(tx_id)
        logger.info(f"expense_confirm: {tx_id} checked{', category by hand' if pair else ''}")
        return RuleService(self.engine).always_questions([tx_id]) if pair is not None else []

    def always_yes(self, question: dict) -> str | None:
        if self._rule_writer is None:
            raise RuntimeError("no rule writer given")
        return self._rule_writer(question)

    def always_no(self, question: dict) -> None:
        RuleService(self.engine).decline_always(question)

    # ── 4.4bis Receipts waiting for their movement ───────────────────────────

    def save_receipt(self, day: date, amount: Decimal, what: str, pair: tuple[str, str] | None,
                     cash: bool = False, kind: str = "expense", cash_tx_id: str | None = None) -> int:
        """Keep a receipt whose movement is not imported yet: date, amount,
        the user's description and category (spec 32, 4.4bis).

        Args:
            day: the day said.
            amount: positive; *kind* gives the direction.
            what: the user's own words.
            pair: (category, subcategory), or None without one.
            cash: True when paid in cash: never matched to a statement.
            kind: "expense", "refund" (an inflow) or "withdrawal" (the bank
                side of a cash withdrawal) (spec 32, 4.10).
            cash_tx_id: for a withdrawal, the cash account's movement already
                written; None until the cash account exists.
        """
        with self._session() as s:
            s.add(PendingReceipt(date=day.isoformat(), amount=amount, description=what,
                                 category=pair[0] if pair else "", subcategory=(pair[1] or "") if pair else "",
                                 paid_cash=cash, kind=kind, cash_tx_id=cash_tx_id))
            s.commit()
            new_id = s.query(func.max(PendingReceipt.id)).scalar()
        logger.info("expense_confirm: receipt saved for the next import")
        return new_id

    def pending_receipts(self) -> list[dict]:
        """The receipts still waiting, oldest day first: id, day, amount,
        what, cash, category, subcategory, kind ("expense", "refund",
        "withdrawal") and, for a withdrawal, the cash movement (cash_tx_id)."""
        with self._session() as s:
            rows = (s.query(PendingReceipt).filter(PendingReceipt.matched_tx_id.is_(None))
                    .order_by(PendingReceipt.date, PendingReceipt.id).all())
            return [{"id": r.id, "day": date.fromisoformat(r.date),
                     "amount": Decimal(str(r.amount)).quantize(Decimal("0.01")), "what": r.description or "",
                     "cash": bool(r.paid_cash),
                     "category": r.category, "subcategory": r.subcategory or "",
                     "kind": r.kind or "expense", "cash_tx_id": r.cash_tx_id} for r in rows]

    def delete_receipt(self, receipt_id: int) -> None:
        """Forget a receipt that waits: the user no longer wants it found.

        Args:
            receipt_id: the PendingReceipt id.
        """
        with self._session() as s:
            s.query(PendingReceipt).filter(PendingReceipt.id == receipt_id,
                                           PendingReceipt.matched_tx_id.is_(None)).delete()
            s.commit()

    def receipt_matches(self) -> list[ReceiptMatch]:
        """The receipts waiting that now have at least one unchecked movement
        with their amount, within the window of spec 32, 4.2: what the
        conversation offers to check at its start. An expense looks among the
        outflows, a refund among the inflows, a withdrawal among the outflows
        of the other accounts, transfers not yet linked included (4.10).
        Nothing is written."""
        window = timedelta(days=WINDOW_DAYS)
        matches = []
        for r in self.pending_receipts():
            if r["cash"]:
                continue                  # cash never reaches a statement (spec 32, 4.4bis)
            span = (r["amount"], r["amount"], r["day"] - window, r["day"] + window)
            if r["kind"] == "refund":
                found = self.inflows(*span)
            elif r["kind"] == "withdrawal":
                found = self.withdrawal_outflows(*span)
            else:
                found = self.outflows(*span, False)
            if found:
                found.sort(key=lambda c: (abs((c.day - r["day"]).days), c.day, c.tx_id))
                pair = (r["category"], r["subcategory"]) if r["category"] else None
                matches.append(ReceiptMatch(receipt_id=r["id"], day=r["day"], amount=r["amount"],
                                            what=r["what"], pair=pair, candidates=tuple(found)))
        return matches

    def inflows(self, low: Decimal, high: Decimal, date_from: date, date_to: date) -> list[Candidate]:
        """Unchecked inflows between two amounts and two dates: where a refund
        paid back on the card appears (spec 32, 4.10). Transfers stay out."""
        with self._session() as s:
            q = s.query(Transaction).filter(
                Transaction.amount >= float(low) - _HALF_CENT, Transaction.amount <= float(high) + _HALF_CENT,
                Transaction.date >= date_from.isoformat(), Transaction.date <= date_to.isoformat(),
                or_(Transaction.tx_type.is_(None), Transaction.tx_type.notin_(NOT_COUNTED_TYPES)),
                Transaction.human_validated.isnot(True))
            return [self._candidate(tx) for tx in q.all()]

    def withdrawal_outflows(self, low: Decimal, high: Decimal, date_from: date, date_to: date) -> list[Candidate]:
        """Unchecked outflows of the accounts other than the cash one, between
        two amounts and two dates, the ones already typed as a transfer but
        not yet linked included: where the bank side of a cash withdrawal
        appears (spec 32, 4.10)."""
        from db.models import InternalTransferLink
        cash = self.cash_account()
        with self._session() as s:
            linked = {row[0] for row in s.query(InternalTransferLink.out_id)}
            q = s.query(Transaction).filter(
                Transaction.amount <= -(float(low) - _HALF_CENT), Transaction.amount >= -(float(high) + _HALF_CENT),
                Transaction.date >= date_from.isoformat(), Transaction.date <= date_to.isoformat(),
                or_(Transaction.tx_type.is_(None), Transaction.tx_type.notin_(("card_settlement", "aggregate_debit",
                                                                               "internal_in"))),
                Transaction.human_validated.isnot(True))
            if cash:
                q = q.filter(Transaction.account_label != cash)
            return [self._candidate(tx) for tx in q.all() if tx.id not in linked]

    @staticmethod
    def _candidate(tx) -> Candidate:
        """A movement as the conversation shows it."""
        return Candidate(tx_id=tx.id, day=date.fromisoformat(tx.date),
                         amount=abs(Decimal(str(tx.amount))).quantize(Decimal("0.01")),
                         counterpart=tx.description or "", category=tx.category or None,
                         subcategory=tx.subcategory or "")

    def confirm_receipt(self, receipt_id: int, tx_id: str, pair: tuple[str, str] | None) -> list[dict]:
        """The user's yes: the movement checked, with the receipt's category
        by hand when *pair* is given, and the receipt no longer waiting.
        Returns the "always like this?" questions of a change by hand, as
        confirm does."""
        with self._session() as s:
            kind, cash_tx = s.get(PendingReceipt, receipt_id).kind, s.get(PendingReceipt, receipt_id).cash_tx_id
        if kind == "withdrawal":
            self._link_withdrawal(tx_id, cash_tx)
            questions = []
        else:
            questions = self.confirm(tx_id, pair)
        with self._session() as s:
            row = s.get(PendingReceipt, receipt_id)
            row.matched_tx_id, row.matched_at = tx_id, datetime.now(timezone.utc)
            s.commit()
        return questions

    def _link_withdrawal(self, bank_tx: str, cash_tx: str | None) -> None:
        """The user's yes on a withdrawal: the bank's movement becomes a
        checked transfer without category, linked to the cash one like every
        transfer, the decision already "yes" (spec 32, 4.10).

        Args:
            bank_tx: the bank's outflow the user picked.
            cash_tx: the cash account's inflow; None when the cash account did
                not exist yet: the bank side is still made a transfer.
        """
        from db.models import InternalTransferLink
        with self._session() as s:
            tx = s.get(Transaction, bank_tx)
            tx.tx_type, tx.category, tx.subcategory = "internal_out", None, None
            tx.human_validated, tx.validated_at = True, datetime.now(timezone.utc)
            if cash_tx:
                s.add(InternalTransferLink(out_id=bank_tx, in_id=cash_tx, confidence="high", user_decision="yes"))
            s.commit()
        logger.info("expense_confirm: withdrawal linked as a transfer")

    # ── 4.9 The expenses dictated to Siri ────────────────────────────────────

    # The Shortcuts folder in iCloud Drive, where the command's file actions write.
    _SHORTCUTS_ICLOUD = Path("Library") / "Mobile Documents" / "iCloud~is~workflow~my~workflows" / "Documents"

    # The iPhone app's folder in iCloud Drive (spec 34, 4), read with no setting:
    # on macOS the app's container; on Windows the same folder as iCloud for
    # Windows names it, under either name it may take there (to verify on a
    # real Windows). Linux has no iCloud: only the folder set in Settings.
    _APP_ICLOUD = Path("Library") / "Mobile Documents" / "iCloud~ai~spendif~ios" / "Documents"
    _APP_ICLOUD_WINDOWS = (Path("iCloudDrive") / "iCloud~ai~spendif~ios", Path("iCloudDrive") / "Spendif.ai")

    @staticmethod
    def phone_folder(app_folder: str = "") -> Path | None:
        """The folder the iPhone app writes to, as this desktop system sees
        it (spec 34, 4); None when there is none to read.

        Args:
            app_folder: the folder set in Settings (setting siri_app_folder),
                shared between accounts or synced by other means; "" for the
                default of this system.
        """
        if app_folder:
            return Path(app_folder).expanduser()
        if sys.platform == "darwin":
            return Path.home() / ExpenseConfirmService._APP_ICLOUD
        if sys.platform == "win32":
            found = [Path.home() / p for p in ExpenseConfirmService._APP_ICLOUD_WINDOWS if (Path.home() / p).is_dir()]
            return found[0] if found else None
        return None

    @staticmethod
    def siri_files(enabled: bool, folder: str = "Spendif.ai", app_folder: str = "") -> list[Path]:
        """Where the dictated entries are (spec 32, 4.9 and spec 34, 4): on a
        Mac, the command's file in the Shortcuts folder of iCloud Drive once
        the command is installed, and Documents/Spendif.ai for a command made
        by hand; on every system, the iPhone app's entries, one file each.

        Args:
            enabled: True once the user installed the command (setting
                siri_enabled).
            folder: the folder name chosen in Settings (setting siri_folder).
            app_folder: the folder the iPhone apps write to, when shared
                between accounts or synced by other means (setting
                siri_app_folder); "" for the default of this system.
        """
        files = []
        if sys.platform == "darwin":             # the Shortcuts command lives on a Mac only
            files.append(Path.home() / "Documents" / "Spendif.ai" / INBOX_NAME)
            if enabled:
                files.append(Path.home() / ExpenseConfirmService._SHORTCUTS_ICLOUD / (folder or "Spendif.ai")
                             / INBOX_NAME)
        entries = ExpenseConfirmService.phone_folder(app_folder)
        if entries is not None and entries.is_dir():
            # The folder's own inbox name anchors it in the list even with no entry
            # left, so the sentences to complete kept there are still found.
            files.append(entries / INBOX_NAME)
            files += sorted(entries.glob(f"{APP_ENTRY_PREFIX}*.txt"))
        return files

    @staticmethod
    def siri_default_name(language: str) -> str:
        """The Siri command's default name in *language*: the phrase to say,
        without the app's name, which made Siri open the app (spec 32, 4.9).

        Args:
            language: the interface language code.
        """
        from core.siri_shortcut import default_name
        return default_name(language)

    @staticmethod
    def install_siri_shortcut(folder: str, language: str, name: str = "") -> list[Path]:
        """Build the command with *folder*, sign it on this Mac and open it:
        Shortcuts shows it and the user adds it with one click (spec 32,
        4.9). A command answers one phrase only, so several phrases are
        several identical commands, one click each. Returns the signed files.

        Args:
            folder: the folder in the Shortcuts folder of iCloud Drive.
            language: the interface language, for the dictation and the
                question.
            name: the phrase said to Siri, or several separated by commas
                ("Segna spesa, Registra scontrino"); "" for the default.

        Raises:
            RuntimeError: not a Mac, or the signing failed.
        """
        import subprocess
        import tempfile

        from core.siri_shortcut import build, default_name
        if sys.platform != "darwin":
            raise RuntimeError("the Siri command exists on macOS only")
        out_dir = Path.home() / ".spendifai" / "siri"
        out_dir.mkdir(parents=True, exist_ok=True)
        names = [n.replace("/", " ").strip() for n in (name or default_name(language)).split(",")]
        seen: set[str] = set()
        names = [n for n in names if n and not (n.lower() in seen or seen.add(n.lower()))] or [default_name(language)]
        signed_files = []
        for one in names:
            signed = out_dir / f"{one}.shortcut"             # the name Shortcuts shows is the file's
            with tempfile.TemporaryDirectory() as tmp:
                unsigned = Path(tmp) / f"{one}.shortcut"
                unsigned.write_bytes(build(folder, language))
                done = subprocess.run(["shortcuts", "sign", "--mode", "anyone", "--input", str(unsigned),
                                       "--output", str(signed)], capture_output=True, text=True, timeout=120)
            if done.returncode != 0 or not signed.exists():
                raise RuntimeError(f"signing the Siri command failed: {done.stderr.strip()[:200]}")
            subprocess.run(["open", str(signed)], check=False)
            signed_files.append(signed)
        logger.info(f"expense_confirm: {len(signed_files)} Siri commands built, signed and opened")
        return signed_files

    def collect_siri(self, files: list[Path], today: date | None = None) -> SiriReport:
        """Read the lines the command wrote and empty the files: each line a
        cash movement, a saved receipt, or a sentence to complete kept next
        to the file. Returns how many of each and, for the conversation, one
        note per line (spec 32, 4.9).

        Args:
            files: the inbox files, as siri_files gives them.
            today: the day for a line without its date; None for today.
        """
        pairs = self.category_pairs()
        saved = unclear = 0
        notes: list[tuple[str, dict]] = []
        confirm: list[SiriItem] = []
        receipts: list[int] = []
        for path in files:
            entry = path.name.startswith(APP_ENTRY_PREFIX)
            if not entry:
                path.parent.mkdir(parents=True, exist_ok=True)     # the command needs the folder to exist
            if not path.exists():
                continue
            if entry:
                # One file per entry from the iPhone app: read, and deleted only
                # once its line is saved (spec 34, 4).
                content = path.read_text(encoding="utf-8")
            else:
                # Emptied in place, never moved nor deleted: the permission the user
                # gives Shortcuts holds for this file, and a new file asked it again
                # at every expense (founder, 2026-10-27).
                with open(path, "r+", encoding="utf-8") as f:
                    content = f.read()
                    f.seek(0)
                    f.truncate()
            kept = []
            for line in content.splitlines():
                got = read_line(line, pairs, today or self._today or date.today())
                if isinstance(got, SiriReceipt) and got.kind != "expense":
                    receipts += self._siri_refund_or_withdrawal(got, notes, confirm)
                    saved += 1
                elif isinstance(got, SiriReceipt):
                    account = self.cash_account() if got.cash else None
                    params = {"what": got.what, "amount": got.amount, "day": got.day}
                    # The words name no category: the model proposes one, the user confirms (4.9).
                    pair = got.pair or (self._model_category(got.what, pairs) if got.what else None)
                    proposed = got.pair is None
                    if account:                  # cash, and an account for it: a movement at once
                        ref = self.cash_movement(got.day, got.amount, got.what, pair, account, proposed=proposed)
                        notes.append(("expense.siri_note_cash", {**params, "account": account}))
                    else:
                        ref = self.save_receipt(got.day, got.amount, got.what, None if proposed else pair, got.cash)
                        receipts.append(ref)
                        notes.append(("expense.siri_note_saved", params))
                    if proposed:
                        confirm.append(SiriItem(kind="cash" if account else "receipt", ref=ref, what=got.what,
                                                amount=got.amount, day=got.day, pair=pair, cash=got.cash))
                    saved += 1
                elif got:
                    kept.append(got)
                    notes.append(("expense.siri_note_unclear", {"text": got}))
            if kept:
                with open(path.with_name(UNCLEAR_NAME), "a", encoding="utf-8") as f:
                    f.write("".join(k + "\n" for k in kept))
                unclear += len(kept)
            if entry:
                path.unlink(missing_ok=True)
        if saved or unclear:
            logger.info(f"expense_confirm: {saved} receipts from Siri saved, {unclear} sentences not understood")
        return SiriReport(saved, unclear, notes, confirm, tuple(receipts))

    def _siri_refund_or_withdrawal(self, got: SiriReceipt, notes: list, confirm: list) -> list[int]:
        """A refund or a withdrawal from the iPhone app (spec 32, 4.10): the
        cash side written at once when there is a cash account, the rest kept
        waiting for the statement. A refund takes an income category of the
        user's taxonomy: named by the words, else the default of the
        Settings, else proposed by the model and confirmed in the chat.
        Returns the ids of the receipts saved.

        Args:
            got: the line read, kind "refund" or "withdrawal".
            notes: the report's notes, one appended for this line.
            confirm: the report's expenses to confirm, a refund appended when
                its category is not the user's yet.
        """
        from core.spoken_expense import category_match
        account = self.cash_account()
        params = {"what": got.what, "amount": got.amount, "day": got.day}
        if got.kind == "refund":
            incomes = self.income_pairs()
            pair = (category_match(got.what, incomes) if got.what else None) or self.default_refund_pair()
            proposed = pair is None
            if proposed and got.what:
                pair = self._model_category(got.what, incomes)
            if got.cash and account:
                ref = self.cash_movement(got.day, got.amount, got.what, pair, account, proposed=proposed,
                                         kind="refund")
                notes.append(("expense.siri_note_cash", {**params, "account": account}))
                saved = []
            else:
                ref = self.save_receipt(got.day, got.amount, got.what, None if proposed else pair, got.cash,
                                        kind="refund")
                notes.append(("expense.siri_note_saved", params))
                saved = [ref]
            if proposed:
                confirm.append(SiriItem(kind="cash" if got.cash and account else "receipt", ref=ref, what=got.what,
                                        amount=got.amount, day=got.day, pair=pair, cash=got.cash, income=True))
            return saved
        cash_tx = (self.cash_movement(got.day, got.amount, got.what, None, account, kind="withdrawal")
                   if account else None)
        notes.append(("expense.siri_note_saved", params))
        return [self.save_receipt(got.day, got.amount, got.what, None, False, kind="withdrawal", cash_tx_id=cash_tx)]

    @staticmethod
    def unclear_sentences(files: list[Path]) -> list[str]:
        """The dictated sentences the reading could not use, to complete or
        forget."""
        out = []
        for f in dict.fromkeys(path.with_name(UNCLEAR_NAME) for path in files):   # one per folder
            if f.exists():
                out += [line for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
        return out

    @staticmethod
    def forget_unclear(files: list[Path], sentence: str) -> None:
        """Drop one sentence not understood, wherever it is.

        Args:
            files: the inbox files, as siri_files gives them.
            sentence: the sentence as unclear_sentences returned it.
        """
        for f in dict.fromkeys(path.with_name(UNCLEAR_NAME) for path in files):   # one per folder
            if not f.exists():
                continue
            lines = f.read_text(encoding="utf-8").splitlines()
            if sentence in lines:
                lines.remove(sentence)
                f.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
                return

    # ── 4.4 The cash account ─────────────────────────────────────────────────

    _CASH_NAMES = {"it": "Contanti", "en": "Cash", "fr": "Espèces", "de": "Bargeld", "es": "Efectivo"}

    def cash_account(self) -> str | None:
        """The account cash receipts go into (spec 32, 4.4), or None while
        the user has neither chosen nor created one."""
        with self._session() as s:
            name = repository.get_all_user_settings(s).get("cash_account") or ""
            names = {a.name for a in repository.get_accounts(s)}
        return name if name in names else None

    def create_cash_account(self) -> str:
        """Create the cash account, named in the user's language, or take
        the one with that name already there, and make it the cash account.
        Returns its name."""
        from services.settings_service import SettingsService
        cfg = SettingsService(self.engine)
        name = self._CASH_NAMES.get(cfg.get("ui_language", "it") or "it", "Contanti")
        if name not in {a.name for a in cfg.get_accounts()}:
            cfg.create_account(name, "", account_type="cash", usage="payments")
        cfg.set("cash_account", name)
        logger.info("expense_confirm: cash account created")
        return name

    def cash_movement(self, day: date, amount: Decimal, what: str, pair: tuple[str, str] | None,
                      account: str, proposed: bool = False, kind: str = "expense") -> str:
        """A cash expense said by the user, written as a movement of the
        cash account (spec 32, 4.4): out of the account, its category by
        hand, already checked. The only movement the expense confirmation
        writes, and only for cash (spec 32, 2.4).

        Args:
            day: the day of the expense.
            amount: the amount, positive.
            what: the user's own words, the movement's description.
            pair: (category, subcategory), or None without one.
            account: the cash account's name.
            proposed: True when *pair* is the model's proposal, not the
                user's choice: it is written as the model's, to confirm.
            kind: "expense" (out of the account), "refund" (in, an income) or
                "withdrawal" (in, a transfer from a bank account, no
                category) (spec 32, 4.10).

        Returns the movement's id.
        """
        import hashlib
        stamp = datetime.now(timezone.utc).isoformat()
        tx_id = hashlib.sha256(f"cash|{account}|{day}|{amount}|{what}|{stamp}".encode()).hexdigest()[:24]
        with self._session() as s:
            repository.upsert_transaction(s, {
                "id": tx_id, "date": day, "amount": -abs(amount) if kind == "expense" else abs(amount),
                "description": what or account,
                "source_file": "Spendif.ai", "doc_type": "cash", "account_label": account,
                "tx_type": {"refund": "income", "withdrawal": "internal_in"}.get(kind, "expense"),
                "category": pair[0] if pair else None,
                "subcategory": (pair[1] or None) if pair else None,
                "category_source": ("llm" if proposed else "manual") if pair else None, "human_validated": True,
            })
            tx = s.get(Transaction, tx_id)
            tx.validated_at = datetime.now(timezone.utc)
            s.commit()
        logger.info("expense_confirm: cash movement written")
        return tx_id

    def flush_cash_receipts(self) -> int:
        """The cash receipts waiting move into the cash account once there
        is one (spec 32, 4.4). Returns how many moved."""
        account = self.cash_account()
        if not account:
            return 0
        moved = 0
        for r in self.pending_receipts():
            if r["kind"] == "withdrawal" and not r["cash_tx_id"]:
                # The cash side written now; the bank side keeps waiting (4.10).
                ref = self.cash_movement(r["day"], r["amount"], r["what"], None, account, kind="withdrawal")
                with self._session() as s:
                    s.get(PendingReceipt, r["id"]).cash_tx_id = ref
                    s.commit()
                moved += 1
                continue
            if not r["cash"] or r["kind"] == "withdrawal":
                continue
            pair = (r["category"], r["subcategory"]) if r["category"] else None
            self.cash_movement(r["day"], r["amount"], r["what"], pair, account, kind=r["kind"])
            self.delete_receipt(r["id"])
            moved += 1
        return moved

    def _model_category(self, what: str, pairs: list[tuple[str, str]]) -> tuple[str, str] | None:
        """The category the model reads in *what*, among *pairs*, or None:
        the same short call as the category question (spec 32, 4.3), never
        during an import."""
        from core.spoken_expense import label
        index = self.model_pick(what, [label(p) for p in pairs])
        return pairs[index] if isinstance(index, int) and 0 <= index < len(pairs) else None

    def siri_category(self, item: SiriItem, pair: tuple[str, str]) -> None:
        """The category the user confirmed for an expense from Siri (spec
        32, 4.9): on the cash movement, by hand; on the saved receipt, as its
        category."""
        if item.kind == "cash":
            self.confirm(str(item.ref), pair)
            return
        with self._session() as s:
            row = s.get(PendingReceipt, int(item.ref))
            if row is not None:
                row.category, row.subcategory = pair[0], pair[1] or ""
                s.commit()
