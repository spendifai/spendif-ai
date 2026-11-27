"""Exploratory look at a product-path benchmark run, before any figure is reported.

Why this script exists: the results of benchmark_product_path.py decide which accuracy
figures may reach the landing page. Before reading them as evidence one checks that the
run is complete, that every file was imported in both scenarios, where the outcomes
concentrate (amount format x account type) and which files fail in both scenarios.
It only reads the result CSV and its manifest; it never imports a file.

Usage:
    python3 benchmark/eda_product_path.py --results <run.csv> --manifest <run.manifest.csv> --out-dir <dir>
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

GROUP_KEYS = ("doc_type_expected", "amount_format")


def load_rows(path: Path) -> list[dict]:
    """Read a CSV into a list of dicts.

    Args:
        path: CSV file with a header row.
    """
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def coverage(results: list[dict], manifest: list[dict]) -> list[tuple[str, str]]:
    """Checks on completeness: one row per file and scenario, errors, row counts.

    Args:
        results: rows of the result CSV.
        manifest: rows of the manifest CSV written next to it.

    Returns:
        (check, outcome) pairs for the diagnostic table.
    """
    files = {row["filename"] for row in manifest}
    checks = [("File nel manifest", str(len(files))), ("Righe di risultato", str(len(results)))]
    for scenario in sorted({row["scenario"] for row in results}):
        seen = {row["filename"] for row in results if row["scenario"] == scenario}
        checks.append((f"Scenario {scenario}: file coperti", f"{len(seen & files)} su {len(files)}"))
    checks.append(("Righe con errore", str(sum(1 for row in results if row["error"]))))
    rows_off = [row["filename"] for row in results if row["rows_match"] != "1"]
    checks.append(("Righe estratte diverse dalle attese", str(len(rows_off))))
    checks.append(("Schema da rivedere (needs_schema_review)", str(sum(row["needs_schema_review"] == "1" for row in results))))
    durations = sorted(float(row["duration_seconds"]) for row in results)
    middle = durations[len(durations) // 2]
    checks.append(("Durata per import (s)", f"min {durations[0]:.0f}, mediana {middle:.0f}, max {durations[-1]:.0f}, totale {sum(durations) / 3600:.1f} h"))
    with_transfers = [row for row in results if row["transfer_recall"] != ""]
    found_any = sum(1 for row in with_transfers if float(row["transfer_recall"]) > 0)
    checks.append(("File con giroconti attesi / con almeno uno trovato", f"{len(with_transfers)} / {found_any}"))
    return checks


def group_table(results: list[dict]) -> list[dict]:
    """Outcome per scenario x account type x amount format.

    Args:
        results: rows of the result CSV.

    Returns:
        One dict per group with file count, mean signed and absolute accuracy,
        questions asked and files with a signed accuracy below 0.5.
    """
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for row in results:
        buckets[(row["scenario"], *[row[key] for key in GROUP_KEYS])].append(row)
    table = []
    for key in sorted(buckets):
        rows = buckets[key]
        signed = [float(row["amount_signed_accuracy"]) for row in rows]
        table.append({
            "scenario": key[0], "doc_type": key[1], "amount_format": key[2], "n_files": len(rows),
            "signed_mean": sum(signed) / len(rows),
            "abs_mean": sum(float(row["amount_abs_accuracy"]) for row in rows) / len(rows),
            "questions": sum(row["sign_needs_confirmation"] == "1" for row in rows),
            "signed_below_half": sum(value < 0.5 for value in signed),
            "doc_type_ok": sum(row["doc_type_match"] == "1" for row in rows),
        })
    return table


def failing_in_both(results: list[dict]) -> list[tuple[str, str, str, float, float, str, str]]:
    """Files whose signed accuracy is below 0.5 in both scenarios.

    Args:
        results: rows of the result CSV.

    Returns:
        (filename, doc_type, amount_format, signed none, signed declared, question none, question declared).
    """
    by_file: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in results:
        by_file[row["filename"]][row["scenario"]] = row
    out = []
    for filename, scenarios in sorted(by_file.items()):
        none_row, declared_row = scenarios.get("none"), scenarios.get("declared")
        if none_row is None or declared_row is None:
            continue
        signed_none, signed_declared = float(none_row["amount_signed_accuracy"]), float(declared_row["amount_signed_accuracy"])
        if signed_none < 0.5 and signed_declared < 0.5:
            out.append((filename, none_row["doc_type_expected"], none_row["amount_format"], signed_none, signed_declared,
                        none_row["sign_needs_confirmation"], declared_row["sign_needs_confirmation"]))
    return out


def draw_figure(results: list[dict], out_path: Path) -> None:
    """Heatmap file x scenario of the signed accuracy, files grouped by format, with question markers.

    Args:
        results: rows of the result CSV.
        out_path: PNG to write.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    files = sorted({row["filename"] for row in results},
                   key=lambda name: next((row["amount_format"], row["doc_type_expected"], name) for row in results if row["filename"] == name))
    scenarios = ["none", "declared"]
    index = {(row["filename"], row["scenario"]): row for row in results}
    grid = [[float(index[(name, scenario)]["amount_signed_accuracy"]) for scenario in scenarios] for name in files]
    figure, axis = plt.subplots(figsize=(6.5, 13))
    image = axis.imshow(grid, aspect="auto", cmap="RdYlGn", vmin=0, vmax=1)
    for row_index, name in enumerate(files):
        for column_index, scenario in enumerate(scenarios):
            if index[(name, scenario)]["sign_needs_confirmation"] == "1":
                axis.text(column_index, row_index, "?", ha="center", va="center", fontsize=8, fontweight="bold")
    labels = [f"{name}  [{index[(name, 'none')]['amount_format']}, {index[(name, 'none')]['doc_type_expected']}]" for name in files]
    axis.set_yticks(range(len(files)), labels, fontsize=6)
    axis.set_xticks(range(len(scenarios)), ["nessun uso dichiarato", "uso dichiarato"], fontsize=8)
    axis.set_title("Importi con segno corretto per file (? = l'app chiede il segno)", fontsize=9)
    figure.colorbar(image, ax=axis, fraction=0.04, label="quota di movimenti con segno corretto")
    figure.tight_layout()
    figure.savefig(out_path, dpi=150)


