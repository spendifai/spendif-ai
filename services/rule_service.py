"""RuleService — thin service layer over CategoryRule and DescriptionRule repository functions."""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy.orm import sessionmaker

from db import repository
from core.categorizer import CategoryRule as CoreCategoryRule
from core.rule_key import KEY_CHECK, KEY_KNOWN, STARTS_WITH, key_confidence  # noqa: F401 - re-exported for UI
from support.logging import setup_logging

logger = setup_logging()


class RuleService:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._Session = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def _session(self):
        s = self._Session()
        try:
            yield s
        finally:
            s.close()

    # ── Rules written by the product (spec 18) ────────────────────────────────

    def write_rules(self, descriptions: list[str], backend=None,
                    queue: bool = False) -> dict[str, tuple[str, str]]:
        """The rule each description should get: {description: (pattern, match_type)}.

        For a tick that means "this merchant always has this category". The
        merchant list (NSI) in the user's country names the merchant without
        the model; the rest goes to the model in one call. A key that does
        not pass the seal, a model that is busy with an import or fails in
        any way, all give the exact description, as before: writing a rule
        never fails because of the model.

        With *queue* (a tick, not a form the user is filling in), the
        descriptions left exact because the model was not available go to
        the queue of rules to rewrite (spec 18).
        """
        out, unavailable = self._write(descriptions, backend)
        if queue and unavailable:
            self._save_queue(self.pending_rewrites() + sorted(unavailable))
        return out

    def _write(self, descriptions: list[str], backend) -> tuple[dict[str, tuple[str, str]], set[str]]:
        """(rules, descriptions the model could not be asked about)."""
        from core.rule_key import nsi_key, seal

        out = {d: (d, "exact") for d in descriptions}
        with self._session() as s:
            settings = repository.get_all_user_settings(s)
            job = repository.get_latest_import_job(s)
        country = settings.get("country") or None

        to_model = []
        for d in descriptions:
            pattern = nsi_key(d, country)
            if pattern:
                out[d] = (pattern, STARTS_WITH)
            else:
                to_model.append(d)
        if not to_model:
            return out, set()
        if job is not None and job.status == "running":
            # The local model runs in this process: never two calls at once.
            logger.info(f"write_rules: import running, {len(to_model)} rules stay exact for now")
            return out, set(to_model)
        try:
            keys = self._ask_model_for_keys(to_model, settings, backend)
        except Exception as exc:
            logger.warning(f"write_rules: model failed, rules stay exact for now: {exc}")
            return out, set(to_model)
        for d in to_model:
            pattern = seal(d, keys.get(d))
            if pattern:
                out[d] = (pattern, STARTS_WITH)
        logger.info(
            "write_rules: "
            + ", ".join(f"{d!r} -> {p!r} ({t})" for d, (p, t) in out.items())
        )
        return out, {d for d in to_model if d not in keys}

    # The queue of rules to rewrite: work to do, not a property of a rule.
    _QUEUE_KEY = "rules_to_rewrite"

    def pending_rewrites(self) -> list[str]:
        import json
        with self._session() as s:
            raw = repository.get_user_setting(s, self._QUEUE_KEY, "[]")
        try:
            items = json.loads(raw or "[]")
        except ValueError:
            return []
        return [d for d in items if isinstance(d, str)]

    def _save_queue(self, descriptions: list[str]) -> None:
        import json
        seen: dict[str, str] = {}
        for d in descriptions:
            seen.setdefault(d.casefold(), d)
        with self._session() as s:
            repository.set_user_setting(s, self._QUEUE_KEY, json.dumps(list(seen.values()),
                                                                        ensure_ascii=False))
            s.commit()

    def rewrite_pending(self, backend=None) -> list[tuple[str, str, int]]:
        """Rewrite the rules left exact while the model was not available.

        For each one still there as the tick left it, a key that passes the
        seal turns it into "starts with" and the rule is applied again to the
        movements already imported, sweeping the other spellings of the same
        merchant (a category chosen by hand stays). Items whose rule is gone,
        or for which the model answered with no usable key, leave the queue;
        items the model still cannot be asked about stay.

        Returns (description, new pattern, movements categorized) per rule
        rewritten.
        """
        queue = self.pending_rewrites()
        if not queue:
            return []
        with self._session() as s:
            rules = repository.get_category_rules(s)
        targets = {}
        for d in queue:
            rule = next((r for r in rules if not r.is_giroconto and r.match_type == "exact"
                         and r.pattern.casefold() == d.casefold()), None)
            if rule is not None:
                targets[d] = rule
        out, unavailable = self._write(list(targets), backend) if targets else ({}, set())
        done = []
        with self._session() as s:
            taken = {(r.pattern.casefold(), r.match_type) for r in rules}
            for d, rule in targets.items():
                pattern, match_type = out[d]
                if match_type != STARTS_WITH:
                    continue
                if (pattern.casefold(), match_type) in taken:
                    # Another description of the same merchant got there first.
                    repository.delete_category_rule(s, rule.id)
                else:
                    repository.update_category_rule(s, rule.id, pattern=pattern,
                                                    match_type=match_type)
                    taken.add((pattern.casefold(), match_type))
                n = 0
                for tx in repository.get_transactions_by_rule_pattern(s, pattern, match_type):
                    if tx.category_source == "manual":
                        continue
                    repository.update_transaction_category(
                        s, tx.id, rule.category, rule.subcategory or "",
                        origin="rule_rewrite", source="rule",
                    )
                    n += 1
                done.append((d, pattern, n))
            s.commit()
        self._save_queue([d for d in queue if d in unavailable])
        logger.info(f"rewrite_pending: {len(done)} rewritten, {len(unavailable)} still waiting")
        return done

    def key_confidence(self, pattern: str, match_type: str) -> str | None:
        """KEY_KNOWN, KEY_CHECK or None for one rule (spec 18)."""
        with self._session() as s:
            country = repository.get_all_user_settings(s).get("country") or None
        return key_confidence(pattern, match_type, country)

    def _ask_model_for_keys(self, descriptions: list[str], settings: dict, backend) -> dict[str, str]:
        """{description: key} from one model call; missing items are left out."""
        import json
        from pathlib import Path

        from core.llm_backends import call_with_fallback

        if backend is None:
            from core.orchestrator import _build_backend, _build_categorizer_backend
            from services.category_service import CategoryService
            config = CategoryService._config_from_settings(settings)
            backend = _build_categorizer_backend(config) or _build_backend(config)
        prompts = json.loads(
            (Path(__file__).parent.parent / "prompts" / "rule_key.json").read_text(encoding="utf-8")
        )
        items = [{"idx": i, "text": d} for i, d in enumerate(descriptions)]
        schema = {
            "type": "object",
            "properties": {"results": {"type": "array", "items": {
                "type": "object",
                "properties": {"idx": {"type": "integer"}, "key": {"type": "string"}},
                "required": ["idx", "key"],
            }}},
            "required": ["results"],
        }
        raw, _ = call_with_fallback(
            primary=backend,
            system_prompt=prompts["system"].format(
                language=settings.get("description_language", "it")),
            user_prompt=prompts["user_template"].format(
                items=json.dumps(items, ensure_ascii=False)),
            json_schema=schema,
            caller="rule_key",
            step="rule_key",
        )
        keys: dict[str, str] = {}
        for r in (raw or {}).get("results", []) or []:
            if not isinstance(r, dict):
                continue
            idx, key = r.get("idx"), r.get("key")
            if isinstance(idx, int) and 0 <= idx < len(descriptions) and isinstance(key, str):
                keys[descriptions[idx]] = key
        return keys

    # ── CategoryRule ──────────────────────────────────────────────────────────

    def get_rules(self) -> list[CoreCategoryRule]:
        with self._session() as s:
            return repository.get_category_rules(s)

    def create_rule(
        self,
        pattern: str,
        match_type: str,
        category: str,
        subcategory: str,
        context: str | None = None,
        doc_type: str | None = None,
        priority: int = 0,
        is_giroconto: bool = False,
    ) -> tuple:
        with self._session() as s:
            result = repository.create_category_rule(
                s, pattern, match_type, category, subcategory, context, doc_type, priority,
                is_giroconto=is_giroconto,
            )
            s.commit()
            return result

    def update_rule(self, rule_id: int, **kwargs) -> bool:
        with self._session() as s:
            result = repository.update_category_rule(s, rule_id, **kwargs)
            s.commit()
            return result

    def set_multi_category(self, description: str, multi: bool = True) -> int:
        """Mark a counterpart multi-category or not (spec 24); returns how
        many movements changed."""
        with self._session() as s:
            n = repository.set_multi_category(s, description, multi)
            s.commit()
            return n

    def delete_giroconto_rule(self, pattern: str, match_type: str) -> bool:
        with self._session() as s:
            result = repository.delete_giroconto_rule(s, pattern, match_type)
            s.commit()
            return result

    def delete_rule(self, rule_id: int) -> bool:
        with self._session() as s:
            result = repository.delete_category_rule(s, rule_id)
            s.commit()
            return result

    def apply_to_review(self) -> int:
        with self._session() as s:
            rules = repository.get_category_rules(s)
            result = repository.apply_rules_to_review_transactions(s, rules)
            s.commit()
            return result

    def apply_to_all(self) -> tuple[int, int]:
        with self._session() as s:
            rules = repository.get_category_rules(s)
            result = repository.apply_all_rules_to_all_transactions(s, rules)
            s.commit()
            return result

    # ── DescriptionRule ───────────────────────────────────────────────────────

    def get_description_rules(self) -> list:
        with self._session() as s:
            return repository.get_description_rules(s)

    def create_description_rule(
        self, raw_pattern: str, match_type: str, cleaned_description: str
    ) -> tuple:
        with self._session() as s:
            result = repository.create_description_rule(s, raw_pattern, match_type, cleaned_description)
            s.commit()
            return result

    # ── Own names (spec 27) ──────────────────────────────────────────────────

    def same_movements(self, tx_id: str) -> list[str]:
        """The other movements equal to this one: same bank text without the
        words holding digits, same amount, same sign."""
        with self._session() as s:
            return repository.same_movements(s, tx_id)

    def other_amounts(self, tx_id: str) -> dict:
        """Movements with the same bank text and sign but another amount: an
        installment that changes. {"n": how many, "low": .., "high": ..} in
        absolute value, including this movement."""
        with self._session() as s:
            others = repository.same_text_other_amounts(s, tx_id)
            if not others:
                return {"n": 0, "low": None, "high": None}
            me = s.get(repository.Transaction, tx_id)
            values = [abs(float(t.amount)) for t in others] + [abs(float(me.amount))]
            return {"n": len(others), "low": min(values), "high": max(values)}

    def create_own_name(self, tx_id: str, name: str, any_amount: bool = False) -> int:
        """The user's own name for every movement like this one, past and
        future, and the category of this movement for all of them (founder,
        2026-10-21: the name and its category go together). Returns how many
        movements carry the name."""
        with self._session() as s:
            rule, renamed = repository.create_own_name(s, tx_id, name, any_amount=any_amount)
            if rule is not None and renamed:
                repository.spread_own_name_category(s, tx_id, renamed)
            s.commit()
            return len(renamed) if rule is not None else 0

    def get_own_names(self) -> list[dict]:
        with self._session() as s:
            return repository.get_own_names(s)

    def delete_description_rule(self, rule_id: int) -> bool:
        with self._session() as s:
            result = repository.delete_description_rule(s, rule_id)
            s.commit()
            return result
