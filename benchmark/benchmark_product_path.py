"""Benchmark of file reading on the same entry point the app uses (process_file).

Why this script exists: benchmark_classifier.py measured the classifier alone and
fed it the document type from the ground truth, so its doc-type score echoed its
input; since the sign moved out of the classifier (into process_file, with the
account-type correction and the account usage) it no longer measures the product
and no longer runs. This script imports each synthetic file exactly as the app
does on a file it has never seen, with nothing from the ground truth in the call,
and compares what would be imported with the expected rows.

Specification: documents/04_software_engineering/09_benchmark_strategy.tex,
section "Benchmark sul percorso del prodotto".

Usage:
    uv run python benchmark/benchmark_product_path.py \
        --model-path ~/.spendifai/models/google_gemma-3-12b-it-Q4_K_M.gguf --runs 3
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BENCHMARK_DIR = Path(__file__).resolve().parent
GENERATED_DIR = BENCHMARK_DIR / "generated_files"
OUTPUT_DIR = BENCHMARK_DIR / "results" / "product_path"

# Tolerance on amounts, in currency units: the files carry two decimals.
AMOUNT_TOLERANCE = 0.01
# Rows extracted may differ from the manifest by this many and still count as a match
# (same tolerance as the classifier benchmark, for merged or footer rows).
ROWS_TOLERANCE = 2

# Account usage the person would declare once per account, by document type.
DECLARED_USAGE_BY_DOC_TYPE = {"credit_card": "payments"}
DECLARED_USAGE_DEFAULT = "in_and_out"


@dataclass
class ExpectedRow:
    """One row of a .expected.csv ground-truth file.

    Attributes:
        date: ISO date of the movement (YYYY-MM-DD).
        amount: signed amount in euro, negative for money leaving the account.
        description: raw description as written in the file.
        is_internal_transfer: True when the movement is a transfer between own accounts.
    """

    date: str
    amount: float
    description: str
    is_internal_transfer: bool


@dataclass
class FileScore:
    """Comparison of the extracted movements of one file with its ground truth.

    Attributes:
        n_expected: rows in the ground truth.
        n_extracted: movements returned by process_file.
        n_matched: ground-truth rows found among the extracted movements (same date and absolute amount).
        n_amount_signed_ok: matched rows whose signed amount is also correct.
        n_transfers_expected: ground-truth rows marked as internal transfers.
        n_transfers_found: of those, matched rows that the pipeline typed as internal.
    """

    n_expected: int
    n_extracted: int
    n_matched: int
    n_amount_signed_ok: int
    n_transfers_expected: int
    n_transfers_found: int


@dataclass
class ResultRow:
    """One output line: one file, one run, one usage scenario.

    Attributes:
        run_id: 1-based run number.
        scenario: "none" (the person has not said how the account is used) or "declared".
        account_usage: value passed to process_file, empty for None.
        filename: synthetic file name.
        doc_type_expected: document type in the manifest.
        amount_format: amount layout in the manifest (signed_single, debit_credit_split, ...).
        doc_type_detected: document type returned by the pipeline, empty if none.
        doc_type_match: 1 when detected equals expected.
        rows_expected: data rows in the manifest.
        rows_extracted: movements returned.
        rows_match: 1 when the two differ by at most ROWS_TOLERANCE.
        amount_signed_accuracy: share of ground-truth rows found with correct signed amount (0..1).
        amount_abs_accuracy: share of ground-truth rows found at all (correct absolute amount and date).
        transfer_recall: share of expected internal transfers typed as internal; empty when none expected.
        needs_schema_review: 1 when the app would ask to confirm the schema.
        sign_needs_confirmation: 1 when the app would ask about the sign.
        confidence_score: classifier confidence (0..1).
        duration_seconds: wall time of process_file.
        error: exception text, or "no movements"; empty on success.
        model: model file or name.
        git_commit: short commit of the code under test.
        hostname: machine that ran the benchmark.
    """

    run_id: int
    scenario: str
    account_usage: str
    filename: str
    doc_type_expected: str
    amount_format: str
    doc_type_detected: str = ""
    doc_type_match: int = 0
    rows_expected: int = 0
    rows_extracted: int = 0
    rows_match: int = 0
    amount_signed_accuracy: float = 0.0
    amount_abs_accuracy: float = 0.0
    transfer_recall: Optional[float] = None
    needs_schema_review: int = 0
    sign_needs_confirmation: int = 0
    confidence_score: float = 0.0
    duration_seconds: float = 0.0
    error: str = ""
    model: str = ""
    git_commit: str = ""
    hostname: str = ""


def load_expected_rows(expected_path: Path) -> list[ExpectedRow]:
    """Read a .expected.csv file.

    Args:
        expected_path: path to <file stem>.expected.csv.

    Returns:
        The ground-truth rows in file order.
    """
    with open(expected_path, encoding="utf-8") as handle:
        return [
            ExpectedRow(
                date=row["date"],
                amount=float(row["amount"]),
                description=row["description_raw"],
                is_internal_transfer=row["is_internal_transfer"].strip().lower() == "true",
            )
            for row in csv.DictReader(handle)
        ]


def _as_iso_date(value) -> str:
    """Normalise a movement date (date, datetime or string) to YYYY-MM-DD.

    Args:
        value: date as produced by the pipeline; None gives an empty string.
    """
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def _as_float(value) -> Optional[float]:
    """Convert a pipeline amount (Decimal, float, str) to float, None when missing or unreadable.

    Args:
        value: amount as produced by the pipeline.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def score_file(expected_rows: list[ExpectedRow], movements: list[dict]) -> FileScore:
    """Match ground-truth rows to extracted movements and count what is correct.

    Rationale: matching by position breaks as soon as one row is merged or skipped;
    matching by date and absolute amount, then by description, keeps each error local
    to the row that has it. Each movement is used at most once.

    Args:
        expected_rows: ground truth of the file.
        movements: transaction dicts from ImportResult.transactions; keys used are
            "date", "amount", "raw_description"/"description" and "tx_type".

    Returns:
        Counts for one file; accuracies are derived by the caller.
    """
    unused = list(range(len(movements)))
    matched = signed_ok = transfers_found = 0
    for expected in expected_rows:
        candidates = []
        for index in unused:
            movement = movements[index]
            amount = _as_float(movement.get("amount"))
            if amount is None or _as_iso_date(movement.get("date")) != expected.date:
                continue
            if abs(abs(amount) - abs(expected.amount)) <= AMOUNT_TOLERANCE:
                candidates.append(index)
        if not candidates:
            continue
        same_description = [i for i in candidates if (movements[i].get("raw_description") or movements[i].get("description") or "") == expected.description]
        chosen = (same_description or candidates)[0]
        unused.remove(chosen)
        matched += 1
        if abs(_as_float(movements[chosen].get("amount")) - expected.amount) <= AMOUNT_TOLERANCE:
            signed_ok += 1
        if expected.is_internal_transfer and str(movements[chosen].get("tx_type", "")).startswith("internal"):
            transfers_found += 1
    return FileScore(
        n_expected=len(expected_rows),
        n_extracted=len(movements),
        n_matched=matched,
        n_amount_signed_ok=signed_ok,
        n_transfers_expected=sum(row.is_internal_transfer for row in expected_rows),
        n_transfers_found=transfers_found,
    )


