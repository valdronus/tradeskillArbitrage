#!/usr/bin/env python3
"""Synthetic Auctioneer Validation Harness.

All development and execution should be done through the MockDBData Makefile.
Use `make generate` to build synthetic data and `make validate` to evaluate it.
`make all` generates then validates.

This module provides the validation architecture for testing the accuracy of
AuctionScanDiff.py (and related inferred-sales algorithms) against synthetic
ground-truth event logs produced by generate.py.

It executes the diff pipeline across sequential and skipped snapshot pairs,
matches predictions against known transaction histories, computes precision/
recall/F1 metrics, isolates failure cases (e.g. missed continuations, false
expirations), and generates diagnostic validation reports.

==============================================================================
OVERARCHING FLOW / CALL HIERARCHY
==============================================================================

CLI Entry Point:
  main()
    │
    ├── parse_args()                         [Parse evaluation flags and file paths]
    │
    └── run_validation(config)               [Coordinate complete validation run]
          │
          ├── discoverSnapshots()            [Locate ordered scan_XX.db files]
          ├── loadGroundTruth()              [Parse known event history & true outcomes]
          │
          ├── For each snapshot pair (e.g., scan_N.db -> scan_N+1.db, or scan_N -> scan_N+2):
          │     │
          │     ├── runAuctionScanDiff()     [Invoke AuctionScanDiff.py -> sales.db]
          │     └── loadDiffResults()        [Read inferred rows and likelihood scores]
          │
          ├── evaluatePredictions()          [Compare predictions against ground truth]
          │     └── matchPredictionToGroundTruth() [Match diff row to listing lifecycle]
          │
          ├── computeMetrics()               [Calculate Precision, Recall, F1, Accuracy]
          │
          ├── extractFailureCases()          [Isolate worst misclassifications & anomalies]
          │
          └── generateValidationReport()     [Render markdown / text summary report]

==============================================================================
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ============================================================================
# Domain Models & Evaluation Records
# ============================================================================


@dataclass
class ValidationConfig:
    """Configuration options for the validation harness."""

    snapshot_dir: Path = Path("./data")
    ground_truth_path: Path = Path("./data/ground_truth.json")
    diff_script_path: Path = Path("../AuctionScanDiff.py")
    sales_db_path: Path = Path("./sales.db")
    test_skipped_scans: bool = True
    output_report_path: Path | None = Path("./validation_report.md")
    tolerance_hours: float = 0.5
    timing_weight: float | None = None
    price_weight: float | None = None
    continuity_weight: float | None = None
    sold_threshold: float | None = None
    expired_threshold: float | None = None
    repost_threshold: float | None = None
    sweep: bool = False


@dataclass
class GroundTruthRecord:
    """True lifecycle and final outcome of a synthetic listing."""

    listing_id: int
    item_id: int
    item_name: str
    seller_name: str
    unit_price: float
    stack_size: int
    post_time: float
    actual_outcome: str  # 'sold', 'expired', 'reposted', 'active', 'cancelled'
    event_history: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DiffPredictionRecord:
    """Classification produced by AuctionScanDiff for a disappeared listing."""

    before_snapshot: str
    after_snapshot: str
    item_id: int | None
    item_name: str | None
    seller_name: str | None
    stack_size: int | None
    buyout_price: float | None
    predicted_status: str  # 'likely_sold', 'likely_expired', 'missing', 'repost'
    sold_likelihood: float | None
    reason: str
    elapsed_hours: float | None = None
    peer_count: int = 0
    cheaper_peer_count: int = 0


@dataclass
class EvaluationRecord:
    """Evaluation pairing between an algorithm prediction and ground truth."""

    prediction: DiffPredictionRecord
    ground_truth: GroundTruthRecord | None
    is_correct: bool
    error_category: str  # 'none', 'missed_continuation', 'premature_expiration',
    # 'repost_false_positive', 'repost_false_negative',
    # 'sold_vs_expired_confusion', 'unmatched'
    details: str = ""


@dataclass
class ValidationMetrics:
    """Statistical summary of validation performance across categories."""

    total_evaluated: int = 0
    correct_count: int = 0
    accuracy: float = 0.0
    precision_by_status: dict[str, float] = field(default_factory=dict)
    recall_by_status: dict[str, float] = field(default_factory=dict)
    f1_by_status: dict[str, float] = field(default_factory=dict)
    confusion_matrix: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass
class FailureCase:
    """Detailed diagnosis for a misclassified listing."""

    listing_id: int | None
    item_name: str
    seller_name: str
    stack_size: int
    unit_price: float
    predicted_status: str
    actual_outcome: str
    error_category: str
    reason: str
    timeline_context: str


# ============================================================================
# Abstract Architecture Functions (Stubs)
# ============================================================================


def discoverSnapshots(snapshot_dir: str | Path) -> list[Path]:
    """Locate and return sorted paths of all synthetic snapshot databases.

    Args:
        snapshot_dir: Directory containing generated `.db` files.

    Returns:
        List of Path objects sorted by snapshot index / timestamp.

    Raises:
        NotImplementedError: Stubbed for architectural design.
    """
    path = Path(snapshot_dir)
    if not path.exists() or not path.is_dir():
        return []
    snapshots = sorted(path.glob("*.db"))
    return snapshots


def loadGroundTruthEvents(ground_truth_path: str | Path) -> list[dict[str, Any]]:
    """Load raw ground truth event history from the synthetic ground truth JSON file.

    Args:
        ground_truth_path: Path to the serialized ground truth file.

    Returns:
        List of event dictionaries in chronological order.
    """
    path = Path(ground_truth_path)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (ValueError, OSError):
        return []
    return data.get("event_history", [])


def loadGroundTruth(ground_truth_path: str | Path) -> dict[str, list[GroundTruthRecord]]:
    """Parse the ground-truth JSON/DB file containing true synthetic events.

    Args:
        ground_truth_path: Path to the serialized ground truth file.

    Returns:
        Mapping of listing key to list of GroundTruthRecord objects sorted chronologically.
    """
    events = loadGroundTruthEvents(ground_truth_path)
    records: dict[str, list[GroundTruthRecord]] = defaultdict(list)
    listing_state: dict[int, GroundTruthRecord] = {}
    for event in events:
        listing_id = event.get("listing_id")
        if listing_id is None:
            continue
        record = listing_state.get(listing_id)
        if event.get("event_type") == "post" and record is None:
            details = event.get("details", {})
            stack_size = int(details.get("stack_size", 1))
            unit_price = float(
                details.get("unit_price")
                or (
                    float(details.get("buyout_price", 0)) / stack_size
                    if stack_size
                    else details.get("buyout_price", 0)
                )
            )
            listing_state[listing_id] = GroundTruthRecord(
                listing_id=listing_id,
                item_id=event.get("item_id", 0),
                item_name=details.get("item_name", ""),
                seller_name=details.get("seller_name", ""),
                unit_price=unit_price,
                stack_size=stack_size,
                post_time=event.get("timestamp", 0.0),
                actual_outcome="active",
                event_history=[event],
            )
        elif record is not None:
            record.event_history.append(event)
            if event.get("event_type") == "buy":
                record.actual_outcome = "sold"
            elif event.get("event_type") == "expire":
                record.actual_outcome = "expired"
            elif event.get("event_type") == "repost":
                record.actual_outcome = "reposted"
    for record in listing_state.values():
        key = f"{record.item_id}_{record.seller_name}_{record.stack_size}_{int(round(record.unit_price))}"
        records[key].append(record)

    # Sort each key list by post_time
    for key in records:
        records[key].sort(key=lambda r: r.post_time)

    return records


def runAuctionScanDiff(
    diff_script_path: str | Path,
    snapshot_dir: str | Path,
    sales_db: str | Path,
    timing_weight: float | None = None,
    price_weight: float | None = None,
    continuity_weight: float | None = None,
    sold_threshold: float | None = None,
    expired_threshold: float | None = None,
    repost_threshold: float | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Execute AuctionScanDiff.py on a directory of snapshot DBs to produce sales.db.

    Args:
        diff_script_path: Path to the AuctionScanDiff.py script.
        snapshot_dir: Directory containing .db snapshot files.
        sales_db: Target path for the output sales database.
        timing_weight: Optional timing weight override.
        price_weight: Optional price weight override.
        continuity_weight: Optional continuity weight override.
        sold_threshold: Optional sold threshold override.
        expired_threshold: Optional expired threshold override.
        repost_threshold: Optional repost threshold override.

    Returns:
        CompletedProcess object from subprocess execution.
    """
    script = Path(diff_script_path)
    if not script.exists():
        return None
    cmd = [
        "python3",
        str(script),
        "--scan-dir",
        str(snapshot_dir),
        "--sales-db",
        str(sales_db),
    ]
    if timing_weight is not None:
        cmd.extend(["--timing-weight", str(timing_weight)])
    if price_weight is not None:
        cmd.extend(["--price-weight", str(price_weight)])
    if continuity_weight is not None:
        cmd.extend(["--continuity-weight", str(continuity_weight)])
    if sold_threshold is not None:
        cmd.extend(["--sold-threshold", str(sold_threshold)])
    if expired_threshold is not None:
        cmd.extend(["--expired-threshold", str(expired_threshold)])
    if repost_threshold is not None:
        cmd.extend(["--repost-threshold", str(repost_threshold)])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        return result
    except OSError:
        return None