def main() -> None:
    """Print the diagnostic tables in markdown and write the figure."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=Path, required=True, help="result CSV of benchmark_product_path.py")
    parser.add_argument("--manifest", type=Path, required=True, help="manifest CSV written next to the results")
    parser.add_argument("--out-dir", type=Path, required=True, help="directory for the figure")
    args = parser.parse_args()
    results, manifest = load_rows(args.results), load_rows(args.manifest)

    print("| Controllo | Esito |\n|:--|:--|")
    for check, outcome in coverage(results, manifest):
        print(f"| {check} | {outcome} |")
    print("\n| Scenario | Tipo conto | Formato importi | File | Segno ok (media) | Trovati (media) | Domande | File < 50% | Tipo doc ok |")
    print("|:--|:--|:--|--:|--:|--:|--:|--:|--:|")
    for group in group_table(results):
        print(f"| {group['scenario']} | {group['doc_type']} | {group['amount_format']} | {group['n_files']} | {group['signed_mean']:.1%} | "
              f"{group['abs_mean']:.1%} | {group['questions']} | {group['signed_below_half']} | {group['doc_type_ok']} |")
    print("\n| File sotto il 50% in entrambi gli scenari | Tipo | Formato | none | declared | domanda none | domanda declared |")
    print("|:--|:--|:--|--:|--:|--:|--:|")
    for item in failing_in_both(results):
        print(f"| {item[0]} | {item[1]} | {item[2]} | {item[3]:.0%} | {item[4]:.0%} | {item[5]} | {item[6]} |")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / "product_path_eda.png"
    draw_figure(results, out_path)
    print(f"\nFigura: {out_path}")


if __name__ == "__main__":
    main()
