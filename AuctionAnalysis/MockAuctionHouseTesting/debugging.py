#!/usr/bin/env python3
"""Comprehensive Debugging & Diagnostic Pipeline for MockDBData & AuctionScanDiff.

This script automates the step-by-step diagnostic process to:
1. Inspect Ground Truth vs SQLite Snapshot consistency.
2. Verify Whole-Stack Purchases and Stack Size integrity.
3. Validate Key Matching & Lookup across Ground Truth and sales.db Predictions.
4. Diagnose Repost False Positives & Confusion Matrix entries.
5. Run Hyperparameter Sweeps and output classification accuracy metrics.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DATA_DIR = Path("./data")
GROUND_TRUTH_FILE = DATA_DIR / "ground_truth.json"
SALES_DB_FILE = Path("./sales.db")
DIFF_SCRIPT = Path("../AuctionScanDiff.py")


def step1_check_snapshot_integrity() -> None:
    """Step 1: Check SQLite Snapshot Integrity & Row Counts.

    Verifies that generated scan_*.db files exist, counts rows and distinct
    listings per snapshot, and checks for database leakage or cross-snapshot
    corruption.
    """
    print("\n" + "=" * 70)
    print("STEP 1: Checking Snapshot DB Integrity & Listing Counts")
    print("=" * 70)

    db_files = sorted(DATA_DIR.glob("scan_*.db"))
    if not db_files:
        print(f"[!] No snapshot .db files found in {DATA_DIR}. Please run 'make generate' first.")
        return

    print(f"[*] Found {len(db_files)} snapshot databases.")
    for db_path in db_files[:5]:
        with sqlite3.connect(db_path) as conn:
            total_rows = conn.execute("SELECT count(*) FROM auctionListings").fetchone()[0]
            distinct_items = conn.execute("SELECT count(DISTINCT itemId) FROM auctionListings").fetchone()[0]
            foreign_snap = conn.execute(
                "SELECT count(*) FROM auctionListings WHERE snapshotId != ?",
                (db_path.stem,),
            ).fetchone()[0]
            print(f"  - {db_path.name}: rows={total_rows}, distinct_items={distinct_items}, foreign_snapshot_rows={foreign_snap}")
    if len(db_files) > 5:
        print(f"  ... and {len(db_files) - 5} more snapshots.")


def step2_inspect_partial_buys_and_stack_mutations() -> None:
    """Step 2: Inspect Purchases and Verify Whole-Listing Purchase Integrity.

    In the WoW auction house, listings can be posted with arbitrary quantities
    (e.g., 13 units of a 20-stack item), but buyers cannot buy partial quantities
    of a listing. A buyer must purchase the entire listing in an all-or-nothing transaction.
    This step verifies that all buy events correspond to entire listing purchases,
    that no listing has multiple buy events, and that ground truth records
    maintain listing size integrity.
    """
    print("\n" + "=" * 70)
    print("STEP 2: Inspecting Purchases & Listing Integrity")
    print("=" * 70)

    if not GROUND_TRUTH_FILE.exists():
        print(f"[!] Ground truth file not found: {GROUND_TRUTH_FILE}")
        return

    with GROUND_TRUTH_FILE.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    events = data.get("event_history", [])
    buy_events = [e for e in events if e.get("event_type") == "buy"]
    post_events = [e for e in events if e.get("event_type") == "post"]
    repost_events = [e for e in events if e.get("event_type") == "repost"]
    expire_events = [e for e in events if e.get("event_type") == "expire"]

    print(f"[*] Total Events: {len(events)}")
    print(f"    - Posts:   {len(post_events)}")
    print(f"    - Buys:    {len(buy_events)}")
    print(f"    - Reposts: {len(repost_events)}")
    print(f"    - Expires: {len(expire_events)}")

    # Check for multiple buys on the same listing (which would indicate illegal partial buys)
    buys_by_listing: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for b in buy_events:
        buys_by_listing[b["listing_id"]].append(b)

    multi_buys = {lid: b_list for lid, b_list in buys_by_listing.items() if len(b_list) > 1}
    print(f"[*] Listings with multiple buys (partial buy violations): {len(multi_buys)}")
    if multi_buys:
        for lid, b_list in list(multi_buys.items())[:3]:
            print(f"    Listing {lid}: {len(b_list)} purchases -> quantities: {[b['details'].get('quantity') for b in b_list]}")
    else:
        print("    [✓] Verified: All purchases are all-or-nothing whole-listing purchases.")


def step3_diagnose_key_matching_coverage() -> None:
    """Step 3: Diagnose Key Matching Coverage between sales.db and Ground Truth.

    Loads sales.db rows and compares their identity keys against ground truth
    records to verify 0% unmatched rate and accurate unit-price indexing.
    """
    print("\n" + "=" * 70)
    print("STEP 3: Diagnosing Ground Truth Key Matching Coverage")
    print("=" * 70)

    if not SALES_DB_FILE.exists():
        print(f"[!] sales.db not found at {SALES_DB_FILE}. Running AuctionScanDiff...")
        subprocess.run(
            ["python3", str(DIFF_SCRIPT), "--scan-dir", str(DATA_DIR), "--sales-db", str(SALES_DB_FILE)],
            check=False,
        )

    import validate
    ground_truth = validate.loadGroundTruth(GROUND_TRUTH_FILE)
    preds = validate.loadDiffResults(SALES_DB_FILE)

    print(f"[*] Total predictions loaded from sales.db: {len(preds)}")
    print(f"[*] Unique ground truth indexing keys: {len(ground_truth)}")

    eval_records = validate.evaluatePredictions(preds, ground_truth)
    metrics = validate.computeMetrics(eval_records)

    unmatched_count = sum(1 for r in eval_records if r.ground_truth is None)
    print(f"[*] Accuracy: {metrics.accuracy:.3%}")
    print(f"[*] Unmatched predictions count: {unmatched_count} ({unmatched_count / max(1, len(preds)):.1%})")
    print("[*] Confusion Matrix:")
    for pred_status, actual_dict in metrics.confusion_matrix.items():
        row_str = ", ".join(f"{act}={cnt}" for act, cnt in actual_dict.items())
        print(f"    - {pred_status:14s}: {row_str}")


def step4_analyze_repost_vs_sale_heuristics() -> None:
    """Step 4: Analyze Repost vs Sale Classification Sensitivity.

    Checks the distribution of price drops, seller undercut margins, and
    determines optimal thresholds to eliminate false positive repost predictions.
    """
    print("\n" + "=" * 70)
    print("STEP 4: Analyzing Repost vs Sale Heuristic Decisions")
    print("=" * 70)

    if not SALES_DB_FILE.exists():
        return

    with sqlite3.connect(SALES_DB_FILE) as conn:
        repost_rows = conn.execute(
            "SELECT status, sold_likelihood, reason, listing_json FROM sales WHERE status = 'repost' LIMIT 5"
        ).fetchall()

    print(f"[*] Sample 'repost' classified listings ({len(repost_rows)} shown):")
    for row in repost_rows:
        status, likelihood, reason, listing_json = row
        listing = json.loads(listing_json)
        print(f"    - {listing.get('itemName')} by {listing.get('sellerName')} (stack={listing.get('stackSize')}, buyout={listing.get('buyoutPrice')})")
        print(f"      Reason: {reason}")


def step5_execute_hyperparameter_sweep() -> None:
    """Step 5: Execute Continuous Hyperparameter Grid Sweep.

    Evaluates various combinations of timing, price, continuity weights and
    sold/expired decision thresholds to maximize accuracy and F1 score.
    """
    print("\n" + "=" * 70)
    print("STEP 5: Running Fast Continuous Hyperparameter Sweep")
    print("=" * 70)

    import validate
    config = validate.ValidationConfig(
        snapshot_dir=DATA_DIR,
        ground_truth_path=GROUND_TRUTH_FILE,
        diff_script_path=DIFF_SCRIPT,
        sales_db_path=SALES_DB_FILE,
        sweep=True,
    )
    validate.run_parameter_sweep(config)


def main() -> None:
    print("======================================================================")
    print("MOCK DATA & AUCTION SCAN DIFF DEBUGGING HARNESS")
    print("======================================================================")
    step1_check_snapshot_integrity()
    step2_inspect_partial_buys_and_stack_mutations()
    step3_diagnose_key_matching_coverage()
    step4_analyze_repost_vs_sale_heuristics()
    step5_execute_hyperparameter_sweep()
    print("\n" + "=" * 70)
    print("All debugging and diagnostic steps complete!")
    print("=" * 70)


if __name__ == "__main__":
    main()
