"""NSI runtime lookup (Fonte 3 della cascata categorizzazione).

Carica core/static_rules.json generato da scripts/build_static_rules.py.
Se il file non esiste, il lookup è silenziosamente disabilitato (graceful degradation).
Nessuna chiamata API — tutto locale.

Utilizzo:
    from core.nsi_lookup import nsi_lookup
    result = nsi_lookup.lookup("Esselunga", user_country="IT")
    if result:
        print(result.hint)   # "Alimentari > Spesa supermercato"
        print(result.osm_tag)  # "shop=supermarket"
"""
from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.initials import drop_apostrophes, join_initials

logger = logging.getLogger(__name__)

_STATIC_RULES_PATH = Path(__file__).parent / "static_rules.json"

_NOT_WORD = re.compile(r"[\W_]+")


def _tidy(text: str) -> str:
    """Lower case, every character that is not a letter or a digit a space,
    one space between words: the founder's rule for descriptions
    (07_deterministic_pipeline.md), so "7-Eleven" and "7 ELEVEN" meet."""
    return join_initials(" ".join(_NOT_WORD.sub(" ", drop_apostrophes(text).lower()).split()))


def _tidy_pattern(pattern: str) -> re.Pattern | None:
    """The same reduction on a list pattern, keeping what makes it a pattern.

    The patterns are prefixes for bank descriptions cut short ("\\bleroy\\ me"),
    alternatives joined by "|", characters escaped ("7\\-eleven"). Each
    alternative is unescaped and tidied; a word boundary stays where there
    was one, and so does a separator at the end. None when nothing is left.
    """
    alts = []
    for alt in pattern.split("|"):
        start = alt.startswith(r"\b")
        end = alt.endswith(r"\b") and not alt.endswith(r"\\b")
        raw = re.sub(r"\\(.)", r"\1", alt[2 if start else 0: len(alt) - (2 if end else 0)])
        core = _tidy(raw)
        # A pattern ending with a separator ("\\bbeijing\\ ") wants another word
        # after it: tidying would drop that space and turn it into a prefix
        # that takes "skyliner" or a "beijing" at the end. The space stays.
        follow = " " if raw and _NOT_WORD.fullmatch(raw[-1]) else ""
        if core:
            alts.append((r"\b" if start else "") + re.escape(core + follow) + (r"\b" if end else ""))
    try:
        return re.compile("|".join(alts)) if alts else None
    except re.error:
        return None

# Continent groupings for country ranking (ISO 3166-1 alpha-2 uppercase)
_CONTINENT_MAP: dict[str, frozenset[str]] = {
    "europe": frozenset({
        "IT", "DE", "FR", "ES", "PT", "NL", "BE", "AT", "CH", "PL",
        "RO", "SE", "DK", "FI", "NO", "CZ", "HU", "GR", "SK", "HR",
        "BG", "SI", "LT", "LV", "EE", "CY", "LU", "MT", "IE", "GB",
    }),
    "north_america": frozenset({"US", "CA", "MX"}),
    "latam": frozenset({"BR", "AR", "CL", "CO", "PE", "VE", "UY"}),
    "asia_pacific": frozenset({"JP", "CN", "AU", "NZ", "SG", "KR", "IN", "TH", "ID"}),
}


def _get_continent(country: str) -> Optional[str]:
    upper = country.upper()
    for continent, members in _CONTINENT_MAP.items():
        if upper in members:
            return continent
    return None


@dataclass(frozen=True)
class NsiMatch:
    hint: str           # e.g. "Alimentari > Spesa supermercato"
    osm_tag: str        # e.g. "shop=supermarket"
    brand: str          # e.g. "Esselunga"
    countries: tuple[str, ...]  # e.g. ("IT",)
    source: str = "nsi"


class NsiLookup:
    """Singleton NSI lookup. Loaded once from static_rules.json."""

    def __init__(self) -> None:
        self._rules: list[dict] = []
        self._loaded: bool = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not _STATIC_RULES_PATH.exists():
            logger.debug(
                "core/static_rules.json not found — NSI lookup disabled. "
                "Run: python scripts/build_static_rules.py"
            )
            return
        try:
            with open(_STATIC_RULES_PATH, encoding="utf-8") as f:
                data = json.load(f)
            self._rules = data.get("rules", [])
            for rule in self._rules:
                rule["_compiled"] = _tidy_pattern(rule.get("pattern", ""))
            logger.info(f"NSI: loaded {len(self._rules)} static rules from {_STATIC_RULES_PATH}")
        except Exception as exc:
            logger.warning(f"NSI: failed to load static_rules.json: {exc}")

    @property
    def is_available(self) -> bool:
        self._ensure_loaded()
        return len(self._rules) > 0

    def lookup(
        self,
        counterpart: str,
        user_country: Optional[str] = None,
    ) -> Optional[NsiMatch]:
        """Look up counterpart against NSI rules.

        Args:
            counterpart: Cleaned counterpart name (e.g., "Esselunga").
                         Should be post-cleaner (not raw description).
            user_country: ISO 3166-1 alpha-2 (e.g., "IT").
                          Used only for ranking — never excludes results.

        Returns:
            NsiMatch with hint and osm_tag, or None if no match.
        """
        self._ensure_loaded()
        if not self._rules:
            return None

        normalized = _tidy(counterpart)
        matches: list[dict] = []

        for rule in self._rules:
            compiled = rule.get("_compiled")
            if compiled is not None and compiled.search(normalized):
                matches.append(rule)

        if not matches:
            return None

        # Pick best match: if country ranking available, use it
        best = self._rank(matches, user_country)
        return NsiMatch(
            hint=best.get("hint", best.get("osm_tag", "")),
            osm_tag=best.get("osm_tag", ""),
            brand=best.get("brand", ""),
            countries=tuple(best.get("countries", [])),
        )

    def _rank(self, matches: list[dict], user_country: Optional[str]) -> dict:
        """Return the best match from a list, ranked by country relevance."""
        if len(matches) == 1 or not user_country:
            return matches[0]

        upper = user_country.upper()
        continent = _get_continent(upper)

        def score(rule: dict) -> int:
            countries = [c.upper() for c in rule.get("countries", [])]
            if not countries:
                return 2  # universal brand
            if upper in countries:
                return 0  # exact country match
            if continent and any(_get_continent(c) == continent for c in countries):
                return 1  # same continent
            return 3  # different region

        return min(matches, key=score)

    def stats(self) -> dict:
        """Return summary stats about the loaded rules (for diagnostics)."""
        self._ensure_loaded()
        country_counts: dict[str, int] = {}
        tag_counts: dict[str, int] = {}
        for rule in self._rules:
            for c in rule.get("countries", []):
                country_counts[c.upper()] = country_counts.get(c.upper(), 0) + 1
            tag = rule.get("osm_tag", "other")
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
        top_countries = sorted(country_counts.items(), key=lambda x: -x[1])[:10]
        top_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:10]
        return {
            "total_rules": len(self._rules),
            "top_countries": top_countries,
            "top_tags": top_tags,
        }


# Singleton — imported and used directly
nsi_lookup = NsiLookup()