def declared_usage(doc_type: str) -> str:
    """Account usage the person would declare for a document type.

    Args:
        doc_type: document type from the manifest.

    Returns:
        "payments" for credit cards, "in_and_out" otherwise.
    """
    return DECLARED_USAGE_BY_DOC_TYPE.get(doc_type, DECLARED_USAGE_DEFAULT)


def _git_commit() -> str:
    """Short commit of the working tree under test, "unknown" outside git."""
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _build_taxonomy():
    """Italian default taxonomy, as a fresh install has it (no database needed)."""
    from core.categorizer import TaxonomyConfig
    from db.taxonomy_defaults import TAXONOMY_DEFAULTS

    defaults = TAXONOMY_DEFAULTS["it"]
    return TaxonomyConfig(
        expenses={entry["category"]: entry["subcategories"] for entry in defaults["expenses"]},
        income={entry["category"]: entry["subcategories"] for entry in defaults["income"]},
    )


def run_one(manifest_row: dict, scenario: str, run_id: int, config, taxonomy, model_label: str, commit: str) -> ResultRow:
    """Import one file through process_file and score it.

    Args:
        manifest_row: row of manifest.csv for the file.
        scenario: "none" or "declared" (see ResultRow.scenario).
        run_id: 1-based run number.
        config: ProcessingConfig shared by the whole benchmark.
        taxonomy: TaxonomyConfig shared by the whole benchmark.
        model_label: model name written in the output.
        commit: git commit written in the output.

    Returns:
        The scored row; exceptions become an error row, never stop the benchmark.
    """
    from core.orchestrator import process_file

    filename = manifest_row["filename"]
    usage = declared_usage(manifest_row["doc_type"]) if scenario == "declared" else None
    row = ResultRow(
        run_id=run_id, scenario=scenario, account_usage=usage or "", filename=filename,
        doc_type_expected=manifest_row["doc_type"], amount_format=manifest_row["amount_format"],
        rows_expected=int(manifest_row["n_data_rows"]), model=model_label, git_commit=commit, hostname=platform.node(),
    )
    started = time.time()
    try:
        result = process_file(
            (GENERATED_DIR / filename).read_bytes(), filename, config, taxonomy, user_rules=[], account_usage=usage,
        )
    except Exception as exc:  # noqa: BLE001 - a crash is a recorded outcome, not a stop
        row.duration_seconds = round(time.time() - started, 2)
        row.error = f"{type(exc).__name__}: {exc}"[:300]
        return row
    row.duration_seconds = round(time.time() - started, 2)
    schema = result.doc_schema
    if schema is not None:
        row.doc_type_detected = getattr(schema.doc_type, "value", str(schema.doc_type))
        row.confidence_score = round(float(getattr(schema, "confidence_score", 0.0) or 0.0), 3)
    row.doc_type_match = int(row.doc_type_detected == row.doc_type_expected)
    row.needs_schema_review = int(result.needs_schema_review)
    row.sign_needs_confirmation = int(result.sign_needs_confirmation)
    movements = result.transactions or []
    row.rows_extracted = len(movements)
    row.rows_match = int(abs(row.rows_extracted - row.rows_expected) <= ROWS_TOLERANCE)
    if not movements:
        row.error = "; ".join(result.errors)[:300] or "no movements"
        return row
    score = score_file(load_expected_rows(GENERATED_DIR / f"{Path(filename).stem}.expected.csv"), movements)
    row.amount_signed_accuracy = round(score.n_amount_signed_ok / score.n_expected, 4) if score.n_expected else 0.0
    row.amount_abs_accuracy = round(score.n_matched / score.n_expected, 4) if score.n_expected else 0.0
    row.transfer_recall = round(score.n_transfers_found / score.n_transfers_expected, 4) if score.n_transfers_expected else None
    return row


