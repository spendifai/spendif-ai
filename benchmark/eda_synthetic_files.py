"""Exploratory data analysis of the synthetic classifier benchmark.

Why this script exists: the published file-reading figures (doc type, sign
convention, parse rate, amount accuracy on 50 synthetic files) can be quoted only
after someone has looked at the data they come from. The script answers three
questions before any figure leaves the repo:

1. What the synthetic dataset contains (composition and balance per file trait).
2. Which recorded runs reproduce each published table row (provenance).
3. How outcomes spread over files (ceiling effects, files every run gets wrong).

It is read-only: it never runs a model, it reads the results already recorded.

Usage:
    python benchmark/eda_synthetic_files.py \
        --manifest generated_files/manifest.csv \
        --results results_all_runs.csv \
        --out-dir /path/to/output
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

# Published table "Cross-backend classification accuracy" (tab:cross-backend in
# documents/05_publications/02_arxiv_paper.tex), in percent. Kept here so that
# the provenance check compares against exactly what was published.
PUBLISHED_TABLE = [
    ("qwen2.5-1.5b", "llamacpp", 100, 72, 100, 94),
    ("qwen2.5:1.5b", "ollama", 100, 74, 93, 74),
    ("gemma-2-2b", "llamacpp", 100, 100, 100, 84),
    ("gemma2:2b", "ollama", 100, 40, 93, 60),
    ("llama-3.2-3b", "llamacpp", 100, 50, 100, 68),
    ("llama3.2:3b", "ollama", 100, 74, 93, 74),
    ("qwen2.5-3b", "llamacpp", 100, 80, 100, 96),
    ("qwen2.5:3b", "ollama", 72, 30, 93, 60),
    ("phi-3-mini", "llamacpp", 100, 100, 100, 86),
    ("phi3", "ollama", 100, 74, 93, 74),
    ("qwen2.5-7b", "llamacpp", 100, 98, 97, 90),
    ("qwen2.5:7b", "ollama", 100, 60, 93, 64),
    ("gemma-3-12b", "llamacpp", 100, 100, 100, 86),
    ("gemma3:12b", "ollama", 89, 75, 98, 75),
    ("gpt-4o-mini", "openai", 100, 74, 93, 74),
]

METRIC_COLUMNS = ["doc_type_match", "convention_match", "parse_rate", "amount_accuracy"]
METRIC_LABELS = {"doc_type_match": "DT", "convention_match": "CV", "parse_rate": "PR", "amount_accuracy": "AM"}

# Tolerance, in percentage points, for a recorded run to count as the source of a
# published row: the paper rounds to whole percents.
MATCH_TOLERANCE_PP = 1.0


def describe_composition(manifest: pd.DataFrame) -> str:
    """Summarise what the synthetic dataset contains, as markdown.

    Rationale: a 100% score means little if every file has the same structure, so
    the balance of file traits is the first thing to read.

    Args:
        manifest: one row per synthetic file, columns as written by
            generate_synthetic_files.py (doc_type, format, amount_format,
            n_header_rows, n_data_rows, n_footer_rows, has_debit_credit_split,
            has_borders, n_internal_transfers, ...).

    Returns:
        Markdown text with one count table per trait and row-count statistics.
    """
    sections = [f"Files in manifest: {len(manifest)}\n"]
    for trait in ["doc_type", "format", "amount_format", "has_debit_credit_split", "has_borders", "bank"]:
        counts = manifest[trait].value_counts(dropna=False).rename_axis(trait).reset_index(name="files")
        sections.append(counts.to_markdown(index=False) + "\n")
    cross = pd.crosstab(manifest["doc_type"], manifest["amount_format"])
    sections.append("doc_type x amount_format\n\n" + cross.to_markdown() + "\n")
    numeric = manifest[["n_header_rows", "n_data_rows", "n_footer_rows", "n_internal_transfers"]].describe().round(1)
    sections.append(numeric.to_markdown() + "\n")
    return "\n".join(sections)


def summarise_runs(classifier_rows: pd.DataFrame) -> pd.DataFrame:
    """Aggregate the four published metrics per recorded run.

    Rationale: the same model appears in several runs (different commit, day or
    host); provenance needs one line per run, not per model.

    Args:
        classifier_rows: rows of results_all_runs.csv with benchmark_type ==
            "classifier"; metrics are 0..1 per file.

    Returns:
        One row per run (provider, model, git_commit, run day, hostname, source
        file) with the file count and each metric in percent.
    """
    runs = classifier_rows.assign(
        run_day="d" + classifier_rows["version"].astype(str).str[:8],
        source=classifier_rows["_source_file"].fillna("(none)"),
    )
    keys = ["provider", "model", "git_commit", "run_day", "runtime_hostname", "source"]
    summary = runs.groupby(keys, dropna=False).agg(files=("filename", "nunique"), rows=("filename", "size"), **{
        METRIC_LABELS[m]: (m, "mean") for m in METRIC_COLUMNS
    })
    for label in METRIC_LABELS.values():
        summary[label] = (summary[label] * 100).round(1)
    return summary.reset_index()


def match_published(run_summary: pd.DataFrame) -> pd.DataFrame:
    """Find, for each published row, the recorded runs that reproduce it.

    Rationale: a figure whose source run cannot be found must not be published.

    Args:
        run_summary: output of summarise_runs.

    Returns:
        One row per published table row with the number of matching runs and
        their identifiers (empty when no run reproduces the row).
    """
    matches = []
    for model_key, provider, *published in PUBLISHED_TABLE:
        candidates = run_summary[(run_summary["provider"] == provider) & run_summary["model"].str.lower().str.contains(model_key, regex=False)]
        labels = list(METRIC_LABELS.values())
        close = candidates[(candidates[labels] - published).abs().le(MATCH_TOLERANCE_PP).all(axis=1)]
        matches.append({
            "model": model_key, "provider": provider,
            **{f"pub_{lab}": val for lab, val in zip(labels, published)},
            "runs_for_model": len(candidates),
            "matching_runs": len(close),
            "matching": "; ".join(f"{r.git_commit}/{r.run_day}/{r.runtime_hostname}/{r.files} files" for r in close.itertuples()),
        })
    return pd.DataFrame(matches)


def per_file_outcomes(classifier_rows: pd.DataFrame) -> pd.DataFrame:
    """Mean of each metric per file over all recorded classifier rows.

    Rationale: files that every run gets right (ceiling) or wrong (possible
    ground-truth defect) change what an aggregate percentage means.

    Args:
        classifier_rows: as in summarise_runs.

    Returns:
        One row per file with the file traits and the mean metric in percent.
    """
    traits = ["file_doc_type", "file_format", "file_amount_format", "file_has_debit_credit_split", "file_has_borders"]
    outcome = classifier_rows.groupby("filename").agg(rows=("filename", "size"), **{t: (t, "first") for t in traits}, **{
        METRIC_LABELS[m]: (m, "mean") for m in METRIC_COLUMNS
    })
    for label in METRIC_LABELS.values():
        outcome[label] = (outcome[label] * 100).round(1)
    return outcome.reset_index()


def draw_figure(manifest: pd.DataFrame, classifier_rows: pd.DataFrame, figure_path: Path) -> None:
    """Draw dataset composition and the file x run heatmap of CV and AM.

    Rationale: one picture shows both the balance of the data and where the
    errors concentrate; CV and AM are the two metrics that are not at ceiling.

    Args:
        manifest: as in describe_composition.
        classifier_rows: as in summarise_runs.
        figure_path: PNG destination; parent folder must exist.
    """
    run_label = classifier_rows["provider"] + " " + classifier_rows["model"].str[:22] + " " + classifier_rows["version"].astype(str).str[:8]
    rows = classifier_rows.assign(run=run_label)
    fig, axes = plt.subplots(1, 3, figsize=(22, 12), gridspec_kw={"width_ratios": [1, 3, 3]})
    pd.crosstab(manifest["doc_type"], manifest["amount_format"]).plot.barh(stacked=True, ax=axes[0])
    axes[0].set_title("Synthetic files: doc type x amount format")
    axes[0].set_xlabel("files")
    for axis, metric in zip(axes[1:], ["convention_match", "amount_accuracy"]):
        grid = rows.pivot_table(index="filename", columns="run", values=metric, aggfunc="mean")
        image = axis.imshow(grid.values, aspect="auto", cmap="RdYlGn", vmin=0, vmax=1)
        axis.set_yticks(range(len(grid.index)), grid.index, fontsize=6)
        axis.set_xticks(range(len(grid.columns)), grid.columns, rotation=90, fontsize=6)
        axis.set_title(f"{METRIC_LABELS[metric]} per file and run (green = correct)")
        fig.colorbar(image, ax=axis, fraction=0.03)
    fig.tight_layout()
    fig.savefig(figure_path, dpi=130)


def main() -> None:
    """Command-line entry point: write the three EDA outputs to --out-dir."""
    parser = argparse.ArgumentParser(description="EDA of the synthetic classifier benchmark (read-only).")
    parser.add_argument("--manifest", type=Path, required=True, help="manifest.csv of the synthetic files used by the runs")
    parser.add_argument("--results", type=Path, required=True, help="results_all_runs.csv")
    parser.add_argument("--out-dir", type=Path, required=True, help="folder for eda_report.md and eda_figure.png")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    manifest = pd.read_csv(args.manifest)
    results = pd.read_csv(args.results, low_memory=False)
    classifier_rows = results[results["benchmark_type"] == "classifier"].copy()
    for metric in METRIC_COLUMNS:
        classifier_rows[metric] = pd.to_numeric(classifier_rows[metric], errors="coerce")

    run_summary = summarise_runs(classifier_rows)
    provenance = match_published(run_summary)
    missing_metrics = classifier_rows[METRIC_COLUMNS].isna().sum()

    # Exclusion rules, applied after provenance (which must see every run):
    # rows with an error are crashed classifications scored 0, not wrong answers;
    # runs covering fewer than all files are partial and not comparable.
    errored = classifier_rows["error"].notna()
    run_keys = ["provider", "model", "git_commit", "_source_file"]
    files_per_run = classifier_rows.groupby(run_keys, dropna=False)["filename"].transform("nunique")
    partial = files_per_run < manifest["filename"].nunique()
    valid_rows = classifier_rows[~errored & ~partial]
    exclusions = pd.DataFrame({
        "rule": ["error column set (crash scored as 0)", "partial run (fewer files than manifest)"],
        "rows": [int(errored.sum()), int((partial & ~errored).sum())],
        "models": [", ".join(sorted(classifier_rows.loc[errored, "model"].unique())), ", ".join(sorted(classifier_rows.loc[partial & ~errored, "model"].unique()))],
    })
    outcomes = per_file_outcomes(valid_rows)
    amount_by_trait = valid_rows.groupby(["file_doc_type", "file_amount_format"])["amount_accuracy"].agg(["size", "mean"]).round(3)

    report = [
        "# EDA synthetic classifier benchmark\n",
        "## 1. Dataset composition\n", describe_composition(manifest),
        "## 2. Missing metric values in classifier rows\n", missing_metrics.to_frame("missing").to_markdown() + "\n",
        "## 3. Provenance of the published table\n", provenance.to_markdown(index=False) + "\n",
        "## 4. Recorded runs\n", run_summary.to_markdown(index=False) + "\n",
        "## 5. Exclusions\n", exclusions.to_markdown(index=False) + f"\n\nValid rows: {len(valid_rows)}\n",
        "## 6. Per-file outcomes (valid rows)\n", outcomes.sort_values(["AM", "DT"]).to_markdown(index=False) + "\n",
        "## 7. Amount accuracy by file structure (valid rows)\n", amount_by_trait.to_markdown() + "\n",
        "## 8. Ceiling and floor (valid rows)\n",
        "| metric | files at 100 in every valid row | files at 0 in every valid row |\n|:--|--:|--:|\n"
        + "\n".join(f"| {lab} | {int((outcomes[lab] == 100).sum())} | {int((outcomes[lab] == 0).sum())} |" for lab in METRIC_LABELS.values())
        + f"\n\nFiles: {len(outcomes)}\n",
    ]
    (args.out_dir / "eda_report.md").write_text("\n".join(report), encoding="utf-8")
    draw_figure(manifest, valid_rows, args.out_dir / "eda_figure.png")
    print(f"Wrote {args.out_dir / 'eda_report.md'} and {args.out_dir / 'eda_figure.png'}")


if __name__ == "__main__":
    main()