def loadDiffResults(sales_db_path: str | Path) -> list[DiffPredictionRecord]:
    """Query and parse inferred predictions from the generated sales.db SQLite file.

    Args:
        sales_db_path: Path to the sales.db generated by AuctionScanDiff.

    Returns:
        List of DiffPredictionRecord instances.

    Raises:
        NotImplementedError: Stubbed for architectural design.
    """
    path = Path(sales_db_path)
    if not path.exists():
        return []
    records: list[DiffPredictionRecord] = []
    try:
        conn = sqlite3.connect(str(path))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT before_snapshot, after_snapshot, status, sold_likelihood, reason, listing_json "
            "FROM sales"
        )
        for row in cursor.fetchall():
            before_snapshot, after_snapshot, status, sold_likelihood, reason, listing_json = row
            item_id = None
            item_name = None
            seller_name = None
            stack_size = None
            buyout_price = None
            try:
                listing = json.loads(listing_json or "{}")
                item_id = listing.get("itemId")
                item_name = listing.get("itemName")
                seller_name = listing.get("sellerName")
                stack_size = listing.get("stackSize")
                buyout_price = listing.get("buyoutPrice")
            except (ValueError, TypeError):
                pass
            records.append(
                DiffPredictionRecord(
                    before_snapshot=before_snapshot,
                    after_snapshot=after_snapshot,
                    item_id=item_id,
                    item_name=item_name,
                    seller_name=seller_name,
                    stack_size=stack_size,
                    buyout_price=buyout_price,
                    predicted_status=status,
                    sold_likelihood=sold_likelihood,
                    reason=reason,
                )
            )
    except sqlite3.Error:
        return []
    finally:
        if "conn" in locals():
            conn.close()
    return records


