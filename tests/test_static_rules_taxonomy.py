"""Le regole esercenti devono puntare a categorie che esistono davvero.

core/static_rules.json e' generato da scripts/build_static_rules.py a partire
da core/static_rules/osm_to_spendifai_map.json. Il file generato e' rimasto
indietro rispetto alla mappa dal 2026-04-05 al 2026-08-29: la mappa fu
allineata alla tassonomia reale il giorno dopo la generazione, e nessuno
rigenero'. Risultato: 7.621 regole su 10.021 suggerivano al modello categorie
inesistenti, e nessun test se ne accorgeva.
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RULES = REPO / "core" / "static_rules.json"
MAP = REPO / "core" / "static_rules" / "osm_to_spendifai_map.json"


def _taxonomy_pairs(lang: str = "it") -> set[tuple[str, str]]:
    import sys
    sys.path.insert(0, str(REPO))
    from db.taxonomy_defaults import TAXONOMY_DEFAULTS

    t = TAXONOMY_DEFAULTS[lang]
    return {
        (c["category"], s)
        for gruppo in ("expenses", "income")
        for c in t[gruppo]
        for s in c["subcategories"]
    }


def _hint_pair(hint: str) -> tuple[str, str]:
    cat, sub = (x.strip() for x in hint.split(" > ", 1))
    return cat, sub


def test_source_map_points_at_real_taxonomy_entries():
    mappa = json.loads(MAP.read_text(encoding="utf-8"))
    mappa.pop("_comment", None)
    valide = _taxonomy_pairs()
    rotte = {tag: v["hint"] for tag, v in mappa.items() if _hint_pair(v["hint"]) not in valide}
    assert not rotte, f"la mappa OSM punta a voci di tassonomia inesistenti: {rotte}"


def test_generated_rules_match_the_source_map():
    """Il file generato non deve restare indietro rispetto alla mappa."""
    mappa = json.loads(MAP.read_text(encoding="utf-8"))
    mappa.pop("_comment", None)
    attesi = {v["hint"] for v in mappa.values()}
    generati = {r["hint"] for r in json.loads(RULES.read_text(encoding="utf-8"))["rules"] if "hint" in r}
    orfani = sorted(generati - attesi)
    assert not orfani, (
        "static_rules.json contiene hint che la mappa non produce piu': "
        "va rigenerato con scripts/build_static_rules.py\n  " + "\n  ".join(orfani[:10])
    )


def test_every_rule_points_at_a_real_taxonomy_entry():
    valide = _taxonomy_pairs()
    regole = json.loads(RULES.read_text(encoding="utf-8"))["rules"]
    rotte = [r["hint"] for r in regole if "hint" in r and _hint_pair(r["hint"]) not in valide]
    assert not rotte, (
        f"{len(rotte)} regole su {len(regole)} suggeriscono categorie inesistenti "
        f"(l'hint finisce nel prompt come merchant_hint): {sorted(set(rotte))[:6]}"
    )