def _done_keys(output_path: Path) -> set[tuple[str, str, str]]:
    """(run_id, scenario, filename) already written, to resume an interrupted benchmark.

    Args:
        output_path: CSV being resumed; missing file means nothing done.
    """
    if not output_path.exists():
        return set()
    with open(output_path, encoding="utf-8") as handle:
        return {(r["run_id"], r["scenario"], r["filename"]) for r in csv.DictReader(handle)}


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Benchmark of file reading through process_file (the app's entry point).")
    parser.add_argument("--model-path", type=str, default=None, help="GGUF file for the llama.cpp backend")
    parser.add_argument("--backend", type=str, default="local_llama_cpp", help="ProcessingConfig.llm_backend (local_llama_cpp, local_ollama, openai, ...)")
    parser.add_argument("--model", type=str, default=None, help="model name for Ollama or hosted backends")
    parser.add_argument("--runs", type=int, default=3, help="repetitions over all files (default 3)")
    parser.add_argument("--files", type=str, default=None, help="glob on file names, e.g. 'CC-1*'")
    parser.add_argument("--scenario", choices=["none", "declared", "both"], default="both", help="account usage scenarios to run")
    parser.add_argument("--resume", type=Path, default=None, help="existing output CSV to complete instead of starting a new one")
    args = parser.parse_args()

    from core.orchestrator import ProcessingConfig

    config = ProcessingConfig(llm_backend=args.backend, force_schema_import=True)
    if args.model_path:
        config.llama_cpp_model_path = str(Path(args.model_path).expanduser())
    if args.model:
        config.ollama_model = config.openai_model = config.compat_model = args.model
    model_label = Path(args.model_path).name if args.model_path else (args.model or args.backend)
    taxonomy = _build_taxonomy()
    commit = _git_commit()

    with open(GENERATED_DIR / "manifest.csv", encoding="utf-8") as handle:
        manifest = [r for r in csv.DictReader(handle) if not args.files or fnmatch.fnmatch(r["filename"], args.files)]
    scenarios = ["none", "declared"] if args.scenario == "both" else [args.scenario]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = args.resume or OUTPUT_DIR / f"{datetime.now():%Y%m%d%H%M%S}_{commit}_{platform.node().split('.')[0]}.csv"
    shutil.copyfile(GENERATED_DIR / "manifest.csv", output_path.with_suffix(".manifest.csv"))
    done = _done_keys(output_path)
    columns = [f.name for f in fields(ResultRow)]
    total = args.runs * len(scenarios) * len(manifest)
    print(f"Output: {output_path}  ({total} imports, {len(done)} already done)", flush=True)

    with open(output_path, "a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        if not done:
            writer.writeheader()
        count = 0
        for run_id in range(1, args.runs + 1):
            for scenario in scenarios:
                for manifest_row in manifest:
                    count += 1
                    if (str(run_id), scenario, manifest_row["filename"]) in done:
                        continue
                    row = run_one(manifest_row, scenario, run_id, config, taxonomy, model_label, commit)
                    writer.writerow(asdict(row))
                    handle.flush()
                    status = row.error[:60] if row.error else f"signed={row.amount_signed_accuracy:.2f} abs={row.amount_abs_accuracy:.2f} dt={row.doc_type_match}"
                    print(f"[{datetime.now():%H:%M:%S}] {count}/{total} run {run_id} {scenario:8} {manifest_row['filename']:18} {row.duration_seconds:6.1f}s {status}", flush=True)
    print(f"Done: {output_path}")


if __name__ == "__main__":
    main()