def matchPredictionToGroundTruth(
    prediction: DiffPredictionRecord,
    ground_truth: dict[str, list[GroundTruthRecord]],
) -> GroundTruthRecord | None:
    """Match an inferred diff prediction back to its corresponding ground-truth listing.

    Matches by item_id, seller, stack size, and unit price, selecting the most
    chronologically relevant record.

    Args:
        prediction: Inferred DiffPredictionRecord from sales.db.
        ground_truth: Lookup map of listing key to GroundTruthRecord list.

    Returns:
        Matched GroundTruthRecord if found, else None.
    """
    if (
        prediction.item_id is None
        or prediction.seller_name is None
        or prediction.stack_size is None
        or prediction.buyout_price is None
    ):
        return None

    unit_price = prediction.buyout_price / prediction.stack_size if prediction.stack_size else 0.0
    key = f"{prediction.item_id}_{prediction.seller_name}_{prediction.stack_size}_{int(round(unit_price))}"
    candidates = ground_truth.get(key)
    if not candidates:
        return None

    # Return the candidate that matches the non-active lifecycle outcome if available, or first
    for cand in candidates:
        if cand.actual_outcome != "active":
            return cand
    return candidates[0]


def evaluatePredictions(
    predictions: list[DiffPredictionRecord],
    ground_truth: dict[str, list[GroundTruthRecord]],
    intermediate_snapshots: list[Path] | None = None,
) -> list[EvaluationRecord]:
    """Evaluate diff predictions against known ground truth outcomes.

    Checks:
      - Precision/Recall for 'likely_sold' vs true purchases
      - Precision/Recall for 'likely_expired' vs true expirations
      - Precision/Recall for 'repost' vs true lower-price repostings
      - Continuation check: verify if listings survived in intermediate snapshots

    Args:
        predictions: Diff results produced by AuctionScanDiff.
        ground_truth: Map of true listing events.
        intermediate_snapshots: Optional intermediate snapshot DBs for skipped-scan verification.

    Returns:
        List of classified EvaluationRecord objects.
    """
    records: list[EvaluationRecord] = []
    for prediction in predictions:
        truth = matchPredictionToGroundTruth(prediction, ground_truth)
        is_correct = False
        error_category = "unmatched"
        if truth is not None:
            if (
                (prediction.predicted_status == "likely_sold" and truth.actual_outcome == "sold")
                or (
                    prediction.predicted_status == "likely_expired"
                    and truth.actual_outcome == "expired"
                )
                or (prediction.predicted_status == "repost" and truth.actual_outcome == "reposted")
                or (
                    prediction.predicted_status == "missing"
                    and truth.actual_outcome in {"active", "cancelled"}
                )
            ):
                is_correct = True
                error_category = "none"
            else:
                error_category = (
                    f"predicted_{prediction.predicted_status}_actual_{truth.actual_outcome}"
                )
        records.append(
            EvaluationRecord(
                prediction=prediction,
                ground_truth=truth,
                is_correct=is_correct,
                error_category=error_category,
                details="",
            )
        )
    return records


