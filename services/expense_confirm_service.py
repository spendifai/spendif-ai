"""ExpenseConfirmService: the ledger side of confirming an expense (spec 32).

The flow is fixed and lives in core.expense_confirm; this service gives it
its ports: the outflows to search, the taxonomy, the model's one short call,
and the writes of spec 32, 5. It never creates a movement.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import sessionmaker

from core.expense_confirm import WINDOW_DAYS, Candidate, Msg, ReceiptMatch, State, start, turn
from core.siri_inbox import INBOX_NAME, UNCLEAR_NAME, SiriReceipt, read_line
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

    def start(self) -> tuple[list[Msg], State]:
        """The first turn, with the receipts saved that now have a movement
        (spec 32, 4.4bis)."""
        return start(self.receipt_matches())

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
                     cash: bool = False) -> None:
        """Keep a receipt whose movement is not imported yet: date, amount,
        the user's description and category (spec 32, 4.4bis)."""
        with self._session() as s:
            s.add(PendingReceipt(date=day.isoformat(), amount=amount, description=what,
                                 category=pair[0] if pair else "", subcategory=(pair[1] or "") if pair else "",
                                 paid_cash=cash))
            s.commit()
        logger.info("expense_confirm: receipt saved for the next import")

    def pending_receipts(self) -> list[dict]:
        """The receipts still waiting, oldest day first: id, day, amount,
        what, cash, category, subcategory."""
        with self._session() as s:
            rows = (s.query(PendingReceipt).filter(PendingReceipt.matched_tx_id.is_(None))
                    .order_by(PendingReceipt.date, PendingReceipt.id).all())
            return [{"id": r.id, "day": date.fromisoformat(r.date),
                     "amount": Decimal(str(r.amount)).quantize(Decimal("0.01")), "what": r.description or "",
                     "cash": bool(r.paid_cash),
                     "category": r.category, "subcategory": r.subcategory or ""} for r in rows]

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
        """The receipts waiting that now have at least one unchecked outflow
        with their amount, within the window of spec 32, 4.2: what the
        conversation offers to check at its start. Nothing is written."""
        window = timedelta(days=WINDOW_DAYS)
        matches = []
        for r in self.pending_receipts():
            if r["cash"]:
                continue                  # cash never reaches a statement (spec 32, 4.4bis)
            found = self.outflows(r["amount"], r["amount"], r["day"] - window, r["day"] + window, False)
            if found:
                found.sort(key=lambda c: (abs((c.day - r["day"]).days), c.day, c.tx_id))
                pair = (r["category"], r["subcategory"]) if r["category"] else None
                matches.append(ReceiptMatch(receipt_id=r["id"], day=r["day"], amount=r["amount"],
                                            what=r["what"], pair=pair, candidates=tuple(found)))
        return matches

    def confirm_receipt(self, receipt_id: int, tx_id: str, pair: tuple[str, str] | None) -> list[dict]:
        """The user's yes: the movement checked, with the receipt's category
        by hand when *pair* is given, and the receipt no longer waiting.
        Returns the "always like this?" questions of a change by hand, as
        confirm does."""
        questions = self.confirm(tx_id, pair)
        with self._session() as s:
            row = s.get(PendingReceipt, receipt_id)
            row.matched_tx_id, row.matched_at = tx_id, datetime.now(timezone.utc)
            s.commit()
        return questions

    # ── 4.9 The expenses dictated to Siri ────────────────────────────────────

    @staticmethod
    def siri_files(icloud: bool) -> list[Path]:
        """Where the Shortcuts command writes (spec 32, 4.9): the Mac's
        Documents always, iCloud Drive only when the user turned it on.

        Args:
            icloud: the setting siri_icloud_inbox, True when the iPhone may
                write through iCloud.
        """
        if sys.platform != "darwin":
            return []                        # Siri and its commands live on Apple systems only
        files = [Path.home() / "Documents" / "Spendif.ai" / INBOX_NAME]
        if icloud:
            files.append(Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
                         / "Spendif.ai" / INBOX_NAME)
        return files

    def collect_siri(self, files: list[Path], today: date | None = None) -> tuple[int, int]:
        """Read the lines the command wrote and empty the files: each line a
        saved receipt, or a sentence to complete kept next to the file.
        Returns (receipts saved, sentences not understood).

        Args:
            files: the inbox files, as siri_files gives them.
            today: the day for a line without its date; None for today.
        """
        pairs = self.category_pairs()
        saved = unclear = 0
        for path in files:
            path.parent.mkdir(parents=True, exist_ok=True)     # the command needs the folder to exist
            if not path.exists():
                continue
            reading = path.with_name(path.name + ".reading")
            os.replace(path, reading)            # a line dictated meanwhile starts a new file
            kept = []
            for line in reading.read_text(encoding="utf-8").splitlines():
                got = read_line(line, pairs, today or self._today or date.today())
                if isinstance(got, SiriReceipt):
                    account = self.cash_account() if got.cash else None
                    if account:                  # cash, and an account for it: a movement at once
                        self.cash_movement(got.day, got.amount, got.what, got.pair, account)
                    else:
                        self.save_receipt(got.day, got.amount, got.what, got.pair, got.cash)
                    saved += 1
                elif got:
                    kept.append(got)
            if kept:
                with open(path.with_name(UNCLEAR_NAME), "a", encoding="utf-8") as f:
                    f.write("".join(k + "\n" for k in kept))
                unclear += len(kept)
            reading.unlink()
        if saved or unclear:
            logger.info(f"expense_confirm: {saved} receipts from Siri saved, {unclear} sentences not understood")
        return saved, unclear

    @staticmethod
    def unclear_sentences(files: list[Path]) -> list[str]:
        """The dictated sentences the reading could not use, to complete or
        forget."""
        out = []
        for path in files:
            f = path.with_name(UNCLEAR_NAME)
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
        for path in files:
            f = path.with_name(UNCLEAR_NAME)
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
                      account: str) -> None:
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
        """
        import hashlib
        stamp = datetime.now(timezone.utc).isoformat()
        tx_id = hashlib.sha256(f"cash|{account}|{day}|{amount}|{what}|{stamp}".encode()).hexdigest()[:24]
        with self._session() as s:
            repository.upsert_transaction(s, {
                "id": tx_id, "date": day, "amount": -abs(amount), "description": what or account,
                "source_file": "Spendif.ai", "doc_type": "cash", "account_label": account,
                "tx_type": "expense", "category": pair[0] if pair else None,
                "subcategory": (pair[1] or None) if pair else None,
                "category_source": "manual" if pair else None, "human_validated": True,
            })
            tx = s.get(Transaction, tx_id)
            tx.validated_at = datetime.now(timezone.utc)
            s.commit()
        logger.info("expense_confirm: cash movement written")

    def flush_cash_receipts(self) -> int:
        """The cash receipts waiting move into the cash account once there
        is one (spec 32, 4.4). Returns how many moved."""
        account = self.cash_account()
        if not account:
            return 0
        moved = 0
        for r in self.pending_receipts():
            if not r["cash"]:
                continue
            pair = (r["category"], r["subcategory"]) if r["category"] else None
            self.cash_movement(r["day"], r["amount"], r["what"], pair, account)
            self.delete_receipt(r["id"])
            moved += 1
        return moved
