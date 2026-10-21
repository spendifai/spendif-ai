"""Payment intermediaries table: seed, learning, rewriting, page data.

Spec: documents/04_software_engineering/20_intermediari_di_pagamento.md.
"""
from __future__ import annotations

import json
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from core.intermediaries import find, learn, prefix_key
from db import repository
from support.logging import setup_logging

logger = setup_logging()

_SEED_FILE = Path(__file__).parent.parent / "core" / "payment_intermediaries.json"
_EXAMPLES = 4


def seed_prefixes() -> list[str]:
    """The prefixes shipped with the app, in reduced words."""
    try:
        data = json.loads(_SEED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(f"intermediaries: seed unreadable ({exc}): none from the seed")
        return []
    return [p for p in (prefix_key(x) for x in data.get("intermediaries", [])) if p]


_seed_synced: set[str] = set()


def sync_seed_once(engine) -> None:
    """The seed sync, once per process and database (app.py reruns often)."""
    key = str(engine.url)
    if key in _seed_synced:
        return
    _seed_synced.add(key)
    try:
        IntermediaryService(engine).sync_seed()
    except Exception as exc:
        logger.warning(f"intermediaries: seed sync failed ({exc})")


class IntermediaryService:
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

    def active(self) -> set[str]:
        with self._session() as s:
            return repository.active_intermediary_prefixes(s)

    # ── Growing the table ─────────────────────────────────────────────────────

    def sync_seed(self) -> list[str]:
        """Add the shipped prefixes the table does not have yet, then rewrite.

        Run at every start: an update can lengthen the list, never take back
        a prefix learned or added by the user, never turn one back on.
        """
        with self._session() as s:
            added = repository.add_intermediaries(s, seed_prefixes(), "seed")
            s.commit()
        if added:
            logger.info(f"intermediaries: seed added {added}")
            self.rewrite_saved()
        return added

    def learn_from_texts(self, texts) -> list[str]:
        """Learn from *texts* plus the raw descriptions already saved."""
        with self._session() as s:
            saved = repository.raw_descriptions_with_asterisk(s)
        learned = learn(list(texts) + saved)
        with self._session() as s:
            added = repository.add_intermediaries(s, sorted(learned), "learned")
            s.commit()
        if added:
            logger.info(f"intermediaries: learned {added} "
                        f"({ {p: sorted(learned[p])[:5] for p in added} })")
            self.rewrite_saved()
        return added

    def learn_from_files(self, files: list[tuple[str, bytes]]) -> list[str]:
        """At the start of an import, on every text cell of every file together.

        Before any cleaning and before the model reads the columns: the
        asterisk is enough to find the descriptions. Never stops an import.
        """
        from core.orchestrator import load_raw_dataframe
        texts: list[str] = []
        for name, data in files:
            try:
                df, _, _ = load_raw_dataframe(data, name)
            except Exception as exc:  # unreadable here: the import will say why
                logger.debug(f"intermediaries: {name} not read for learning ({exc})")
                continue
            for col in df.columns:
                texts.extend(v for v in df[col].tolist() if isinstance(v, str) and "*" in v)
        try:
            return self.learn_from_texts(texts)
        except Exception as exc:
            logger.warning(f"intermediaries: learning failed ({exc})")
            return []

    def add_user(self, text: str) -> str | None:
        """Add (or turn back on) a prefix typed by the user. Returns it."""
        prefix = prefix_key(text)
        if not prefix:
            return None
        with self._session() as s:
            if not repository.add_intermediaries(s, [prefix], "user"):
                repository.set_intermediary_active(s, prefix, True)
            s.commit()
        self.rewrite_saved()
        return prefix

    def set_active(self, prefix: str, active: bool) -> None:
        with self._session() as s:
            repository.set_intermediary_active(s, prefix, active)
            s.commit()
        if active:
            self.rewrite_saved()

    # ── Saved movements ───────────────────────────────────────────────────────

    def rewrite_saved(self) -> int:
        """Write the merchant on saved movements that still show an active
        intermediary, and apply the rules to them. Returns how many."""
        with self._session() as s:
            active = repository.active_intermediary_prefixes(s)
            changed = repository.rewrite_intermediary_descriptions(s, active)
            if changed:
                repository.apply_rules_to_transactions(
                    s, changed, repository.get_category_rules(s))
            s.commit()
        if changed:
            logger.info(f"intermediaries: {len(changed)} saved descriptions rewritten")
        return len(changed)

    # ── Page ──────────────────────────────────────────────────────────────────

    def page_rows(self) -> list[dict]:
        """One row per prefix: origin, active, movements and example merchants."""
        with self._session() as s:
            rows = repository.get_intermediaries(s)
            raws = repository.raw_descriptions(s)
        count: dict[str, int] = defaultdict(int)
        merchants: dict[str, set[str]] = defaultdict(set)
        prefixes = {r.prefix for r in rows}
        for raw in raws:
            hit = find(raw, prefixes)
            if hit is None:
                continue
            count[hit[0]] += 1
            merchants[hit[0]].add(hit[1])
        return [
            {"prefix": r.prefix, "origin": r.origin, "active": bool(r.active),
             "movements": count.get(r.prefix, 0),
             "examples": sorted(merchants.get(r.prefix, set()))[:_EXAMPLES]}
            for r in rows
        ]

    def rules_on_intermediaries(self, rules) -> list:
        """Rules whose text starts with an active intermediary: after the
        rewrite they take nothing (spec 20)."""
        active = self.active()
        out = []
        for r in rules:
            key = prefix_key(r.pattern)
            if any(key == p or key.startswith(p + " ") for p in active):
                out.append(r)
        return out