def computeMetrics(records: list[EvaluationRecord]) -> ValidationMetrics:
    """Calculate aggregate classification metrics from evaluation records.

    Computes overall accuracy, precision, recall, F1 score per status class,
    and a full confusion matrix.

    Args:
        records: List of evaluated prediction records.

    Returns:
        ValidationMetrics object summarizing performance.

    Raises:
        NotImplementedError: Stubbed for architectural design.
    """
    metrics = ValidationMetrics()
    metrics.total_evaluated = len(records)
    metrics.correct_count = sum(1 for record in records if record.is_correct)
    metrics.accuracy = (
        metrics.correct_count / metrics.total_evaluated if metrics.total_evaluated else 0.0
    )
    status_totals: dict[str, int] = defaultdict(int)
    status_correct: dict[str, int] = defaultdict(int)
    truth_totals: dict[str, int] = defaultdict(int)
    for record in records:
        status = record.prediction.predicted_status
        status_totals[status] += 1
        if record.is_correct:
            status_correct[status] += 1
        if record.ground_truth is not None:
            truth_totals[record.ground_truth.actual_outcome] += 1
    for status, total in status_totals.items():
        precision = status_correct[status] / total if total else 0.0
        metrics.precision_by_status[status] = precision
    for outcome, total in truth_totals.items():
        correct = sum(
            1
            for record in records
            if record.ground_truth is not None
            and record.ground_truth.actual_outcome == outcome
            and record.is_correct
        )
        metrics.recall_by_status[outcome] = correct / total if total else 0.0
        precision = metrics.precision_by_status.get(outcome, 0.0)
        recall = metrics.recall_by_status[outcome]
        metrics.f1_by_status[outcome] = (
            (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        )
    for record in records:
        predicted = record.prediction.predicted_status
        actual = record.ground_truth.actual_outcome if record.ground_truth else "unmatched"
        metrics.confusion_matrix.setdefault(predicted, {}).setdefault(actual, 0)
        metrics.confusion_matrix[predicted][actual] += 1
    return metrics


def extractFailureCases(
    records: list[EvaluationRecord],
    max_cases: int = 10,
) -> list[FailureCase]:
    """Extract and prioritize key misclassification failure cases.

    Isolates concrete failure modes such as:
      - Predicted likely_sold, but item persisted in intermediate scan (missed continuation)
      - Predicted likely_expired, but item was actually purchased
      - Repost false positives or false negatives

    Args:
        records: List of evaluated records.
        max_cases: Maximum number of failure examples to return.

    Returns:
        List of structured FailureCase objects.

    Raises:
        NotImplementedError: Stubbed for architectural design.
    """
    failures: list[FailureCase] = []
    for record in records:
        if record.is_correct:
            continue
        truth = record.ground_truth
        failures.append(
            FailureCase(
                listing_id=truth.listing_id if truth else None,
                item_name=truth.item_name if truth else (record.prediction.item_name or "unknown"),
                seller_name=(
                    truth.seller_name if truth else (record.prediction.seller_name or "unknown")
                ),
                stack_size=truth.stack_size if truth else (record.prediction.stack_size or 0),
                unit_price=truth.unit_price if truth else (record.prediction.buyout_price or 0.0),
                predicted_status=record.prediction.predicted_status,
                actual_outcome=truth.actual_outcome if truth else "unmatched",
                error_category=record.error_category,
                reason=record.prediction.reason,
                timeline_context="",
            )
        )
        if len(failures) >= max_cases:
            break
    return failures


def summarizeSnapshots(snapshot_paths: list[Path]) -> list[dict[str, Any]]:
    """Collect snapshot-level statistics from Auctioneer snapshot DB files."""
    summaries: list[dict[str, Any]] = []
    for path in snapshot_paths:
        if not path.exists():
            continue
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            try:
                rows = cursor.execute("SELECT * FROM auctionListings").fetchall()
            except sqlite3.OperationalError:
                continue
            total_rows = len(rows)
            item_counts: Counter = Counter()
            total_stack = 0
            row_hashes = set()
            for row in rows:
                item_name = row["itemName"] if "itemName" in row.keys() else ""
                stack_size = row["stackSize"] if "stackSize" in row.keys() else 0
                total_stack += int(stack_size)
                item_counts[item_name] += 1
                row_hashes.add(tuple((key, row[key]) for key in row.keys() if key != "id"))
            summaries.append(
                {
                    "snapshot": path.name,
                    "rows": total_rows,
                    "unique_rows": len(row_hashes),
                    "total_stack": total_stack,
                    "unique_item_names": len(item_counts),
                    "top_items": item_counts.most_common(3),
                }
            )
    return summaries


def summarizeGroundTruth(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Create summary statistics from synthetic ground truth events."""
    event_counter: Counter = Counter()
    item_counter: Counter = Counter()
    seller_counter: Counter = Counter()
    buy_quantities = 0
    snapshot_count = sum(1 for event in events if event.get("event_type") == "snapshot")
    for event in events:
        event_type = event.get("event_type")
        event_counter[event_type] += 1
        if event_type == "post":
            item_counter[event.get("item_id")] += 1
            seller_counter[event.get("details", {}).get("seller_name", "")] += 1
        if event_type == "buy":
            buy_quantities += int(event.get("details", {}).get("quantity", 0))
    return {
        "event_count": len(events),
        "snapshot_count": snapshot_count,
        "post_count": event_counter["post"],
        "buy_count": event_counter["buy"],
        "expire_count": event_counter["expire"],
        "repost_count": event_counter["repost"],
        "cancel_count": event_counter["cancel"],
        "unique_items": len(item_counter),
        "unique_sellers": len(seller_counter),
        "total_bought_quantity": buy_quantities,
        "top_items": item_counter.most_common(5),
    }


def generateValidationReport(
    metrics: ValidationMetrics,
    failure_cases: list[FailureCase],
    output_path: str | Path | None = None,
    snapshot_paths: list[Path] | None = None,
    ground_truth_events: list[dict[str, Any]] | None = None,
) -> str:
    """Format evaluation metrics and failure cases into a comprehensive report.

    Args:
        metrics: Computed validation metrics.
        failure_cases: List of diagnosed failure cases.
        output_path: Optional file path to save the report (e.g., Markdown file).
        snapshot_paths: Optional list of snapshot DB paths to summarize.
        ground_truth_events: Optional raw event history from the synthetic generator.

    Returns:
        Formatted string of the validation report.
    """
    lines: list[str] = [
        "Validation Report",
        "===============",
        "",
    ]
    if ground_truth_events is not None:
        gt_summary = summarizeGroundTruth(ground_truth_events)
        lines += [
            "Ground Truth Summary",
            "---------------------",
            f"- Total events: {gt_summary['event_count']}",
            f"- Snapshot events logged: {gt_summary['snapshot_count']}",
            f"- Seeded item postings: {gt_summary['post_count']}",
            f"- Buy events: {gt_summary['buy_count']}",
            f"- Expire events: {gt_summary['expire_count']}",
            f"- Repost events: {gt_summary['repost_count']}",
            f"- Unique seeded items: {gt_summary['unique_items']}",
            f"- Unique sellers: {gt_summary['unique_sellers']}",
            f"- Total bought quantity: {gt_summary['total_bought_quantity']}",
            "- Top seeded items by post count:",
        ]
        for item_id, count in gt_summary["top_items"]:
            lines.append(f"  - Item {item_id}: {count} posts")
        lines.append("")
    if snapshot_paths is not None:
        snapshot_summaries = summarizeSnapshots(snapshot_paths)
        lines += [
            "Snapshot Summary",
            "----------------",
            f"- Total snapshots: {len(snapshot_summaries)}",
        ]
        for summary in snapshot_summaries:
            lines.append(
                f"- {summary['snapshot']}: rows={summary['rows']}, unique_rows={summary['unique_rows']}, total_stack={summary['total_stack']}, unique_items={summary['unique_item_names']}"
            )
            if summary["top_items"]:
                top_desc = ", ".join(f"{name}({count})" for name, count in summary["top_items"])
                lines.append(f"  top items: {top_desc}")
        lines.append("")
    lines += [
        "Evaluation Summary",
        "------------------",
        f"Total evaluated: {metrics.total_evaluated}",
        f"Accuracy: {metrics.accuracy:.3f}",
        "",
        "Precision by status:",
    ]
    for status, value in metrics.precision_by_status.items():
        lines.append(f"- {status}: {value:.3f}")
    lines.append("")
    lines.append("Recall by status:")
    for status, value in metrics.recall_by_status.items():
        lines.append(f"- {status}: {value:.3f}")
    lines.append("")
    lines.append("Confusion matrix:")
    for predicted, actuals in metrics.confusion_matrix.items():
        rows = ", ".join(f"{actual}={count}" for actual, count in actuals.items())
        lines.append(f"- {predicted}: {rows}")
    lines.append("")
    lines.append("Failure cases:")
    if not failure_cases:
        lines.append("- None")
    else:
        for failure in failure_cases[:20]:
            lines.append(
                (
                    "- {item_name} by {seller_name}: predicted={predicted}, "
                    "actual={actual}, error={error}, quantity={quantity}, "
                    "price={price}, reason={reason}"
                ).format(
                    item_name=failure.item_name or "unknown",
                    seller_name=failure.seller_name or "unknown",
                    predicted=failure.predicted_status,
                    actual=failure.actual_outcome,
                    error=failure.error_category,
                    quantity=failure.stack_size,
                    price=failure.unit_price,
                    reason=failure.reason,
                )
            )
    report = "\n".join(lines)
    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
    return report


def run_parameter_sweep(config: ValidationConfig) -> int:
    """Run a grid search over continuous hyperparameters to find optimal settings."""
    snapshots = discoverSnapshots(config.snapshot_dir)
    ground_truth = loadGroundTruth(config.ground_truth_path)

    timing_weights = [0.30, 0.45, 0.60]
    price_weights = [0.20, 0.35, 0.50]
    continuity_weights = [0.10, 0.20]
    sold_thresholds = [0.45, 0.50, 0.55]
    expired_thresholds = [0.40, 0.45]

    print("=================================================================")
    print("Beginning Continuous Hyperparameter Sweep Grid Search")
    print("=================================================================")
    best_acc = 0.0
    best_params = {}
    best_metrics = None

    for tw in timing_weights:
        for pw in price_weights:
            for cw in continuity_weights:
                for st in sold_thresholds:
                    for et in expired_thresholds:
                        if et >= st:
                            continue
                        runAuctionScanDiff(
                            config.diff_script_path,
                            config.snapshot_dir,
                            config.sales_db_path,
                            timing_weight=tw,
                            price_weight=pw,
                            continuity_weight=cw,
                            sold_threshold=st,
                            expired_threshold=et,
                        )
                        preds = loadDiffResults(config.sales_db_path)
                        evals = evaluatePredictions(preds, ground_truth)
                        metrics = computeMetrics(evals)

                        if metrics.accuracy > best_acc:
                            best_acc = metrics.accuracy
                            best_params = {
                                "timing_weight": tw,
                                "price_weight": pw,
                                "continuity_weight": cw,
                                "sold_threshold": st,
                                "expired_threshold": et,
                            }
                            best_metrics = metrics
                            print(
                                f"[+] New Best Accuracy: {best_acc:.3f} | "
                                f"tw={tw}, pw={pw}, cw={cw}, sold_thresh={st}, exp_thresh={et} | "
                                f"prec(sold)={metrics.precision_by_status.get('likely_sold', 0.0):.2f}, "
                                f"rec(sold)={metrics.recall_by_status.get('sold', 0.0):.2f}"
                            )

    print("=================================================================")
    print(f"Sweep Completed. Best Accuracy: {best_acc:.3f}")
    print(f"Best Parameters: {best_params}")
    print("=================================================================")
    return 0


def run_validation(config: ValidationConfig) -> int:
    """Execute the end-to-end validation harness across all snapshot series.

    Args:
        config: Validation configuration parameters.

    Returns:
        Exit code (0 on success).
    """
    if config.sweep:
        return run_parameter_sweep(config)

    snapshots = discoverSnapshots(config.snapshot_dir)
    ground_truth = loadGroundTruth(config.ground_truth_path)
    ground_truth_events = loadGroundTruthEvents(config.ground_truth_path)

    all_predictions: list[DiffPredictionRecord] = []
    if snapshots:
        runAuctionScanDiff(
            config.diff_script_path,
            config.snapshot_dir,
            config.sales_db_path,
            timing_weight=config.timing_weight,
            price_weight=config.price_weight,
            continuity_weight=config.continuity_weight,
            sold_threshold=config.sold_threshold,
            expired_threshold=config.expired_threshold,
            repost_threshold=config.repost_threshold,
        )
        all_predictions.extend(loadDiffResults(config.sales_db_path))

    evaluation_records = evaluatePredictions(all_predictions, ground_truth)
    metrics = computeMetrics(evaluation_records)
    failures = extractFailureCases(evaluation_records)
    report = generateValidationReport(
        metrics,
        failures,
        config.output_report_path,
        snapshot_paths=snapshots,
        ground_truth_events=ground_truth_events,
    )
    print(report)
    return 0


# ============================================================================
# CLI & Entry Point
# ============================================================================


def parse_args(args: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command line options for the validation harness.

    Args:
        args: Optional CLI argument list.

    Returns:
        Parsed argparse.Namespace.
    """
    parser = argparse.ArgumentParser(
        description="Validate AuctionScanDiff inference accuracy against synthetic mock ground truth.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=Path("./data"),
        help="Directory containing synthetic snapshot .db files.",
    )
    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=Path("./data/ground_truth.json"),
        dest="ground_truth_path",
        help="Path to ground truth event log JSON file.",
    )
    parser.add_argument(
        "--diff-script",
        type=Path,
        default=Path("../AuctionScanDiff.py"),
        dest="diff_script_path",
        help="Path to AuctionScanDiff.py script.",
    )
    parser.add_argument(
        "--sales-db",
        type=Path,
        default=Path("./sales.db"),
        dest="sales_db_path",
        help="Path for temporary sales.db database produced by AuctionScanDiff.",
    )
    parser.add_argument(
        "--test-skipped-scans",
        action="store_true",
        default=True,
        help="Perform skipped-scan verification (e.g. diff scan 1 -> 3 and verify against intermediate scan 2).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("./validation_report.md"),
        dest="output_report_path",
        help="Path to write the markdown validation report.",
    )
    parser.add_argument("--timing-weight", type=float, default=None)
    parser.add_argument("--price-weight", type=float, default=None)
    parser.add_argument("--continuity-weight", type=float, default=None)
    parser.add_argument("--sold-threshold", type=float, default=None)
    parser.add_argument("--expired-threshold", type=float, default=None)
    parser.add_argument("--repost-threshold", type=float, default=None)
    parser.add_argument("--sweep", action="store_true", help="Run hyperparameter sweep grid search")
    return parser.parse_args(args)


def main(args: Sequence[str] | None = None) -> int:
    """Main CLI entry point invoking the stubbed architecture functions.

    Args:
        args: Optional CLI argument list.

    Returns:
        Exit code.
    """
    parsed = parse_args(args)
    config = ValidationConfig(
        snapshot_dir=parsed.snapshot_dir,
        ground_truth_path=parsed.ground_truth_path,
        diff_script_path=parsed.diff_script_path,
        sales_db_path=parsed.sales_db_path,
        test_skipped_scans=parsed.test_skipped_scans,
        output_report_path=parsed.output_report_path,
        timing_weight=parsed.timing_weight,
        price_weight=parsed.price_weight,
        continuity_weight=parsed.continuity_weight,
        sold_threshold=parsed.sold_threshold,
        expired_threshold=parsed.expired_threshold,
        repost_threshold=parsed.repost_threshold,
        sweep=parsed.sweep,
    )

    print(f"[*] Validating snapshots in: {config.snapshot_dir}")
    print(f"[*] Using ground truth: {config.ground_truth_path}")

    # Demonstrate architectural call graph (unimplemented stubs raise NotImplementedError)
    try:
        run_validation(config)
    except NotImplementedError as exc:
        print(f"[!] Architecture stub encountered: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
